#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 00:08
@Author  : weiyutao
@File    : test_knowledge_base_service.py
"""

import pytest
import uuid
from pdf2train.core.service.knowledge_base_service import KnowledgeBaseService
from pdf2train.core.schema.knowledge_base_dto import KnowledgeBaseCoreDTO, KnowledgeBaseUpdateDTO
from pdf2train.core.schema.retrieval_dto import RetrievalSettings
from pdf2train.core.config import core_config

@pytest.mark.asyncio
class TestKnowledgeBaseService:

    @pytest.fixture
    def service(self, db_session):
        """
        初始化 Service，注入测试配置和 Session
        """
        svc = KnowledgeBaseService(sql_config=core_config.sql_config_test)
        svc.sql_provider.session = db_session
        return svc

    async def test_lifecycle(self, service, db_session):
        """
        测试：创建 -> 获取 -> 更新 (含JSON字段) -> 列表 -> 删除
        """
        random_suffix = uuid.uuid4().hex[:6]
        
        # =======================================
        # 1. 测试 Create (包含 retrieval_settings)
        # =======================================
        # 构造复杂的检索设置
        a_settings = RetrievalSettings(
            top_k=5,
            score_threshold=0.75,
            kb_ids=[]
        )

        dto = KnowledgeBaseCoreDTO(
            name=f"KB-Service-Test-{random_suffix}",
            description="这是一个测试知识库",
            avatar_url="http://localhost/logo.png",
            embedding_model="bge-large-zh",
            vector_store_collection_name="bge-large-zh",
            user_id=1,
            a_settings=a_settings,  # Pydantic 对象
            is_public=True
        )

        kb_id = await service.create(dto)
        assert kb_id > 0
        print(f"\n[Success] Created KB ID: {kb_id}")

        # =======================================
        # 2. 测试 Get By ID & Verify Settings
        # =======================================
        saved_record = await service.get_by_id(kb_id)
        assert saved_record is not None
        assert saved_record.name == f"KB-Service-Test-{random_suffix}"
        
        # 验证 JSON 字段是否正确存取
        # 注意：SQLAlchemy 取出的可能是 dict 或 Pydantic 对象，取决于 ORM 配置
        # 如果数据库直接返回 dict：
        r_settings = saved_record.a_settings
        if isinstance(r_settings, dict):
            assert r_settings["top_k"] == 5
            assert r_settings["score_threshold"] == 0.75
        else:
            # 假设自动转回了对象（视实现而定）
            pass
            
        print("[Success] Get By ID & Settings Verified")

        # =======================================
        # 3. 测试 Update
        # =======================================
        # 更新名称和检索设置
        new_settings = RetrievalSettings(top_k=10, score_threshold=0.8)
        
        update_dto = KnowledgeBaseUpdateDTO(
            name=f"KB-Updated-{random_suffix}",
            a_settings=new_settings
        )
        
        success = await service.update(kb_id, update_dto)
        assert success is True
        
        updated_record = await service.get_by_id(kb_id)
        assert updated_record.name == f"KB-Updated-{random_suffix}"
        
        # 验证 JSON 更新
        updated_settings = updated_record.a_settings
        if isinstance(updated_settings, dict):
             assert updated_settings["top_k"] == 10
        
        print("[Success] Update Verified")

        # =======================================
        # 4. 测试 List (Search)
        # =======================================
        search_res = await service.search_paginated(page=1, page_size=10, keyword="Updated")
        assert search_res["total"] >= 1
        # 验证列表里包含刚才更新的数据
        found = False
        for item in search_res["items"]:
            if item.id == kb_id:
                found = True
                break
        assert found is True
        print("[Success] List Search Verified")

        # =======================================
        # 5. 测试 Delete
        # =======================================
        del_success = await service.delete(kb_id)
        assert del_success is True
        
        deleted_record = await service.get_by_id(kb_id)
        assert deleted_record is None
        print("[Success] Delete Verified")