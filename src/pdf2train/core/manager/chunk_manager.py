#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/24 17:47
@Author  : weiyutao
@File    : chunk_manager.py
"""

import json
import uuid
import logging
import os
import traceback
from typing import List
from fastapi import BackgroundTasks
from llama_index.core.schema import Document, TextNode

# Reuse existing services and tools
from pdf2train.core.table.pdf_document import PdfDocument
from pdf2train.core.table.pipeline_task import PipelineTask
from pdf2train.core.schema.document_chunk_dto import DocumentChunkCoreDTO
from pdf2train.core.schema.pipeline_task_dto import (
    PipelineTaskUpdateDTO,
    PipelineTaskCoreDTO,
)

from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.core.service.document_chunk_service import DocumentChunkService
from pdf2train.core.service.minio_service import MinioService
from pdf2train.core.service.pipeline_task_service import PipelineTaskService
from pdf2train.tool.markdown_parser import HybridMarkdownParser
from pdf2train.core.table.pipeline_task import TaskType, TaskLifecycle, ChunkStatus, ChunkTaskResult

class ChunkManager:
    def __init__(
        self, 
        pdf_document_service: PdfDocumentService,
        document_chunk_service: DocumentChunkService,
        pipeline_task_service: PipelineTaskService,
        minio_service: MinioService
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.pdf_document_service = pdf_document_service
        self.document_chunk_service = document_chunk_service
        self.pipeline_task_service = pipeline_task_service
        self.minio_service = minio_service

    async def submit_chunk_task(
        self, 
        doc_id: int, 
        chunk_size: int, 
        overlap: int, 
        background_tasks: BackgroundTasks
    ) -> None:
        """
        [Sync Phase] 校验并提交后台任务
        """
        # 1. 简单校验文档是否存在 (Optional, based on requirement)
        db_data: PdfDocument  = await self.pdf_document_service.get_by_id(doc_id)
        if not db_data:
            raise ValueError(f"Document {doc_id} not found")

        # 2. 确保 Task 记录存在 (Create or Reset)
        # 这里我们查找现有的 MARKDOWN_CHUNK 任务，如果不存在则创建，如果存在则重置状态
        tasks: List[PipelineTask] = await self.pipeline_task_service.get_by_doc_id(doc_id)
        task = next((t for t in tasks if t.task_type == TaskType.MARKDOWN_CHUNK.value), None)
        
        task_id = None
        if task:
            task_id = task.id
            # 重置为 Pending 状态
            await self.pipeline_task_service.update(
                task_id, 
                PipelineTaskUpdateDTO(
                    status=TaskLifecycle.PENDING.value,
                    progress=0
                )
            )
        else:
            self.logger.error(f"Task for doc {doc_id} not found. Ensure pipeline initialized.")
            raise ValueError("Pipeline task not initialized")

        self.logger.info(f"🚀 [Doc {doc_id}] 提交切分任务 (Size={chunk_size}, Overlap={overlap})")
        
        # 3. 添加到后台任务
        background_tasks.add_task(
            self.run_document_chunking, 
            doc_id=doc_id, 
            chunk_size=chunk_size, 
            overlap=overlap,
            task_id=task_id 
        )

    async def run_document_chunking(self, doc_id: int, chunk_size: int, overlap: int, task_id: int):
        """
        [Async Phase] 执行文档切片全流程 (Extract -> Transform -> Load)
        """
        try:
            # 如果 submit 阶段没有传 task_id，尝试重新获取
            if not task_id:
                raise ValueError("Pipeline task not initialized") 

            # === Step 1: 准备数据 (Extract) ===
            md_content = await self.get_markdown_content(doc_id)
            if not md_content:
                raise ValueError("Markdown 内容为空，请先执行解析步骤")
            
            # 获取元数据
            doc_info: PdfDocument = await self.pdf_document_service.get_by_id(doc_id)
            file_name = doc_info.file_name or "unknown"
            object_name = doc_info.object_name or "unknown"
            object_name_pre = os.path.splitext(object_name)[0]

            # === Step 2: 解析切分 (Transform - Parser) ===
            split_statu: bool = await self.pipeline_task_service.update(
                task_id, 
                PipelineTaskUpdateDTO(
                    status=TaskLifecycle.RUNNING, 
                    detailed_status=ChunkStatus.SPLITTING
                )
            )
            llama_doc = Document(text=md_content, metadata={"file_name": file_name, "document_id": doc_id})
            # 使用传入的 chunk_size 和 overlap
            parser = HybridMarkdownParser(chunk_size=chunk_size, chunk_overlap=overlap)
            nodes = parser.get_nodes_from_documents([llama_doc])
            self.logger.info(f"✅ [Doc {doc_id}] 解析完成，生成 {len(nodes)} 个节点")

            # === Step 3: 数据清洗与格式化 (Transform - Formatting) ===
            split_clean: bool = await self.pipeline_task_service.update(
                task_id, 
                PipelineTaskUpdateDTO(
                    status=TaskLifecycle.RUNNING, 
                    detailed_status=ChunkStatus.CLEANING
                )
            )
            formatted_chunks: List[DocumentChunkCoreDTO] = self._format_nodes_to_dicts(doc_id, nodes)

            # === Step 4: 归档与入库 (Load) ===
            # 4.1 存 MinIO (归档 JSON)
            split_upload: bool = await self.pipeline_task_service.update(
                task_id, 
                PipelineTaskUpdateDTO(
                    status=TaskLifecycle.RUNNING, 
                    detailed_status=ChunkStatus.RESULT_UPLOADING
                )
            )
            json_bucket, json_path = await self._archive_json_to_minio(doc_id, object_name_pre, formatted_chunks)
            
            # 4.2 存 PostgreSQL (业务数据)
            # 注意: 这里可能需要先清理旧的 chunk (如果是重跑)
            # await self.document_chunk_service.delete_chunks_by_doc_id(doc_id)
            saved_count = await self.document_chunk_service.create_batch(formatted_chunks)
            
            # === Step 5: 完成 ===
            result_obj = ChunkTaskResult(
                json_bucket=json_bucket,
                json_path=json_path,
                chunk_count=saved_count
            )
            
            await self.pipeline_task_service.update(
                task_id,
                PipelineTaskUpdateDTO(
                    status=TaskLifecycle.SUCCESS.value,
                    detailed_status=ChunkStatus.SUCCESS.value,
                    progress=ChunkStatus.SUCCESS.value,
                    result_data=result_obj.model_dump()
                )
            )
            
            # 激活下一步 (Embedding)
            await self.pipeline_task_service.activate_next_step(doc_id=doc_id, current_step_order=2)
            self.logger.info(f"🎉 [Doc {doc_id}] 切片入库完成，共 {saved_count} 条")

        except Exception as e:
            error_msg = f"❌ [Doc {doc_id}] 切片失败: {str(e)}\n{str(traceback.format_exc())}"
            self.logger.error(error_msg)
            if task_id:
                await self.pipeline_task_service.update(
                    task_id,
                    PipelineTaskUpdateDTO(
                        status=TaskLifecycle.FAILED.value,
                        detailed_status=ChunkStatus.FAILED.value,
                        progress=ChunkStatus.FAILED.value,
                        error_message=str(e)
                    )
                )

    def _format_nodes_to_dicts(self, doc_id: int, nodes: List[TextNode]) -> List[DocumentChunkCoreDTO]:
        """将 Nodes 转换为存库字典格式"""
        clean_data = []
        for i, node in enumerate(nodes):
            raw_meta = node.metadata.copy()
            images = raw_meta.pop('images', [])
            
            # 4. 构建 DTO
            str_id = str(uuid.uuid4())
            dto = DocumentChunkCoreDTO(
                id=str_id,
                document_id=doc_id,
                chunk_index=i,
                content=node.text,
                # 如果 images 里的字典结构和 ChunkImageInfo 定义一致，Pydantic 会自动转换
                image_info=images, 
                meta_info=raw_meta,
                # 注意：这里目前是字符长度，如果需要精准 Token 数需引入 Tokenizer
                token_count=len(node.text), 
                is_indexed=False,
                qdrant_point_id=str_id
            )
            
            clean_data.append(dto)
        return clean_data

    async def get_markdown_content(self, doc_id: int) -> str:
        """
        获取 Markdown 内容
        """
        # 1. 改用 Service 调用，复用连接池，不要手动 new SqlProvider
        # Service 层已经封装了 get_with_relations
        doc: PdfDocument = await self.pdf_document_service.get_with_relations(doc_id, relations=["tasks"])
        
        if not doc:
            raise FileNotFoundError(f"文档 ID {doc_id} 不存在")
        
        # 2. 【核心逻辑保留】直接调用 Model 层的智能属性
        result = getattr(doc, 'latest_extract_result', None)
        
        if not result:
            # 这里抛出异常给前端提示，或者返回空字符串看你需求
            raise ValueError(f"文档 {doc_id} 尚未生成解析结果")
        
        # 3. 读 MinIO
        try:
            return await self.minio_service.read_object_text(
                result.md_bucket,      
                result.markdown_path   
            )
        except Exception as e:
            self.logger.error(f"MinIO 读取失败: {e}")
            raise RuntimeError(f"底层存储读取失败: {str(e)}")

    async def _archive_json_to_minio(self, doc_id: int, object_name_pre: str, data: List[DocumentChunkCoreDTO]):
        """归档 JSON 到 MinIO"""
        try:
            data_dicts = [item.model_dump(mode='json') for item in data]
            json_str = json.dumps(data_dicts, ensure_ascii=False, indent=2)
            path = f"{doc_id}/{object_name_pre}_chunks.json" # Added suffix to distinguish
            bucket = self.minio_service.target_bucket_name
            
            await self.minio_service.put_object_text(
                bucket_name=bucket, 
                object_name=path, 
                content=json_str, 
                content_type="application/json"
            )
            return bucket, path
        except Exception as e:
            self.logger.warning(f"⚠️ MinIO 归档失败(不影响主流程): {e}")
            return "", ""