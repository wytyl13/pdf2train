#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 21:27
@Author  : weiyutao
@File    : pdf2md_manager.py
"""

import os
import logging
import asyncio
import re
from typing import Optional
from fastapi import BackgroundTasks

from pdf2train.core.table.llm_config import LLMConfig
from pdf2train.core.schema.llm_config_dto import LLMConfigCoreDTO
from pdf2train.core.service.pdf_document_service import PdfDocumentService, PdfDocUpdateDTO
from pdf2train.core.service.pipeline_task_service import PipelineTaskService, PipelineTaskCoreDTO, PipelineTaskUpdateDTO
from pdf2train.core.service.pdf2md_service import Pdf2MdService
from pdf2train.core.service.llm_config_service import LLMConfigService
from pdf2train.core.table.pipeline_task import TaskType, TaskLifecycle, ExtractStatus, ExtractTaskResult

from pdf2train.core.config import core_config

class Pdf2MdManager:
    def __init__(
        self,
        pdf_document_service: PdfDocumentService,
        pipeline_task_service: PipelineTaskService,
        pdf2md_service: Pdf2MdService,
        llm_config_service: LLMConfigService
    ):
        self.doc_service = pdf_document_service
        self.task_service = pipeline_task_service
        self.worker_service = pdf2md_service
        self.llm_config_service = llm_config_service
        self.logger = logging.getLogger("Pdf2MdManager")

    async def submit_convert_task(
        self, 
        doc_id: int, 
        is_ocr: bool, 
        split_pages: int, 
        background_tasks: BackgroundTasks
    ) -> None:
        """提交转换任务"""
        # 1. 验证文档是否存在
        doc = await self.doc_service.get_by_id(doc_id)
        if not doc:
            raise ValueError(f"Document {doc_id} not found")

        # 2. 查找或创建 Pipeline Task (Extract Task)
        tasks = await self.task_service.get_by_doc_id(doc_id)
        extract_task = next((t for t in tasks if t.task_type == TaskType.MINERU_EXTRACT.value), None)
        
        if not extract_task:
            raise ValueError("Pipeline task not initialized")
        else:
            task_id = extract_task.id
            # 重置状态
            await self.task_service.update(task_id, PipelineTaskUpdateDTO(
                status=TaskLifecycle.PENDING.value,
                progress=0,
            ))

        # 3. 添加到后台任务
        background_tasks.add_task(
            self.process_pdf_pipeline,
            task_id=task_id,
            doc_id=doc_id,
            is_ocr=is_ocr,
            split_pages=split_pages
        )

    def get_short_safe_name(self, filename, max_chars=50):
        # 1. 分离文件名和后缀
        # 例如: "非常长...的文档.pdf" -> name="非常长...的文档", ext=".pdf"
        name, ext = os.path.splitext(filename)
        
        # 2. (可选但推荐) 清洗掉怪异字符，避免 S3 报错
        # 把非中文、非英文、非数字的字符换成下划线
        name = re.sub(r'[^\w\u4e00-\u9fa5\-]', '_', name)

        # 3. 截取前 max_chars 个字符
        # 比如截取前 50 个字。如果文件名本身短于 50，这行代码也不会报错
        short_name = name[:max_chars]
        
        # 4. 拼回后缀
        return f"{short_name}{ext}"

    async def process_pdf_pipeline(self, task_id: int, doc_id: int, is_ocr: bool, split_pages: int) -> None:
        """后台执行转换流程"""
        task_dir = await self.worker_service.prepare_workspace(doc_id)
        
        try:
            # Step 1: 标记开始
            await self.task_service.update(task_id, PipelineTaskUpdateDTO(
                status=TaskLifecycle.RUNNING.value,
                detailed_status=ExtractStatus.OCR_PROCESSING.value,
                progress=0
            ))
            await self.doc_service.update(doc_id, PdfDocUpdateDTO(
                status=TaskLifecycle.RUNNING.value,
                progress=0
            ))
            
            # Step 2: 获取文档信息并下载
            doc = await self.doc_service.get_by_id(doc_id)
            local_pdf_path = await self.worker_service.download_pdf(
                doc.bucket_name, doc.object_name, task_dir
            )

            # Step 3: 拆分 PDF
            chunks = await self.worker_service.split_pdf(local_pdf_path, split_pages)
            total_chunks = len(chunks)
            
            # Step 4: 循环调用 MinerU
            chunk_results = []
            base_img_prefix = f"images/{doc_id}"
            
            for idx, chunk_path in enumerate(chunks):
                chunk_img_prefix = f"{base_img_prefix}/chunk_{idx}"
                result = await self.worker_service.extract_chunk(
                    chunk_path=chunk_path,
                    output_dir=task_dir,
                    is_ocr=is_ocr,
                    minio_img_prefix=chunk_img_prefix
                )
                chunk_results.append(result)
                
                # 更新进度 (10% -> 90%)
                progress = int(10 + ((idx + 1) / total_chunks) * 80)
                await self.task_service.update(task_id, PipelineTaskUpdateDTO(progress=progress))

            # Step 5: 合并与清洗
            await self.task_service.update(task_id, PipelineTaskUpdateDTO(
                detailed_status=ExtractStatus.LAYOUT_MERGING.value,
                progress=95
            ))
            
            final_name = os.path.splitext(doc.file_name)[0]
            final_name = self.get_short_safe_name(final_name)
            # 获取 MinIO Base URL (用于图片链接拼接)
            minio_base = core_config.minio_config.minio_public_url # 假设 Config 能取到，或者通过 MinioService 取
            if not minio_base.startswith("http"): minio_base = f"http://{minio_base}"
            llm_config_obj: LLMConfig = await self.llm_config_service.get_config_by_doc_id(doc_id, field_llm_id_name="h_title_llm_config_id")
            # 2. 将 ORM 对象转换为字典
            llm_config_dict = None
            if llm_config_obj:
                # 过滤掉 SQLAlchemy 的内部状态字段 (以 _sa_ 开头的字段)
                llm_config_dict = {
                    k: v for k, v in llm_config_obj.__dict__.items() 
                    if not k.startswith('_sa_')
                }
            local_md_path = await self.worker_service.merge_and_clean(
                doc_id, chunk_results, final_name, task_dir, 
                minio_base_url=minio_base,
                llm_config=llm_config_dict
            )

            # Step 6: 上传最终 Markdown
            remote_md_path = f"{doc_id}/{final_name}.md"
            await self.worker_service.minio_service.internal_upload_file(
                self.worker_service.minio_service.target_bucket_name, 
                remote_md_path, 
                local_md_path
            )

            # Step 7: 成功
            result_obj = ExtractTaskResult(
                md_bucket=self.worker_service.minio_service.target_bucket_name,
                markdown_path=remote_md_path,
                images_bucket=self.worker_service.minio_service.public_bucket,
                images_path=f"{base_img_prefix}/"
            )
            
            await self.task_service.update(task_id, PipelineTaskUpdateDTO(
                status=TaskLifecycle.SUCCESS.value,
                detailed_status=ExtractStatus.SUCCESS.value,
                progress=100,
                result_data=result_obj.model_dump()
            ))
            await self.doc_service.update(doc_id, PdfDocUpdateDTO(
                status=TaskLifecycle.RUNNING.value,
                progress=20
            ))

            # 8. 更新文档元数据 (例如原标题，这里是在解析到文档实际内容后获取更全的文档原始信息）
            # await self.doc_service.update(doc_id, PdfDocUpdateDTO(...))
            
            # 9. 激活下一步，这里只是激活下部的状态，而不是直接执行下一步
            await self.task_service.activate_next_step(doc_id=doc_id, current_step_order=1)
            
            self.logger.info(f"Task {doc_id} Completed Successfully.")

        except Exception as e:
            self.logger.error(f"Task {doc_id} Failed: {e}", exc_info=True)
            # 更新报错信息
            await self.task_service.update(task_id, PipelineTaskUpdateDTO(
                status=TaskLifecycle.FAILED.value,
                detailed_status=ExtractStatus.FAILED.value,
                error_message=str(e)
            ))
            await self.doc_service.update(doc_id, PdfDocUpdateDTO(
                status=TaskLifecycle.FAILED.value,
                process_error=str(e)
            ))
        finally:
            await self.worker_service.cleanup_workspace(task_dir)