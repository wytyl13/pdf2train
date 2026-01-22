#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/16 16:59
@Author  : weiyutao
@File    : pdf2md_service.py
"""

import os
import shutil
import logging
import asyncio
import requests
import zipfile
import io
import re
from typing import Tuple, List, Dict
from functools import partial
from PyPDF2 import PdfReader, PdfWriter

from pdf2train.core.service.minio_service import MinioService
from pdf2train.tool.markdown_cleaner import MarkdownCleaner
from pdf2train.core.service.llm_config_service import LLMConfigService


class Pdf2MdService:
    """
    [Worker Layer] PDF 转 Markdown 的原子能力服务
    只负责：IO操作、API调用、计算。不负责：数据库读写、任务状态流转。
    """
    def __init__(
        self, 
        minio_service: MinioService, 
        mineru_api_url: str,
        llm_config_service: LLMConfigService,
        work_dir: str = "/tmp/pdf2md_worker"
    ):
        self.minio_service = minio_service
        self.api_url = mineru_api_url
        self.llm_config_service = llm_config_service
        self.work_dir = work_dir
        self.logger = logging.getLogger("Pdf2MdService")
        os.makedirs(self.work_dir, exist_ok=True)

    async def _run_async(self, func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    async def prepare_workspace(self, doc_id: int) -> str:
        """准备工作目录"""
        task_dir = os.path.join(self.work_dir, str(doc_id))
        os.makedirs(task_dir, exist_ok=True)
        return task_dir

    async def cleanup_workspace(self, task_dir: str):
        """清理工作目录"""
        if os.path.exists(task_dir):
            shutil.rmtree(task_dir)

    async def download_pdf(self, bucket: str, object_name: str, local_dir: str) -> str:
        """下载PDF到本地"""
        local_path = os.path.join(local_dir, os.path.basename(object_name))
        await self.minio_service.internal_download_file(bucket, object_name, local_path)
        return local_path

    async def split_pdf(self, input_path: str, chunk_size: int) -> List[str]:
        """拆分PDF"""
        return await self._run_async(self._sync_split_pdf, input_path, chunk_size)

    def _sync_split_pdf(self, input_path, chunk_size):
        try:
            reader = PdfReader(input_path)
            total_pages = len(reader.pages)
            if total_pages <= chunk_size:
                return [input_path]

            self.logger.info(f"PDF共 {total_pages} 页，按每 {chunk_size} 页拆分...")
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

    async def extract_chunk(self, chunk_path: str, output_dir: str, is_ocr: bool, minio_img_prefix: str) -> Dict:
        """处理单个分块：调用API -> 解压 -> 上传图片 -> 返回MD路径"""
        # 1. 调用 API
        extracted_dir = await self._run_async(
            self._sync_call_mineru_api, 
            pdf_path=chunk_path, 
            output_dir=output_dir, 
            is_ocr=is_ocr
        )
        
        final_md_path = None
        # 2. 上传图片 & 查找MD
        for root, _, files in os.walk(extracted_dir):
            for file in files:
                local_file = os.path.join(root, file)
                if file.endswith('.md'):
                    final_md_path = os.path.abspath(local_file)
                    continue
                
                # 上传图片到 MinIO Public Bucket
                upload_key = f"{minio_img_prefix}/{file}"
                await self.minio_service.internal_upload_file(
                    self.minio_service.public_bucket, upload_key, local_file
                )
        
        if not final_md_path:
            raise Exception("MinerU API 未返回 Markdown 文件")

        return {
            "md_file": final_md_path,
            "img_prefix": minio_img_prefix
        }

    def _sync_call_mineru_api(self, pdf_path, output_dir, is_ocr):
        filename = os.path.basename(pdf_path)
        key_name = os.path.splitext(filename)[0]
        chunk_out_dir = os.path.join(output_dir, key_name)
        os.makedirs(chunk_out_dir, exist_ok=True)

        files = {'files': (filename, open(pdf_path, 'rb'), 'application/pdf')}
        data = {
            'parse_method': 'auto',
            'output_dir': "./output",
            'return_md': 'true',
            'return_images': 'true',
            'formula_enable': 'true',
            'table_enable': 'true',
            'response_format_zip': 'true',
        }

        try:
            response = requests.post(self.api_url, files=files, data=data, timeout=3000)
            if response.status_code != 200:
                raise Exception(f"API Error: {response.status_code} - {response.text}")

            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                z.extractall(chunk_out_dir)
            return chunk_out_dir
        finally:
            files['files'][1].close()

    async def merge_and_clean(
        self, 
        doc_id: int, 
        chunk_results: List[Dict], 
        final_name: str, 
        task_dir: str, 
        minio_base_url: str,
        llm_config: Dict[str, str]
    ) -> str:
        """合并分块结果，替换图片链接，并使用LLM清洗"""
        final_out_dir = os.path.join(task_dir, "final_output")
        os.makedirs(final_out_dir, exist_ok=True)
        final_md_path = os.path.join(final_out_dir, f"{final_name}.md")

        # 1. 文本合并 & 链接替换
        merged_text = await self._run_async(
            self._sync_merge_and_replace, 
            chunk_results, 
            self.minio_service.public_bucket,
            minio_base_url
        )

        # 2. LLM 清洗
        markdown_cleaner = MarkdownCleaner(
            file_path=merged_text, # 注意: Cleaner如果支持直接传文本更好，这里假设它需要路径或文本
            llm_config=llm_config,
            doc_id=doc_id
        )
        # 这里为了适配 Cleaner 接口，如果 Cleaner 需要文件路径，我们可能需要先保存一次
        # 假设 Cleaner 内部处理文本
        cleaned_text = await markdown_cleaner.run()

        # 3. 保存最终文件
        def save_file():
            with open(final_md_path, "w", encoding="utf-8") as f:
                f.write(cleaned_text)
        await self._run_async(save_file)
        
        return final_md_path

    def _sync_merge_and_replace(self, chunk_results, img_bucket_name, minio_base):
        full_md_content = []
        for item in chunk_results:
            md_file = item["md_file"]
            img_prefix = item["img_prefix"]
            if not md_file or not os.path.exists(md_file): continue
            
            with open(md_file, "r", encoding="utf-8") as f:
                content = f.read()
            
            current_base_url = f"{minio_base}/{img_bucket_name}/{img_prefix}/"
            content = re.sub(r'!\[(.*?)\]\((.*?)\)', 
                             lambda m: f"![{m.group(1)}]({current_base_url}{os.path.basename(m.group(2))})", 
                             content)
            full_md_content.append(content)
            full_md_content.append("\n\n")
        return "".join(full_md_content)