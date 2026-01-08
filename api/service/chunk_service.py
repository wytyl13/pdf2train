#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/24 13:22
@Author  : weiyutao
@File    : chunk_service.py
"""

import json
import uuid
import logging
from io import BytesIO
from typing import List
from llama_index.core.schema import Document, TextNode
import os

# 引入底层依赖
from api.service.pdf_document_service import PdfDocumentService
from api.service.document_chunk_service import DocumentChunkService
from api.service.minio_service import MinioService
from api.service.pipeline_task_service import PipelineTaskService
from tool.markdown_parser import HybridMarkdownParser
from api.table.base.pipeline_task import TaskType, TaskLifecycle, ChunkStatus, ChunkTaskResult


class ChunkService:
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

    async def run_document_chunking(self, doc_id: int):
        """
        [主入口] 执行文档切片全流程
        """
        self.logger.info(f"🚀 [Doc {doc_id}] 开始切片任务...")
        
        try:
            tasks = await self.pipeline_task_service.get_tasks_by_doc_id(doc_id)
            extract_task = next((t for t in tasks if t['task_type'] == TaskType.MARKDOWN_CHUNK.value), None)
            if not extract_task: return
            task_id = extract_task['id']
            
            # === Step 1: 准备数据 (Extract) ===
            md_content = await self.pdf_document_service.get_markdown_content(doc_id)
            if not md_content:
                raise ValueError("Markdown 内容为空")
            
            # 获取文件名用于元数据
            doc_info = await self.pdf_document_service.get_document_list(condition={"id": doc_id})
            item_ = doc_info.get("items", [])
            file_name = item_[0].get("file_name", "unknown") if item_ else "unknown"
            object_name = item_[0].get("object_name", "unknown") if item_ else "unknown"
            object_name_pre = os.path.splitext(object_name)[0]
            
            # === Step 2: 解析切分 (Transform - Parser) ===
            # 更新ChunkStatus任务状态为SPLITTING
            await self.pipeline_task_service.update_task_status(
                task_id,
                status=TaskLifecycle.RUNNING.value,
                detailed_status=ChunkStatus.SPLITTING.value,
                progress=ChunkStatus.SPLITTING.value,
            )
            # 包装为 LlamaIndex Document
            llama_doc = Document(text=md_content, metadata={"file_name": file_name, "document_id": doc_id})
            parser = HybridMarkdownParser(chunk_size=500, chunk_overlap=50)
            nodes = parser.get_nodes_from_documents([llama_doc])
            self.logger.info(f"✅ [Doc {doc_id}] 解析完成，生成 {len(nodes)} 个节点")

            # === Step 3: 数据清洗与格式化 (Transform - Formatting) ===
            # 更新ChunkStatus任务状态为CLEANING
            await self.pipeline_task_service.update_task_status(
                task_id,
                status=TaskLifecycle.RUNNING.value,
                detailed_status=ChunkStatus.CLEANING.value,
                progress=ChunkStatus.CLEANING.value,
            )
            formatted_chunks = self._format_nodes_to_dicts(doc_id, nodes)

            # === Step 4: 归档与入库 (Load) ===
            # 4.1 存 MinIO (归档 JSON)
            # 更新ChunkStatus任务状态为RESULT_UPLOADING
            await self.pipeline_task_service.update_task_status(
                task_id,
                status=TaskLifecycle.RUNNING.value,
                detailed_status=ChunkStatus.RESULT_UPLOADING.value,
                progress=ChunkStatus.RESULT_UPLOADING.value,
            )
            json_bucket, json_path = await self._archive_json_to_minio(doc_id, object_name_pre, formatted_chunks)
            
            # 4.2 存 PostgreSQL (业务数据)
            saved_count = await self.document_chunk_service.batch_save_chunks(doc_id, formatted_chunks)
            
            # === Step 5: 主文档状态会自动在update_task_status中更新 ===
            result_obj = ChunkTaskResult(
                json_bucket=json_bucket,
                json_path=json_path,
                chunk_count=saved_count
            )
            await self.pipeline_task_service.update_task_status(
                task_id,
                status=TaskLifecycle.SUCCESS.value,
                detailed_status=ChunkStatus.SUCCESS.value,
                progress=ChunkStatus.SUCCESS.value,
                result_data=result_obj.model_dump(),
            )
            await self.pipeline_task_service.activate_next_step(doc_id=doc_id, current_step_order=2)
            self.logger.info(f"🎉 [Doc {doc_id}] 切片入库完成，共 {saved_count} 条")
            return saved_count

        except Exception as e:
            import traceback
            error_msg = f"❌ [Doc {doc_id}] 切片失败: {str(e)}\n{str(traceback.format_exc())}"
            self.logger.error(error_msg)
            await self.pipeline_task_service.update_task_status(
                task_id,
                status=TaskLifecycle.FAILED.value,
                detailed_status=ChunkStatus.FAILED.value,
                progress=ChunkStatus.FAILED.value,
                error_message=error_msg
            )
            raise e


    def _format_nodes_to_dicts(self, doc_id: int, nodes: List[TextNode]) -> List[dict]:
        """将 Nodes 转换为我们要存库的字典格式"""
        clean_data = []
        for i, node in enumerate(nodes):
            raw_meta = node.metadata.copy()
            images = raw_meta.pop('images', []) # 提取图片
            
            clean_data.append({
                "id": str(uuid.uuid4()),
                "document_id": doc_id,
                "chunk_index": i,
                "content": node.text,
                "image_info": images,
                "meta_info": raw_meta,
                "token_count": len(node.text),
                "is_indexed": False
            })
        return clean_data


    async def _archive_json_to_minio(
        self, 
        doc_id: int, 
        object_name_pre: str,
        data: List[dict]
    ):
        """归档 JSON 到 MinIO"""
        try:
            json_str = json.dumps(data, ensure_ascii=False, indent=2)
            path = f"{doc_id}/{object_name_pre}.json"
            bucket = self.minio_service.target_bucket_name
            from io import BytesIO
            await self.minio_service.put_object_text(
                bucket_name=bucket, 
                object_name=path, 
                content=json_str, 
                content_type="application/json"
            )
            return bucket, path
        except Exception as e:
            self.logger.warning(f"⚠️ MinIO 归档失败(不影响主流程): {e}")