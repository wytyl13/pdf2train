#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 00:10
@Author  : weiyutao
@File    : test_knowledge_base_manager.py
"""

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 10:35
@Author  : weiyutao
@File    : test_knowledge_base_manager.py
"""

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock

from pdf2train.core.manager.knowledge_base_manager import KnowledgeBaseManager
from pdf2train.core.service.knowledge_base_service import KnowledgeBaseService
from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.core.service.qdrant_service import QdrantService
from pdf2train.core.service.llm_config_service import LLMConfigService

from pdf2train.core.schema.knowledge_base_dto import KnowledgeBaseCoreDTO
from pdf2train.core.schema.retrieval_dto import RetrievalSettings
from pdf2train.core.config import core_config

@pytest.mark.asyncio
class TestKnowledgeBaseManager:

    @pytest.fixture
    def manager(self, db_session):
        """
        初始化 Manager，连接测试数据库，但 MOCK 掉 QdrantService
        """
        # 1. 真实 DB Service
        kb_service = KnowledgeBaseService(sql_config=core_config.sql_config_test)
        kb_service.sql_provider.session = db_session
        
        pdf_service = PdfDocumentService(sql_config=core_config.sql_config_test)
        pdf_service.sql_provider.session = db_session
        
        llm_service = LLMConfigService(sql_config=core_config.sql_config_test)
        llm_service.sql_provider.session = db_session
        
        # 2. Mock Qdrant Service (避免连接真实向量库)
        mock_qdrant = MagicMock(spec=QdrantService)
        # 将异步方法 mock 为 AsyncMock
        mock_qdrant.update_kb_id_in_payload = AsyncMock(return_value=True)
        
        return KnowledgeBaseManager(
            kb_service=kb_service,
            pdf_document_service=pdf_service,
            qdrant_service=mock_qdrant,
            llm_config_service=llm_service
        )

    async def test_create_and_update_flow(self, manager):
        """测试常规业务流程：DTO传递是否正常"""
        suffix = uuid.uuid4().hex[:6]
        
        # 1. Create
        dto = KnowledgeBaseCoreDTO(
            name=f"Manager-Test-{suffix}",
            embedding_model="test-model",
            vector_store_collection_name="test-col",
            user_id=1,
            a_settings=RetrievalSettings(top_k=99)
        )
        kb_id = await manager.create_kb(dto)
        assert kb_id > 0
        
        # 2. Get
        detail = await manager.get_kb_detail(kb_id)
        # 确认 settings 字段名修正后的效果
        assert detail.a_settings["top_k"] == 99

    async def test_delete_logic_with_qdrant_mock(self, manager):
        """
        测试删除逻辑：
        验证是否先调用了 qdrant_service 解绑向量，再删除 DB
        """
        # 1. 先创建一个
        dto = KnowledgeBaseCoreDTO(
            name="Delete-Test",
            embedding_model="bge-test",
            vector_store_collection_name="bge-test",
            user_id=1
        )
        kb_id = await manager.create_kb(dto)
        
        # 2. 执行删除
        success = await manager.delete_kb(kb_id)
        assert success is True
        
        # 3. 验证 Qdrant Mock 是否被调用
        # manager.delete_kb 内部应该调用 qdrant_service.update_kb_id_in_payload
        assert manager.qdrant_service.update_kb_id_in_payload.called
        call_args = manager.qdrant_service.update_kb_id_in_payload.call_args[0][0]
        
        # 验证调用参数 (collection_name 是否正确传了 embedding_model)
        assert call_args.collection_name == "bge-test"
        assert call_args.filter_value == kb_id
        assert call_args.payload["kb_id"] == 0 # 解绑逻辑
        
        # 4. 验证 DB 是否已删除
        db_record = await manager.get_kb_detail(kb_id)
        assert db_record is None
        print("[Success] Manager Delete Logic (DB+Qdrant) Verified")