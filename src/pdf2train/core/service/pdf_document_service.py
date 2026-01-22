#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/17 12:46
@Author  : weiyutao
@File    : pdf_document_service.py
"""

import logging
from fastapi import BackgroundTasks
from datetime import datetime
from typing import Dict, Any, List, Optional, Union, Tuple
from pathlib import Path
from dotenv import dotenv_values
from sqlalchemy import select, func, and_, or_, text, update

from pdf2train.core.configs.sql_config import SqlConfig
from pdf2train.core.provider.sql_provider import SqlProvider
from pdf2train.core.table.pdf_document import PdfDocument
from pdf2train.core.table.pipeline_task import PipelineTask, ChunkTaskResult
from pdf2train.core.table.knowledge_base import KnowledgeBase

from pdf2train.core.schema.pdf_document_dto import PdfDocCoreDTO, PdfDocUpdateDTO, PdfDocFilterDTO

class PdfDocumentService:
    """
    PDF 文档业务服务
    为保证service业务的纯粹性
    涉及关于pdf_document表格的单表、多表操作均在这里
    涉及到关联其它service的操作不在这里
    """
    def __init__(
        self, 
        sql_config: Optional[SqlConfig] = None
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.model = PdfDocument
        self.sql_provider = SqlProvider(
            model=PdfDocument, 
            sql_config=sql_config 
        )

    async def get_by_id(self, doc_id: int) -> Optional[PdfDocument]:
        """
        根据 ID 获取文档对象
        """
        result = await self.sql_provider.get_record_by_condition({"id": doc_id})
        return result[0] if result else None

    async def get_by_hash(self, file_hash: str) -> Optional[PdfDocument]:
        """
        根据文件 Hash 获取文档 (用于判重)
        """
        result = await self.sql_provider.get_record_by_condition({"file_hash": file_hash})
        return result[0] if result else None
    
    async def get_with_relations(self, doc_id: int, relations: List[str] = None) -> Optional[PdfDocument]:
        """
        获取文档并预加载关联数据 (如 tasks)
        """
        if not relations:
            relations = ["tasks"]
        return await self.sql_provider.get_with_relations(doc_id, relations=relations)
    
    async def create(self, doc_dto: PdfDocCoreDTO) -> int:
        """
        创建文档记录
        :param data: 包含数据库字段的字典
        :return: 新增记录的 ID
        """
        data = doc_dto.model_dump(exclude={'id'}, exclude_none=True)
        return await self.sql_provider.add_record(data)
    
    async def update(self, doc_id: int, update_dto: PdfDocUpdateDTO) -> bool:
        """
        更新文档记录
        """
        data = update_dto.model_dump(exclude_unset=True)
        return await self.sql_provider.update_record(record_id=doc_id, data=data)
    
    async def delete(self, doc_id: int, hard_delete: bool = True) -> bool:
        """
        删除文档记录
        注意：这只是删除数据库记录。文件清理逻辑在 Manager 层。
        """
        return await self.sql_provider.delete_record(record_id=doc_id, hard_delete=hard_delete)
    
    async def search_paginated(
        self, 
        page: Optional[int] = None, 
        page_size: Optional[int] = None, 
        filter_dto: Optional[PdfDocFilterDTO] = None
    ) -> Tuple[List[PdfDocument], int]:
        """
        分页查询 (支持复杂筛选)
        控制参分离
        业务参 DTO
        """
        complex_filters = []

        # 1. 知识库筛选
        if filter_dto.kb_id is not None:
            if isinstance(filter_dto.kb_id, list):
                if filter_dto.kb_id:
                    complex_filters.append(PdfDocument.kb_id.in_(filter_dto.kb_id))
                else:
                    complex_filters.append(text("1=0"))
            else:
                complex_filters.append(PdfDocument.kb_id == filter_dto.kb_id)

        # 2. status过滤
        if filter_dto.status is not None:
            if isinstance(filter_dto.status, list):
                if filter_dto.status:
                    complex_filters.append(PdfDocument.status.in_(filter_dto.status))
                else:
                    complex_filters.append(text("1=0"))
            else:
                complex_filters.append(PdfDocument.status == filter_dto.status)
        
        # 2. 关键词筛选
        if filter_dto.keyword:
            complex_filters.append(or_(
                PdfDocument.file_name.like(f"%{filter_dto.keyword}%"),
                PdfDocument.author.like(f"%{filter_dto.keyword}%")
            ))

        # 3. 任务状态筛选
        if filter_dto.filter_step_type is not None:
            task_conditions = [PipelineTask.task_type == filter_dto.filter_step_type]
            
            if filter_dto.filter_step_status is not None:
                if isinstance(filter_dto.filter_step_status, list):
                    task_conditions.append(PipelineTask.status.in_(filter_dto.filter_step_status))
                else:
                    task_conditions.append(PipelineTask.status == filter_dto.filter_step_status)
            
            complex_filters.append(PdfDocument.tasks.any(and_(*task_conditions)))

        # 4. 执行查询
        result = await self.sql_provider.get_records_paginated(
            page=page,
            page_size=page_size,
            condition={}, 
            filters=complex_filters,
            order_by=PdfDocument.create_time.desc()
        )

        return result["items"], result["total"]
    
    async def update_kb_by_ids(self, ids: List[int], new_kb_id: Optional[Union[int, None]] = None):
        try:
            result = None
            stmt = (
                update(self.model)
                .where(self.model.id.in_(ids))
                .values(
                    kb_id=new_kb_id,
                    update_time=datetime.now()
                )
            )
            async with self.sql_provider.get_db_session() as session:
                result = await session.execute(stmt)
        except Exception as e:
            self.logger.error("更新失败！")
            raise
        return result
    
    async def get_stats_group_by_status(self) -> Dict[int, int]:
        """获取文档状态分布"""
        async with self.sql_provider.get_db_session() as session:
            stmt = select(PdfDocument.status, func.count(PdfDocument.id)).group_by(PdfDocument.status)
            res = await session.execute(stmt)
            return {row[0]: row[1] for row in res.all()}
        
    async def get_recent_docs(self, limit: int) -> List[PdfDocument]:
        """获取最近更新的文档"""
        data = await self.sql_provider.get_records_paginated(
            page=1, page_size=limit, order_by=PdfDocument.update_time.desc()
        )
        return data.get("items", [])
    
    async def get_chunk_count(self, doc_id: int) -> str:
        """获取文档关联的 Markdown 内容"""
        try:
            # 1. 一次查询，带出 tasks 关系
            # 返回的是 PdfDocument 对象，不是字典
            doc: PdfDocument = await self.sql_provider.get_with_relations(doc_id, relations=["tasks"])
            
            if not doc:
                raise ValueError(f"文档 ID {doc_id} 不存在")
            
            # 2. 直接调用 Model 层的智能属性
            # 因为 tasks 已经预加载了，这里是在内存中计算，无需再次查库
            result: ChunkTaskResult = doc.latest_chunk_result
            if not result:
                raise ValueError("未找到已完成的解析结果 (Json Path)")
            
            # 3. 读 MinIO (bucket_name 也是直接从对象取)
            return result.chunk_count
        except Exception as e:
            raise e
        
    async def get_doc_count_by_kb_id(self, kb_id: int) -> int:
        """
        根据知识库ID统计文档数量
        """
        try:
            async with self.sql_provider.get_db_session() as session:
                # 构建查询语句: SELECT count(id) FROM pdf_document WHERE kb_id = :kb_id
                stmt = select(func.count(PdfDocument.id)).where(PdfDocument.kb_id == kb_id)
                
                # 执行查询
                result = await session.execute(stmt)
                
                # 获取结果 scalar() 返回第一行第一列的值，即 count 数值
                return result.scalar() or 0
        except Exception as e:
            raise e
    


    