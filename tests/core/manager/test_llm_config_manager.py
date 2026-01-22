#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/20 12:31
@Author  : weiyutao
@File    : test_llm_config_manager.py
"""

import pytest
import uuid
from pdf2train.core.manager.llm_config_manager import LLMConfigManager
from pdf2train.core.service.llm_config_service import LLMConfigService
from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.api.schema.llm_config_schema import LLMConfigCreateReq
from pdf2train.core.table.llm_enum import LLMProvider, ModelType
from pdf2train.core.config import core_config
from pdf2train.core.schema.llm_config_dto import LLMConfigCoreDTO

@pytest.mark.asyncio
class TestLLMConfigManager:

    @pytest.fixture
    def manager(self, db_session):
        """初始化 Manager (连接测试库)"""
        # 初始化 Service
        llm_service = LLMConfigService(sql_config=core_config.sql_config_test)
        llm_service.sql_provider.session = db_session
        
        pdf_service = PdfDocumentService(sql_config=core_config.sql_config_test)
        pdf_service.sql_provider.session = db_session
        
        return LLMConfigManager(service=llm_service, pdf_service=pdf_service)

    def _req_to_dto(self, req: LLMConfigCreateReq) -> LLMConfigCoreDTO:
        """
        [工具方法] 模拟 Router 的行为：将 Request(Enum) 转换为 DTO(String)
        Manager 层只认 DTO，不认 Request。
        """
        return LLMConfigCoreDTO(
            name=req.name,
            model_type=req.model_type.value,  # 🔥 Enum -> String
            provider=req.provider.value,      # 🔥 Enum -> String
            model_name=req.model_name,
            api_key=req.api_key,
            base_url=req.base_url,
            is_default=req.is_default
        )

    async def test_create_logic_and_defaults(self, manager, db_session):
        """测试：Enum转换 + 默认值重置逻辑"""
        suffix = uuid.uuid4().hex[:6]

        # 1. 准备 Request (前端数据)
        req1 = LLMConfigCreateReq(
            name=f"Config-A-{suffix}",
            model_type=ModelType.LLM,       # Enum
            provider=LLMProvider.DEEPSEEK,  # Enum
            model_name="deepseek-chat",
            api_key="sk-key-A",
            is_default=True
        )
        # 🔥 关键修正：先转 DTO，再传 Manager
        dto1 = self._req_to_dto(req1)
        id1 = await manager.create_config(dto1)
        assert id1 > 0

        # 2. 创建第二个默认配置
        req2 = LLMConfigCreateReq(
            name=f"Config-B-{suffix}",
            model_type=ModelType.LLM,
            provider=LLMProvider.OPENAI,
            model_name="gpt-4",
            api_key="sk-key-B",
            is_default=True
        )
        # 🔥 关键修正：先转 DTO
        dto2 = self._req_to_dto(req2)
        id2 = await manager.create_config(dto2)
        assert id2 > 0
        
        # 3. 验证数据库状态
        # 获取记录 (Service返回的是 ORM 对象，属性也是 String)
        record_a = await manager.service.get_by_id(id1)
        assert record_a.is_default is False
        
        record_b = await manager.service.get_by_id(id2)
        assert record_b.is_default is True
        assert record_b.model_type == "llm"       # 库里存的是 String
        assert record_b.provider == "OpenAI"      # 库里存的是 String
        
        print("[Success] Logic Verified")

    async def test_mask_sensitive_info(self, manager, db_session):
        """测试：获取详情时的脱敏逻辑"""
        suffix = uuid.uuid4().hex[:6]
        
        req = LLMConfigCreateReq(
            name=f"Sensitive-{suffix}",
            model_type=ModelType.LLM,
            provider=LLMProvider.DEEPSEEK,
            model_name="deepseek-reasoner",
            api_key="sk-1234567890abcdef", 
            is_default=True
        )
        # 🔥 转 DTO
        await manager.create_config(self._req_to_dto(req))
        
        # 获取 (Manager 内部只处理 String)
        result = await manager.get_active_config(model_type="llm")
        
        assert result is not None
        assert "****" in result["api_key"]