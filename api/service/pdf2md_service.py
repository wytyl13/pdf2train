#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/16 16:59
@Author  : weiyutao
@File    : pdf2md_service.py
"""



import os
import shutil
import uuid
import logging
import asyncio
from datetime import datetime
import requests
from typing import Tuple
from functools import partial
from PyPDF2 import PdfReader, PdfWriter
import io
import zipfile
import re

from api.service.minio_service import MinioService  # 导入之前定义好的 MinioService
from api.service.pdf_document_service import PdfDocumentService
from api.service.pipeline_task_service import PipelineTaskService
from api.table.base.pdf_document import DocStatus
from tool.markdown_cleaner import MarkdownCleaner
from api.service.llm_config_service import LLMConfigService

# 引入 Model 和 Enum
from api.table.base.pipeline_task import (
    TaskType, TaskLifecycle, ExtractStatus, ExtractTaskResult
)



class Pdf2MdService:
    """
    PDF 转 Markdown 的核心业务逻辑
    负责：下载 -> 拆分 -> 调用 MinerU API -> 合并 -> 上传
    """
    def __init__(
        self, 
        minio_service: MinioService, 
        pdf_document_service: PdfDocumentService,
        task_service: PipelineTaskService,
        mineru_api_url: str,
        llm_config_service: LLMConfigService,
        work_dir: str = "/tmp/pdf2md_worker"
    ):
        self.minio_service = minio_service  # 注入 MinioService
        self.pdf_document_service = pdf_document_service
        self.work_dir = work_dir
        self.task_service = task_service
        self.api_url = mineru_api_url
        self.llm_config_service = llm_config_service
        self.logger = logging.getLogger("Pdf2MdService")
        os.makedirs(self.work_dir, exist_ok=True)

    async def _update_db_status(self, doc_id: int, status: DocStatus, progress: int, message: str = None, extra_update: dict = None):
        """
        实时更新数据库中的进度和状态
        """
        try:
            update_data = {
                "status": status.value,
                "progress": progress
            }
            if message:
                update_data["process_error"] = message  # 如果是错误消息，存入 error 字段
            
            if extra_update:
                update_data.update(extra_update)

            self.logger.info(f"[Task {doc_id}] Status: {status.name}, Progress: {progress}%")
            
            # 调用 DB Service 更新
            await self.pdf_document_service.update_document(doc_id, update_data)
            
        except Exception as e:
            self.logger.error(f"更新数据库状态失败: {e}")

    async def _run_async(self, func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    # --- 步骤 1: 拆分 PDF ---
    def _split_pdf(self, input_path, chunk_size):
        try:
            reader = PdfReader(input_path)
            total_pages = len(reader.pages)
            if total_pages <= chunk_size:
                return [input_path]

            self.logger.info(f"PDF共 {total_pages} 页，正在按每 {chunk_size} 页拆分...")
            base_name = os.path.splitext(input_path)[0]
            chunks = []

            for i in range(0, total_pages, chunk_size):
                writer = PdfWriter()
                end = min(i + chunk_size, total_pages)
                for page_num in range(i, end):
                    writer.add_page(reader.pages[page_num])
                
                chunk_name = f"{base_name}_part_{i}.pdf"
                with open(chunk_name, "wb") as f:
                    writer.write(f)
                chunks.append(chunk_name)
            return chunks
        except Exception as e:
            self.logger.error(f"拆分PDF失败: {e}")
            raise e

    # --- 步骤 2: 调用 MinerU API ---
    def _call_mineru_api(self, pdf_path, output_dir, is_ocr):
        try:
            filename = os.path.basename(pdf_path)
            
            # 保存分块结果
            key_name = os.path.splitext(filename)[0]
            chunk_out_dir = os.path.join(output_dir, key_name)
            os.makedirs(chunk_out_dir, exist_ok=True)
            # 这里的 files 指针必须在请求结束后关闭，所以使用 with open 并不是最方便的，requests 会自动处理
            files = {
                'files': (filename, open(pdf_path, 'rb'), 'application/pdf')
            }
            data = {
                'parse_method': 'auto',
                'output_dir': "./output",
                'return_md': 'true',
                'return_middle_json': 'false',
                'return_model_output': 'false',
                'return_images': 'true',
                'formula_enable': 'true',
                'table_enable': 'true',
                'backend': 'pipeline',
                'response_format_zip': 'true',
            }

            self.logger.info(f"调用 API 处理: {filename}")
            response = requests.post(self.api_url, files=files, data=data, timeout=3000)
            # 显式关闭文件句柄，防止资源泄露
            files['files'][1].close()
            if response.status_code != 200:
            # 如果不是200，API 可能会返回 JSON 格式的错误信息
                raise Exception(f"API Error: {response.status_code} - {response.text}")

            # --- 核心修改开始 ---
            # 因为是 zip 模式，不能用 .json()，而是处理二进制 .content
            self.logger.info("API 返回成功，正在解压 ZIP...")
            zip_content = response.content
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                z.extractall(chunk_out_dir)

            return chunk_out_dir
            # resp_json = response.json()
            # print(f"resp_json: {resp_json}")
            # md_content = ""
            # if "results" in resp_json and key_name in resp_json["results"]:
            #     md_content = resp_json["results"][key_name].get("md_content", "")
            
            # # 保存分块结果
            # chunk_out_dir = os.path.join(output_dir, key_name)
            # os.makedirs(chunk_out_dir, exist_ok=True)
            # md_file = os.path.join(chunk_out_dir, f"{key_name}.md")
            
            # with open(md_file, "w", encoding="utf-8") as f:
            #     f.write(md_content)
                
            # return chunk_out_dir, md_file
        except Exception as e:
            self.logger.error(f"API调用失败 {pdf_path}: {e}")
            raise e

    async def processing_mineru_workflow(
        self, 
        local_pdf_path, 
        output_dir,
        is_ocr,
        minio_img_prefix,
    ) -> Tuple[str, str]:
        
        # 1. 调用 API，获取解压后的本地目录
        extracted_path = await self._run_async(
            self._call_mineru_api, 
            pdf_path=local_pdf_path, 
            output_dir=output_dir, 
            is_ocr=is_ocr
        )
        final_md_path = None
        print(f"开始上传结果到 MinIO: {minio_img_prefix}")
        
        # 2. 遍历该目录并上传到 MinIO
        for root, dirs, files in os.walk(extracted_path):
            for file in files:
                local_file = os.path.join(root, file)
                if file.endswith('.md'):
                    # 获取绝对路径，确保后续使用不出错
                    final_md_path = os.path.abspath(local_file)
                    continue
                
                # 构造 MinIO 完整 Key
                upload_key = f"{minio_img_prefix}/{file}"
                # 执行上传
                await self.minio_service.internal_upload_file(self.minio_service.public_bucket, upload_key, local_file)
        if final_md_path is None:
            self.logger.warning("警告: 在结果目录中未找到 .md 文件")
            
        # 3. 返回 目录路径 和 MD文件路径
        return extracted_path, final_md_path


    def _sync_merge_and_replace(self, chunk_results, img_bucket_name):
        full_md_content = []
        minio_base = self.pdf_document_service.minio_base_url
        
        for item in chunk_results:
            md_file = item["md_file"]
            current_img_prefix = item["img_prefix"]
            if not md_file or not os.path.exists(md_file): continue
            
            # [阻塞操作] 读文件
            with open(md_file, "r", encoding="utf-8") as f:
                content = f.read()
            
            current_base_url = f"{minio_base}/{img_bucket_name}/{current_img_prefix}/"
            
            def replace_link(match):
                alt_text = match.group(1)
                raw_path = match.group(2)
                img_filename = os.path.basename(raw_path)
                return f"![{alt_text}]({current_base_url}{img_filename})"

            # [阻塞操作] 正则替换 (CPU密集)
            pattern = r'!\[(.*?)\]\((.*?)\)'
            content = re.sub(pattern, replace_link, content)
            
            full_md_content.append(content)
            full_md_content.append("\n\n")
            
        return "".join(full_md_content)
    

    # --- 步骤 3: 合并 ---
    async def _merge_results(self, doc_id, task_dir, chunk_results, final_name, img_bucket_name):
        final_out_dir = os.path.join(task_dir, "final_output")
        os.makedirs(final_out_dir, exist_ok=True)
        
        # 这样在合并的时候，FastAPI 依然可以响应其他请求，数据库也能更新
        merged_text = await self._run_async(
            self._sync_merge_and_replace, 
            chunk_results, 
            img_bucket_name
        )
        
        # MarkdownCleaner 内部已经是 async 的了，且调用了 LLM API，不会阻塞本地 CPU
        markdown_cleaner = MarkdownCleaner(
            file_path=merged_text,
            llm_config_service=self.llm_config_service,
            doc_id=doc_id
        )
        cleaned_text = await markdown_cleaner.run()

        # 写文件也放入线程池
        final_md_path = os.path.join(final_out_dir, f"{final_name}.md")
        
        def save_file():
            with open(final_md_path, "w", encoding="utf-8") as f:
                f.write(cleaned_text)
                
        await self._run_async(save_file)
            
        return final_md_path


    # --- 主流程入口 ---
    async def process_pdf_pipeline(
        self, 
        doc_id: int,          # 对应数据库主键 ID
        is_ocr: bool = False, 
        split_pages: int = 10,
    ):
        
        tasks = await self.task_service.get_tasks_by_doc_id(doc_id)
        extract_task = next((t for t in tasks if t['task_type'] == TaskType.MINERU_EXTRACT.value), None)
        if not extract_task: return
        task_id = extract_task['id']
        task_dir = os.path.join(self.work_dir, str(doc_id))
        
        # 1. [关键] 在这里明确定义目标存储位置
        try:
            # Step 0: 标记开始
            await self.task_service.update_task_status(
                task_id, 
                status=TaskLifecycle.RUNNING.value,
                detailed_status=ExtractStatus.OCR_PROCESSING.value,
            )
            
            # Step 1: 下载源文件
            condition = {"id": doc_id}
            docs = await self.pdf_document_service.get_document_list(condition)
            doc = docs['items'][0]
            local_pdf = os.path.join(task_dir, os.path.basename(doc.get("object_name")))
            os.makedirs(task_dir, exist_ok=True)
            await self.minio_service.internal_download_file(doc.get("bucket_name"), doc.get("object_name"), local_pdf)
            
            # Step 2: 拆分
            chunks = await self._run_async(self._split_pdf, local_pdf, split_pages)
            
            # Step 3: 循环处理
            chunk_results = []
            # 定义图片在 MinIO 上的路径前缀 (Virtual Folder)
            base_img_prefix = f"images/{doc_id}"
            total_chunks = len(chunks)
            
            start_pct = 10  # 循环开始前的基础进度 (假设下载拆分占了 10%)
            loop_weight = 85 # 循环处理占总任务的 85% (处理完达到 95%)
            for idx, chunk_path in enumerate(chunks):
                chunk_img_prefix = f"{base_img_prefix}/chunk_{idx}"
                # 1. 执行处理
                extracted_path, final_md_path = await self.processing_mineru_workflow(
                    chunk_path,
                    output_dir=task_dir,
                    is_ocr=is_ocr,
                    minio_img_prefix=chunk_img_prefix 
                )
                chunk_results.append({
                    "md_file": final_md_path,
                    "img_prefix": chunk_img_prefix 
                })
                # 2. [核心] 计算平滑进度
                current_progress = int(start_pct + ((idx + 1) / total_chunks) * loop_weight)
                # 3. 更新任务状态
                await self.task_service.update_task_status(
                    task_id,
                    status=TaskLifecycle.RUNNING.value,
                    detailed_status=ExtractStatus.OCR_PROCESSING.value,
                    progress=current_progress,
                )
                
            # Step 4: 合并 & 替换链接
            await self.task_service.update_task_status(
                task_id, 
                status=TaskLifecycle.RUNNING.value, 
                detailed_status=ExtractStatus.LAYOUT_MERGING.value,
                progress=95,
            )
            
            base_name = os.path.splitext(os.path.basename(doc.get("object_name")))[0]
            final_local_md = await self._merge_results( 
                doc_id,
                task_dir, 
                chunk_results, 
                base_name,
                self.minio_service.public_bucket,
            )
            # final_local_md = await self._run_async(
            #     self._merge_results, 
            #     doc_id,
            #     task_dir, 
            #     chunk_results, 
            #     base_name,
            #     self.minio_service.public_bucket,
            # )
            
            # Step 5: 上传 MD
            remote_md_path = f"{doc_id}/{base_name}.md"
            await self.minio_service.internal_upload_file(self.minio_service.target_bucket_name, remote_md_path, final_local_md)
            
            # Step 6: 任务完成 & 实例化 ExtractTaskResult
            result_obj = ExtractTaskResult(
                md_bucket=self.minio_service.target_bucket_name,
                markdown_path=remote_md_path,
                images_bucket=self.minio_service.public_bucket,
                images_path=f"{base_img_prefix}/"
            )
            
            # 第一步完成状态更新并激活下一步
            await self.task_service.update_task_status(
                task_id, 
                status=TaskLifecycle.SUCCESS.value,
                detailed_status=ExtractStatus.SUCCESS.value,
                progress=100,
                result_data=result_obj.model_dump(), # 序列化存入 DB
            )
            await self.task_service.activate_next_step(doc_id=doc_id, current_step_order=1)
            self.logger.info(f"Task {task_id} Success. Result: {result_obj}")
            
        except Exception as e:
            self.logger.error(f"Task Failed: {e}", exc_info=True)
            await self.task_service.update_task_status(
                task_id, 
                status=TaskLifecycle.FAILED.value, 
                detailed_status=ExtractStatus.FAILED.value,
                error_message=str(e)
            )
        finally:
            if os.path.exists(task_dir): shutil.rmtree(task_dir)