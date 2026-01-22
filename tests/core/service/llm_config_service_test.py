#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/20 12:30
@Author  : weiyutao
@File    : llm_config_service_test.py
"""

import pytest
import uuid
from pdf2train.core.service.llm_config_service import LLMConfigService
from pdf2train.core.schema.llm_config_dto import LLMConfigCoreDTO, LLMConfigUpdateDTO
from pdf2train.core.config import core_config

@pytest.mark.asyncio
class TestLLMConfigService:

    @pytest.fixture
    def service(self, db_session):
        """
        初始化 Service，并注入测试配置和 Session
        """
        svc = LLMConfigService(sql_config=core_config.sql_config_test)
        # 强制使用 pytest fixture 提供的 session (通常包含事务回滚机制)
        svc.sql_provider.session = db_session
        return svc

    async def test_lifecycle(self, service, db_session):
        """
        测试：创建 -> 获取 -> 默认配置查询 -> 更新
        """
        random_suffix = uuid.uuid4().hex[:6]
        
        # =======================================
        # 1. 测试 Create (使用 CoreDTO)
        # =======================================
        dto = LLMConfigCoreDTO(
            name=f"DeepSeek-Service-{random_suffix}",
            model_type="llm",             # String
            provider="DeepSeek",          # String
            model_name="deepseek-chat",
            api_key="sk-service-test-key",
            base_url="https://api.deepseek.com",
            is_default=True
        )

        config_id = await service.create(dto)
        assert config_id > 0
        print(f"\n[Success] Created Config ID: {config_id}")

        # =======================================
        # 2. 测试 Get By ID
        # =======================================
        saved_record = await service.get_by_id(config_id)
        assert saved_record is not None
        assert saved_record.name == f"DeepSeek-Service-{random_suffix}"
        assert saved_record.provider == "DeepSeek"
        print("[Success] Get By ID Verified")

        # =======================================
        # 3. 测试 Get Active Config (默认配置)
        # =======================================
        active_config = await service.get_active_config(model_type="llm")
        assert active_config is not None
        assert active_config.id == config_id
        assert active_config.is_default is True
        print("[Success] Active Config Verified")

        # =======================================
        # 4. 测试 Update
        # =======================================
        update_dto = LLMConfigUpdateDTO(
            name=f"DeepSeek-Updated-{random_suffix}",
            api_key="sk-new-key"
        )
        success = await service.update(config_id, update_dto)
        assert success is True
        
        updated_record = await service.get_by_id(config_id)
        assert updated_record.name == f"DeepSeek-Updated-{random_suffix}"
        assert updated_record.api_key == "sk-new-key"
        assert updated_record.model_name == "deepseek-chat" # 未修改字段保持原样
        print("[Success] Update Verified")