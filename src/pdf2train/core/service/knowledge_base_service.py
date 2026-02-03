#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/10 11:58
@Author  : weiyutao
@File    : knowledge_base_service.py
"""

from typing import Optional, Dict, Any, List
import logging
from sqlalchemy import select

from pdf2train.core.configs.sql_config import SqlConfig
from pdf2train.core.provider.sql_provider import SqlProvider
from pdf2train.core.schema.knowledge_base_dto import KnowledgeBaseCoreDTO, KnowledgeBaseUpdateDTO

from pdf2train.core.table.knowledge_base import KnowledgeBase

class KnowledgeBaseService:
    """
    知识库服务
    为保证service业务的纯粹性
    涉及关于knowledge_base表格的单表、多表操作均在这里
    涉及到关联其它service的操作不在这里
    """
    def __init__(self, sql_config: Optional[SqlConfig] = None):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.model = KnowledgeBase
        self.sql_provider = SqlProvider(
            model=KnowledgeBase, 
            sql_config=sql_config 
        )

    async def create(self, dto: KnowledgeBaseCoreDTO) -> int:
        """[DB] 创建记录"""
        return await self.sql_provider.add_record(dto.model_dump(exclude={'id'}))
    
    async def update(self, kb_id: int, dto: KnowledgeBaseUpdateDTO) -> bool:
        """[DB] 更新记录"""
        return await self.sql_provider.update_record(kb_id, dto.model_dump(exclude_unset=True))
    
    async def get_by_id(self, kb_id: int) -> KnowledgeBase:
        """[DB] 获取单条记录"""
        results = await self.sql_provider.get_record_by_condition({"id": kb_id})
        return results[0] if results else None

    async def get_by_name(self, name: str) -> Dict[str, Any]:
        """[DB] 获取单条记录"""
        results = await self.sql_provider.get_record_by_condition({"name": name})
        return results[0] if results else None
    
    async def search_paginated(
        self, 
        page: int, 
        page_size: int, 
        keyword: str = None
    ) -> List[KnowledgeBase]:
        """[DB] 分页列表查询"""
        condition = {}
            
        filters = []
        if keyword:
            filters.append(self.model.name.like(f"%{keyword}%"))

        return await self.sql_provider.get_records_paginated(
            page=page,
            page_size=page_size,
            condition=condition,
            filters=filters,
            order_by=self.model.create_time.desc()
        )
        
    async def get_names_by_ids(self, kb_ids: List[int]) -> Dict[int, str]:
        """
        [DB] 批量查询知识库名称 (供 PdfDocumentManager 使用)
        """
        if not kb_ids:
            return {}
        
        unique_ids = list(set(kb_ids))
        async with self.sql_provider.get_db_session() as session:
            stmt = select(self.model.id, self.model.name).where(self.model.id.in_(unique_ids))
            result = await session.execute(stmt)
            rows = result.fetchall()
            return {row[0]: row[1] for row in rows}

    async def delete(self, kb_id: int) -> bool:
        """
        物理删除单条记录
        """
        return await self.sql_provider.delete_record(kb_id, hard_delete=True)
        
