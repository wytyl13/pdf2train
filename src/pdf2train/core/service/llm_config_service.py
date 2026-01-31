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
from pdf2train.core.schema.qdrant_dto import EmbeddingConfigOverride

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

    async def get_names_by_ids(self, ids: List[int]) -> Dict[int, str]:
        """
        [DB] 批量根据 ID 获取配置名称
        用于解决列表查询时的 N+1 问题
        :param ids: ID 列表 [1, 2, 3]
        :return: {1: "DeepSeek-V3", 2: "Aliyun-Embedding"}
        """
        # 1. 边界条件判断：如果列表为空，直接返回空字典，避免无效数据库查询
        if not ids:
            return {}

        # 2. 使用 Session 执行查询
        async with self.sql_provider.get_db_session() as session:
            # 优化：只查询 id 和 name 两个字段，减少数据传输量
            stmt = select(self.model.id, self.model.name).where(self.model.id.in_(ids))
            result = await session.execute(stmt)
            
            # 3. 将结果转换为字典 {id: name}
            # result.all() 返回的是 Row 对象列表，可以直接通过属性访问
            return {row.id: row.name for row in result.all()}

    async def get_embedding_config_override(self, doc_id: int) -> EmbeddingConfigOverride:
        """
        [业务] 获取指定文档的 Embedding 配置覆写对象
        """
        # 1. 直接获取配置对象 (传入新的字段名 _id)
        db_config: LLMConfig = await self.get_config_by_doc_id(doc_id, "embedding_llm_config_id")
        
        # 2. 组装 Override 对象
        return EmbeddingConfigOverride(
            base_url=db_config.base_url,
            api_key=db_config.api_key,
            model_name=db_config.model_name
        )

    async def get_config_by_doc_id(self, doc_id: int, field_llm_id_name: str) -> LLMConfig:
        """
        [DB] 通用方法：根据文档ID和指定的配置ID字段名，获取完整的 LLMConfig 对象
        :param doc_id: 文档 ID
        :param field_llm_id_name: PdfDocument 表中的字段名 (如 'embedding_llm_config_id')
        """
        # 1. 从 PdfDocument 表中查询配置 ID
        async with self.sql_provider.get_db_session() as session:
            # 动态获取列对象 (例如 PdfDocument.embedding_llm_config_id)
            target_column = getattr(PdfDocument, field_llm_id_name, None)
            if target_column is None:
                raise AttributeError(f"PdfDocument 模型中不存在字段: {field_llm_id_name}")

            # SELECT embedding_llm_config_id FROM pdf_document WHERE id = :doc_id
            stmt = select(target_column).where(PdfDocument.id == doc_id)
            result = await session.execute(stmt)
            config_id = result.scalar()

        # 2. 校验 ID 是否存在
        if not config_id:
            raise ValueError(f"文档 (ID={doc_id}) 未关联 {field_llm_id_name}，无法获取配置！")

        # 3. 根据 ID 查询配置详情 (复用 get_by_id)
        config = await self.get_by_id(config_id)
        
        if not config:
            raise ValueError(f"关联的配置 (ID={config_id}) 在配置表中已不存在！")
            
        return config

    async def get_collection_name_by_doc_id(self, doc_id: int) -> str:
        """
        [DB] 根据文档ID获取对应的 Qdrant Collection Name
        
        优化后原理: 
        1. 查 pdf_document 拿到 embedding_llm_config_id
        2. 查 sys_llm_configs 拿到 model_name
        3. 也就是直接复用 get_config_by_doc_id 即可
        """
        # 直接复用上面的方法，获取完整的配置对象
        try:
            config = await self.get_config_by_doc_id(doc_id, "embedding_llm_config_id")
            # Qdrant Collection Name 严格等于配置表中的 model_name (物理模型名)
            return config.model_name
        except ValueError as e:
            # 捕获异常并记录，或者抛出更具体的业务异常
            self.logger.error(f"无法获取 Collection Name: {e}")
            raise e

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

    async def get_active_config_id(self, model_type: str = ModelType.LLM.value) -> Optional[int]:
        """
        [DB] 获取当前激活的默认配置名称
        直接复用 get_active_config 的逻辑，避免重复查询代码
        """
        # 1. 内部调用上面的方法
        config = await self.get_active_config(model_type)
        # 2. 提取名称 (ORM 对象直接用 .name)
        return config.id if config else None

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