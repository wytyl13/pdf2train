#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/24 13:26
@Author  : weiyutao
@File    : document_chunk_service.py
"""



import logging
import json
from typing import List, Optional, Tuple, AsyncGenerator, Dict
from sqlalchemy import text, select, desc, asc, func

from pdf2train.core.provider.sql_provider import SqlProvider
from pdf2train.core.configs.sql_config import SqlConfig
from pdf2train.core.table.document_chunk import DocumentChunk
from pdf2train.core.schema.document_chunk_dto import DocumentChunkCoreDTO, DocumentChunkUpdateDTO, DocumentChunkFilterDTO

class DocumentChunkService:
    def __init__(self, sql_config: Optional[SqlConfig] = None):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.model=DocumentChunk
        self.sql_config = sql_config
        # 内部持有 provider，避免每次调用都重新建立连接
        self.sql_provider = SqlProvider(
            model=self.model,
            sql_config=self.sql_config 
        )

    async def create_batch(self, dtos: List[DocumentChunkCoreDTO]) -> int:
        """Batch insert chunks"""
        data_list = [dto.model_dump() for dto in dtos]
        return await self.sql_provider.batch_create(data_list)

    async def update(self, chunk_id: str, dto: DocumentChunkUpdateDTO) -> bool:
        """Update a specific chunk"""
        data = dto.model_dump(exclude_unset=True)
        return await self.sql_provider.update_record(chunk_id, data)

    async def delete(self, chunk_id: str) -> bool:
        """Delete a single chunk"""
        return await self.sql_provider.delete_record(record_id=chunk_id, hard_delete=True)

    async def delete_by_doc_id(self, doc_id: int) -> int:
        """Delete all chunks for a document"""
        condition = {"document_id": doc_id}
        return await self.sql_provider.delete_records_by_condition(condition)

    async def get_by_id(self, chunk_id: str) -> Optional[DocumentChunk]:
        """Get single chunk instance"""
        results: List[DocumentChunk] = await self.sql_provider.get_record_by_condition({"id": chunk_id})
        return results[0] if results else None

    async def search_paginated(
        self, 
        filter_dto: DocumentChunkFilterDTO, 
        page: int, 
        page_size: int
    ) -> Dict[str, List[DocumentChunk] | int]:
        """
        Get paginated list of DocumentChunk instances
        Returns: (List[DocumentChunk], total_count)
        """
        condition = {"document_id": filter_dto.document_id}
        if filter_dto.id:
            condition["id"] = filter_dto.id
        
        filters = []
        if filter_dto.keyword:
            filters.append(DocumentChunk.content.like(f"%{filter_dto.keyword}%"))

        # Return (items, total)
        return await self.sql_provider.get_records_paginated(
            page=page,
            page_size=page_size,
            condition=condition,
            filters=filters,
            order_by=DocumentChunk.chunk_index.asc()
        )

    async def get_all_by_doc_id(self, doc_id: int) -> List[DocumentChunk]:
        """Get all chunks for export (Non-paginated)"""
        condition = {"document_id": doc_id}
        return await self.sql_provider.get_record_by_condition(
            condition=condition
        )

    async def get_counts_by_doc_ids(self, doc_ids: List[int]) -> Dict[int, int]:
        """
        批量统计文档的 instruction 数量
        """
        if not doc_ids:
            return {}

        # 1. 使用 async with 获取上下文管理的 session
        async with self.sql_provider.get_db_session() as session:
            try:
                # 2. 构建查询语句
                stmt = (
                    select(self.model.document_id, func.count(self.model.id))
                    .where(self.model.document_id.in_(doc_ids))
                    .group_by(self.model.document_id)
                )
                
                # 3. 执行查询
                result = await session.execute(stmt)
                
                # 4. 转换结果为字典 {doc_id: count}
                return dict(result.all())
                
            except Exception as e:
                self.logger.error(f"批量统计失败: {e}")
                # 如果 get_db_session 内部没有吞掉异常，这里可以 raise，也可以返回空字典
                return {}
    
    async def generate_pretrain_stream(self, doc_ids: List[int]) -> AsyncGenerator[str, None]:
        """Stream generator for pretrain data"""
        sql_provider = SqlProvider(model=DocumentChunk)
        stmt = text("SELECT content, meta_info FROM document_chunks WHERE document_id = :doc_id ORDER BY chunk_index ASC")
        for doc_id in doc_ids:
            async with sql_provider.get_db_session() as session:
                result = await session.execute(stmt, {"doc_id": doc_id})
                # Yielding raw strings/jsonl lines
                rows = result.fetchall()
                if not rows: continue
                
                full_text = "\n\n".join([r[0] for r in rows if r[0]])
                # Use metadata from first chunk if available
                meta = rows[0][1] if rows[0][1] else {}
                
                entry = {"text": full_text, "meta": meta}
                yield json.dumps(entry, ensure_ascii=False) + "\n"