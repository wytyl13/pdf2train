#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/29 09:47
@Author  : weiyutao
@File    : instruction_datum_service.py
"""


from typing import List, Dict, Any, Optional
from sqlalchemy import select, func, or_, delete, update
from sqlalchemy.ext.asyncio import AsyncSession
import logging


from pdf2train.core.configs.sql_config import SqlConfig
from pdf2train.core.provider.sql_provider import SqlProvider
from pdf2train.core.table.instruction_datum import InstructionDatum
from pdf2train.core.schema.instruction_datum_dto import (
    InstructionDatumCoreDTO, 
    InstructionDatumUpdateDTO, 
    InstructionDatumFilterDTO
)



class InstructionDatumService:
    """
    instruction datum service
    """
    def __init__(self, sql_config: Optional[SqlConfig] = None):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.model=InstructionDatum
        self.sql_config = sql_config
        self.sql_provider = SqlProvider(
            model=self.model,
            sql_config=self.sql_config 
        )
        
    async def create_batch(self, dtos: List[InstructionDatumCoreDTO]) -> int:
        """批量创建"""
        data_list = [dto.model_dump() for dto in dtos]
        return await self.sql_provider.batch_create(data_list)
    
    async def update(self, instruction_id: str, dto: InstructionDatumUpdateDTO) -> bool:
        """更新"""
        # exclude_unset=True 保证只更新 DTO 中显式赋值的字段
        data = dto.model_dump(exclude_unset=True)
        return await self.sql_provider.update_record(instruction_id, data)
    
    async def delete(self, instruction_id: str) -> bool:
        return await self.sql_provider.delete_record(record_id=instruction_id, hard_delete=True)
    
    async def delete_by_doc_id(self, doc_id: int) -> int:
        condition = {"doc_id": doc_id}
        return await self.sql_provider.delete_records_by_condition(condition)
    
    async def get_by_id(self, instruction_id: str) -> bool:
        results: List[InstructionDatum] = await self.sql_provider.get_record_by_condition({"id": instruction_id})
        return results[0] if results else None
    
    async def get_by_doc_id(self, doc_id: int) -> List[InstructionDatum]:
        return await self.sql_provider.get_record_by_condition({"doc_id": doc_id})
    
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
                    select(self.model.doc_id, func.count(self.model.id))
                    .where(self.model.doc_id.in_(doc_ids))
                    .group_by(self.model.doc_id)
                )
                
                # 3. 执行查询
                result = await session.execute(stmt)
                
                # 4. 转换结果为字典 {doc_id: count}
                return dict(result.all())
                
            except Exception as e:
                self.logger.error(f"批量统计失败: {e}")
                # 如果 get_db_session 内部没有吞掉异常，这里可以 raise，也可以返回空字典
                return {}
    
    async def get_all_instruction_doc_ids(self) -> List[int]:
        """
        [辅助方法] 获取所有包含指令数据的文档 ID (去重)
        """
        try:
            records: List[InstructionDatum] = await self.sql_provider.get_record_by_condition(condition={}, fields=["doc_id"])
            
            # 提取并去重
            doc_ids = set()
            for r in records:
                # 兼容字典或对象访问
                did = r.doc_id
                if did:
                    doc_ids.add(int(did))
            
            return list(doc_ids)
        except Exception as e:
            self.logger.error(f"获取文档ID列表失败: {e}")
            return []
    
    async def get_valid_by_doc_id(self, doc_id: int) -> List[InstructionDatum]:
        """
        获取某个doc_id下的所有有效的指令数据
        有效定义: is_valid 为 0 (待审核) 或 1 (有效)
        """
        # 利用 filters 参数传入 SQL 的 IN 查询条件
        return await self.sql_provider.get_record_by_condition(
            condition={"doc_id": doc_id},
            filters=[self.model.is_valid.in_([0, 1])]
        )
    
    async def search_paginated(
        self, 
        filter_dto: InstructionDatumFilterDTO, 
        page: int, 
        page_size: int
    ) -> Dict[str, List[InstructionDatum] | int]:
        """分页查询 (支持通过 doc 关联查询 kb_id)"""
        condition = {"doc_id": filter_dto.doc_id}
        if filter_dto.is_valid:
            condition["is_valid"] = filter_dto.is_valid
        if filter_dto.type:
            condition["type"] = filter_dto.type
        
        filters = []
        if filter_dto.keyword:
            filters.append(or_(
                InstructionDatum.system_prompt.like(filter_dto.keyword),
                InstructionDatum.question.like(filter_dto.keyword),
                InstructionDatum.answer.like(filter_dto.keyword),
                InstructionDatum.chain_of_thought.like(filter_dto.keyword)
            ))

        # Return (items, total)
        return await self.sql_provider.get_records_paginated(
            page=page,
            page_size=page_size,
            condition=condition,
            filters=filters,
            order_by=InstructionDatum.create_time.asc()
        )
        

    
    
    