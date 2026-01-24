#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/19 17:25
@Author  : weiyutao
@File    : llm_config_manager.py
"""

import logging
from typing import Dict, Any, List, Optional

# 引入下层依赖
from pdf2train.core.table.llm_enum import LLMProvider, ModelType
from pdf2train.core.table.llm_config import LLMConfig
from pdf2train.core.service.llm_config_service import LLMConfigService
from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.core.schema.llm_config_dto import LLMConfigCoreDTO, LLMConfigUpdateDTO, ResetDefaulExceptDTO

class LLMConfigManager:
    def __init__(self, service: LLMConfigService, pdf_service: PdfDocumentService):
        self.service = service
        self.pdf_service = pdf_service
        self.logger = logging.getLogger(self.__class__.__name__)

    # ================= 内部工具 =================

    def _mask_sensitive_info(self, item: Any) -> Dict[str, Any]:
        """
        [工具] 数据脱敏
        将 ORM 对象或字典中的 api_key 进行掩码处理
        如果处理过程中ORM对象消失，则会返回空字典
        """
        data = item if isinstance(item, dict) else {k: v for k, v in item.__dict__.items() if not k.startswith('_')}
        
        api_key = data.get("api_key", "")
        if api_key and len(api_key) > 8:
            data["api_key"] = f"{api_key[:3]}****{api_key[-4:]}"
        elif api_key:
            data["api_key"] = "******"
        return data

    # ================= 业务接口 =================

    async def get_provider_list(self) -> List[str]:
        return [p.value for p in LLMProvider]

    async def get_model_type_list(self) -> List[str]:
        return [t.value for t in ModelType]

    async def create_config(self, dto: LLMConfigCoreDTO) -> int:
        """
        [入参] dto: Core DTO (纯字符串)
        Manager 不需要知道前端传的是 Enum，它只处理已经标准化的数据。
        """
        # 1. 业务逻辑
        # 因为 DTO 里是 String，这里直接传给 Service 即可
        config_id = await self.service.create(dto)
        if dto.is_default:
            reset_ = ResetDefaulExceptDTO(model_type=dto.model_type, exclude_id=config_id)
            await self.service.reset_defaults_except(reset_)

        # 2. 调用 Service
        return config_id

    async def update_config(self, config_id: int, dto: LLMConfigUpdateDTO) -> bool:
        """
        [入参] dto: Core DTO (纯字符串)
        """
        # 1. 业务逻辑
        if dto.is_default:
            # 如果 DTO 里有 type，直接用；没有则查库
            if dto.model_type:
                target_type = dto.model_type
            else:
                existing = await self.service.get_by_id(config_id)
                target_type = existing.model_type
            reset_ = ResetDefaulExceptDTO(model_type=target_type, exclude_id=config_id)
            await self.service.reset_defaults_except(reset_)

        # 2. 调用 Service
        return await self.service.update(config_id, dto)

    async def delete_config(self, config_id: int) -> bool:
        return await self.service.delete(config_id)

    async def get_config_list(
        self, 
        page: int, 
        page_size: int, 
        model_type: Optional[ModelType] = None
    ) -> Dict[str, List[LLMConfig] | int]:
        """
        [业务] 获取列表 (含脱敏)
        """
        condition = {"model_type": model_type.value} if model_type else {}
        result = await self.service.search_paginated(page, page_size, condition)
        
        # 对结果集进行脱敏
        items = result.get("items", [])
        result["items"] = [self._mask_sensitive_info(item) for item in items]
        return result

    async def get_active_config(self, model_type: str) -> Optional[Dict[str, Any]]:
        """
        [业务] 获取当前激活的默认配置
        Router 传进来的是 String (m_type)
        """
        # 1. 调用 Service (Service 需要新增 get_active_config 方法，或者复用 get_records)
        # 建议直接在 Service 复用 get_record_by_condition
        record = await self.service.get_active_config(model_type)
        if not record:
            return None
        # 2. 脱敏返回
        return self._mask_sensitive_info(record)

    async def get_config_by_doc_id(self, doc_id: int, field_llm_name: str) -> Optional[Dict[str, Any]]:
        """
        [业务] 跨服务查询
        1. 调 PdfDocumentService 查文档信息
        2. 拿配置名
        3. 调 LLMConfigService 查配置详情
        4. 脱敏返回
        """
        doc = await self.pdf_service.get_by_id(doc_id)
        if not doc:
            return None
        
        # 动态获取字段值 (例如 embedding_llm_config)
        config_identifier = getattr(doc, field_llm_name, None)
        if not config_identifier:
            return None
            
        config = await self.service.find_by_name_or_model(config_identifier)
        return self._mask_sensitive_info(config) if config else None