#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/19 16:28
@Author  : weiyutao
@File    : document_chunk_manager.py
"""

from fastapi import Depends
from typing import Dict, List, Any, AsyncGenerator

from pdf2train.core.service.document_chunk_service import DocumentChunkService, DocumentChunk
from pdf2train.core.service.pipeline_task_service import PipelineTaskService
from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.core.schema.base_schema import PageResult
from pdf2train.core.schema.document_chunk_dto import (
    DocumentChunkCoreDTO,
    DocumentChunkFilterDTO,
    DocumentChunkUpdateDTO,
)

from pdf2train.core.table.pipeline_task import PipelineTask, TaskLifecycle, TaskType, ChunkStatus
from pdf2train.core.schema.pipeline_task_dto import PipelineTaskUpdateDTO

class DocumentChunkManager:
    def __init__(
        self, 
        document_chunk_service: DocumentChunkService,
        pipeline_task_service: PipelineTaskService,
        pdf_document_service: PdfDocumentService
    ):
        self.service = document_chunk_service
        self.pipeline_task_service = pipeline_task_service
        self.pdf_document_service = pdf_document_service

    async def list_chunks(
        self, 
        filter_dto: DocumentChunkFilterDTO, 
        page: int, 
        page_size: int, 
    ) -> PageResult[DocumentChunkCoreDTO]:
        """
        Business Logic: List chunks and convert DB Models to Pydantic Schema
        """
        # 1. Call Service
        db_result: Dict[str, List[DocumentChunk] | int] = await self.service.search_paginated(filter_dto, page, page_size)
        return PageResult[DocumentChunkCoreDTO](**db_result)
        
    async def update_chunk(
        self, 
        chunk_id: str, 
        update_dto: DocumentChunkUpdateDTO
    ) -> bool:
        """
        Business Logic: Update SQL -> Mark dirty -> (Optional) Sync Vector
        单个chunk更新：
        先根据更新内容更新数据库状态
        如果需要更新向量数据库则更新并在向量数据更新完以后再次更新数据库状态
        """
        # 1. 查看现有的is_indexed
        old_is_indexed_status = await self.get_indexed_status_by_chunk_id(chunk_id)
        
        # 2. Logic: If content changes, token count changes and index becomes invalid
        if "content" in update_dto.model_fields_set:
            new_content = update_dto.content
            if new_content:
                 update_dto.token_count = len(new_content)
                 # 必须更新状态为false，先更新，后处理完了再更新回来
                 update_dto.is_indexed = False
        else:
             update_dto.token_count = 0
        
        # 3. Call Service
        success = await self.service.update(chunk_id, update_dto)
        
        if success:
            # TODO: Async trigger vector deletion or re-embedding here if needed
            # For now, we just marked is_indexed=False in SQL
            if old_is_indexed_status and not update_dto.is_indexed:
                # 重新嵌入向量 
                self.logger.info("重新嵌入向量！")
                pass
            
        return success
    
    async def get_indexed_status_by_chunk_id(
        self, 
        chunk_id: str, 
    ) -> bool:
        document_chun_data: DocumentChunk = await self.service.get_by_id(chunk_id)
        return document_chun_data.is_indexed
    
    async def delete_chunk(self, chunk_id: str) -> bool:
        """
        删除指定chunk
        Business Logic: Get Doc ID -> Delete SQL -> Delete Vector -> Update Task Stats
        """
        try:
            # 1. Get Doc ID (Need for Vector delete and Task update)
            chunk_db_data: DocumentChunk = await self.service.get_by_id(chunk_id)
            if not chunk_db_data: raise ValueError(f"[DocumentChunk]没有查询到要删除的chunk！{chunk_id}")
            
            # 2. Delete from SQL
            success = await self.service.delete(chunk_id)
            if not success: raise ValueError(f"[DocumentChunk]删除失败！{chunk_id}")

            # 3. Delete from Vector DB (Cross-Service Call)
            # collection_name = await self.vector_service.get_collection_name_by_doc_id(chunk_db_data.document_id)
            # if collection_name:
            #     await self.vector_service.delete_vector(
            #         VectorDeleteRequest(
            #             collection_name=collection_name,
            #             filters={"chunk_id": chunk_id}
            #         )
            #     )

            # 4. 不需要 Update Task Stats
            # await self._decrease_task_count(doc_id)
            
            # 5. 判断是否是最后一个删除，如果是更新对应的task状态为pending
            counts_map: Dict[int, int] = await self.service.get_counts_by_doc_ids([chunk_db_data.document_id])
            if not counts_map.get(chunk_db_data.document_id):
                task_db_data: PipelineTask = await self.pipeline_task_service.get_specific_task_by_doc_id(chunk_db_data.document_id, TaskType.MARKDOWN_CHUNK.value)
                task_update_status: bool = await self.pipeline_task_service.update_and_refresh_parent_doc_status(
                    task_db_data.id,
                    PipelineTaskUpdateDTO(
                        status=TaskLifecycle.PENDING.value,
                        detailed_status=ChunkStatus.PENDING.value,
                        result_data=None
                    )
                )
                if not task_update_status:
                    raise ValueError(f"[PipelineTask]更新状态失败-{task_db_data.id}-")
            return success
        except Exception as e:
            raise ValueError(f"删除指定chunk失败！{str(e)}") from e
    
    async def delete_chunks_by_doc_id(self, doc_id: int) -> int:
        """
        清空指定知识块
        """
        try:
            # 1. Delete SQL
            count = await self.service.delete_by_doc_id(doc_id)
            
            # 2. Delete Vector
            # collection_name = await self.vector_service.get_collection_name_by_doc_id(doc_id)
            # if collection_name:
            #      await self.vector_service.delete_vector(
            #         VectorDeleteRequest(
            #             collection_name=collection_name,
            #             filters={"doc_kb_id": doc_id, "type": "document_chunk"}
            #         )
            #     )
                
            # 3. Reset Task Logic
            # (Simplified for brevity, similar to _decrease_task_count but setting to 0)
            
            # 5. 判断是否是最后一个删除，如果是更新对应的task状态为pending
            task_db_data: PipelineTask = await self.pipeline_task_service.get_specific_task_by_doc_id(doc_id, TaskType.MARKDOWN_CHUNK.value)
            task_update_status: bool = await self.pipeline_task_service.update_and_refresh_parent_doc_status(
                task_db_data.id,
                PipelineTaskUpdateDTO(
                    status=TaskLifecycle.PENDING.value,
                    detailed_status=ChunkStatus.PENDING.value,
                    result_data=None
                )
            )
            if not task_update_status:
                raise ValueError(f"[PipelineTask]更新状态失败-{task_db_data.id}-")
            return count
        except Exception as e:
            raise ValueError(f"清空指定知识块失败！{str(e)}") from e
    
    async def export_chunks_json(self, doc_id: int) -> List[Dict[str, Any]]:
        """导出json数据"""
        try:
            return await self.service.export_chunks_json(doc_id)
        except Exception as e:
            raise ValueError(f"导出json数据失败！{str(e)}") from e
    
    async def download_pretrain_stream(self, doc_ids: List[int]) -> AsyncGenerator[str, None]:
        """Pass-through stream"""
        return self.service.generate_pretrain_stream(doc_ids)
    
    async def download_pretrain_stream_by_kb(self, kb_ids: List[int]) -> AsyncGenerator[str, None]:
        """Business Logic: Resolve KB IDs to Doc IDs -> Stream"""
        # We need to query PDFDocument table. 
        # Ideally, there should be a PdfDocumentService, but for now we use SqlProvider directly or a service if available.
        # Assuming we can just query the table directly here via a Provider as this is read-only logic.
        doc_ids: List[int] = self.pdf_document_service.get_doc_ids_by_kb_ids(kb_ids)
            
        if not doc_ids:
            yield ""
            return

        async for chunk in self.service.generate_pretrain_stream(doc_ids):
            yield chunk