#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/27 22:15
@Author  : weiyutao
@File    : embedding_sql_service.py
"""

from typing import List, Optional
from sqlalchemy import update
from datetime import datetime
import logging
from pdf2train.core.configs.sql_config import SqlConfig

from pdf2train.core.provider.sql_provider import SqlProvider
from pdf2train.core.table.document_chunk import DocumentChunk
from pdf2train.core.table.instruction_datum import InstructionDatum

class EmbeddingSqlService:
    def __init__(self, sql_config: Optional[SqlConfig] = None):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.model=DocumentChunk
        self.sql_config = sql_config
        self.sql_provider = SqlProvider(
            model=self.model,
            sql_config=self.sql_config 
        )

    async def mark_chunks_as_indexed(self, chunk_ids: List[str]) -> None:
        """
        [原子操作] 批量标记 Chunk/Instruction 为已索引
        """
        if not chunk_ids: raise ValueError("chunk_id must not be null!")

        # 1. Update DocumentChunk
        stmt_chunk = update(DocumentChunk).where(
            DocumentChunk.id.in_(chunk_ids)
        ).values(
            is_indexed=True,
            qdrant_point_id=DocumentChunk.id 
        )

        # 2. Update InstructionDatum
        stmt_inst = update(InstructionDatum).where(
            InstructionDatum.id.in_(chunk_ids)
        ).values(
            is_indexed=True,
            qdrant_point_id=InstructionDatum.id,
            update_time=datetime.now()
        )

        async with self.sql_provider.get_db_session() as session:
            await session.execute(stmt_chunk)
            await session.execute(stmt_inst)
            await session.commit()