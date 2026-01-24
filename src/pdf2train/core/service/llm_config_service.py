#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/30 11:35
@Author  : weiyutao
@File    : llm_config_service.py
"""


import logging
from typing import Dict, Any, List, Optional
from sqlalchemy import select, update, or_
from pdf2train.core.table.llm_config import LLMConfig
from pdf2train.core.table.llm_enum import ModelType
from pdf2train.core.table.pdf_document import PdfDocument

from pdf2train.core.configs.sql_config import SqlConfig
from pdf2train.core.provider.sql_provider import SqlProvider
from pdf2train.core.schema.llm_config_dto import LLMConfigCoreDTO, LLMConfigUpdateDTO, ResetDefaulExceptDTO

class LLMConfigService:
    def __init__(
        self, 
        sql_config: Optional[SqlConfig] = None
    ):
        self.model=LLMConfig
        self.sql_config = sql_config
        # 内部持有 provider，避免每次调用都重新建立连接
        self.sql_provider = SqlProvider(
            model=LLMConfig, 
            sql_config=self.sql_config 
        )
        self.logger = logging.getLogger(self.__class__.__name__)

    async def create(self, dto: LLMConfigCoreDTO) -> int:
        """[DB] 创建记录"""
        # 将 DTO 转为字典存入数据库
        return await self.sql_provider.add_record(dto.model_dump())

    async def update(self, config_id: int, dto: LLMConfigUpdateDTO) -> bool:
        """[DB] 更新记录"""
        # exclude_unset=True 确保只更新 DTO 中被赋值的字段
        data = dto.model_dump(exclude_unset=True)
        return await self.sql_provider.update_record(config_id, data)

    async def delete(self, config_id: int) -> bool:
        """[DB] 物理删除"""
        return await self.sql_provider.delete_record(config_id, hard_delete=True)

    async def get_by_id(self, config_id: int) -> Optional[LLMConfig]:
        """[DB] 按 ID 查询"""
        results = await self.sql_provider.get_record_by_condition({"id": config_id})
        return results[0] if results else None

    async def get_config_by_doc_id(self, doc_id: int, field_llm_name: str) -> Optional[LLMConfig]:
        try:
            doc_provider = SqlProvider(
                model=PdfDocument, 
                sql_config=self.sql_config 
            )
            # 2. 查询文档记录
            docs = await doc_provider.get_record_by_condition({"id": doc_id})
            if not docs:
                self.logger.warning(f"未找到 ID 为 {doc_id} 的文档")
                return None
            doc = docs[0]

            # 3. 动态获取字段值 (该值应为 LLMConfig 的 name，例如 "gpt-4-dev")
            # 检查字段是否存在于模型中
            if not hasattr(doc, field_llm_name):
                self.logger.error(f"PdfDocument 模型中不存在字段: {field_llm_name}")
                return None
            
            # 获取字段的值
            config_name = getattr(doc, field_llm_name)
            # 4. 如果文档中该字段为空 (None 或 "")，直接返回 None
            if not config_name:
                return None

            # 5. 复用现有的方法，根据名称查找完整的配置
            return await self.find_by_name_or_model(config_name)
        except Exception as e:
            # 捕获所有未预期的异常 (如数据库连接断开、SQL错误等)
            import traceback
            self.logger.error(f"根据文档获取LLM配置发生异常 (doc_id={doc_id}, field={field_llm_name}): {e}")
            self.logger.error(traceback.format_exc())
            return None

    async def search_paginated(self, page: int, page_size: int, condition: Dict[str, Any]):
        """[DB] 分页查询，默认将 is_default=True 的排在前面"""
        return await self.sql_provider.get_records_paginated(
            page=page,
            page_size=page_size,
            condition=condition,
            order_by=self.model.is_default.desc()
        )

    async def reset_defaults_except(self, dto: ResetDefaulExceptDTO):
        """
        [DB] 批量重置默认状态 (Atomic Operation)
        将指定类型下，除 exclude_id 以外的所有记录 is_default 设为 False
        """
        async with self.sql_provider.get_db_session() as session:
            stmt = update(self.model).where(
                self.model.model_type == dto.model_type,
                self.model.is_default == True
            )
            if dto.exclude_id is not None:
                stmt = stmt.where(self.model.id != dto.exclude_id)
            
            stmt = stmt.values(is_default=False)
            await session.execute(stmt)
            # 注意：如果是手动管理的 session，需要 commit，SqlProvider 上下文管理器通常会自动 commit

    async def find_by_name_or_model(self, identifier: str) -> Optional[LLMConfig]:
        """[DB] 根据 [配置别名] 或 [物理模型名] 查找记录"""
        async with self.sql_provider.get_db_session() as session:
            stmt = select(self.model).where(
                or_(
                    self.model.name == identifier,
                    self.model.model_name == identifier
                )
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()
    
    async def get_active_config(self, model_type: str) -> Optional[LLMConfig]:
        """
        [DB] 获取当前激活的默认配置
        """
        condition = {
            "is_default": True, 
            "model_type": model_type
        }
        # 直接复用 provider 的查询能力
        results = await self.sql_provider.get_record_by_condition(condition)
        return results[0] if results else None

    async def get_active_config_name(self, model_type: str = ModelType.LLM.value) -> Optional[str]:
        """
        [DB] 获取当前激活的默认配置名称
        直接复用 get_active_config 的逻辑，避免重复查询代码
        """
        # 1. 内部调用上面的方法
        config = await self.get_active_config(model_type)
        
        # 2. 提取名称 (ORM 对象直接用 .name)
        return config.name if config else None

    async def get_real_model_name(self, identifier: str) -> Optional[str]:
        """
        [DB] 查找真实模型名称
        逻辑：
        1. 检查 identifier 是否直接就是 model_name
        2. 检查 identifier 是否是配置名称 (name)，如果是，返回其对应的 model_name
        """
        async with self.sql_provider.get_db_session() as session:
            # 1. 第一步：尝试匹配 model_name
            # SELECT * FROM config WHERE model_name = identifier
            stmt_1 = select(self.model).where(self.model.model_name == identifier)
            result_1 = await session.execute(stmt_1)
            if result_1.scalar_one_or_none():
                return identifier

            # 2. 第二步：尝试匹配 name (配置别名)
            # SELECT * FROM config WHERE name = identifier
            stmt_2 = select(self.model).where(self.model.name == identifier)
            result_2 = await session.execute(stmt_2)
            record = result_2.scalar_one_or_none()
            
            if record:
                return record.model_name

            # 3. 没找到
            return None