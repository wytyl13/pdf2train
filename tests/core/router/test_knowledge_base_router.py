#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 00:09
@Author  : weiyutao
@File    : test_knowledge_base_router.py
"""


import pytest
import uuid
from fastapi.testclient import TestClient

from pdf2train.api.server.main_server import app 
from pdf2train.api.dependencies import get_sql_config
from pdf2train.core.configs.sql_config import SqlConfig
from pdf2train.core.config import core_config

client = TestClient(app)

# ==========================================
# 配置测试数据库环境 (Mock 依赖)
# ==========================================
def get_test_sql_config() -> SqlConfig:
    return core_config.sql_config_test

@pytest.fixture(scope="function", autouse=True)
def override_db_config():
    app.dependency_overrides[get_sql_config] = get_test_sql_config
    yield
    app.dependency_overrides.clear()

# ==========================================
# 集成测试用例
# ==========================================

class TestKnowledgeBaseRouter:
    
    def test_create_kb_api(self):
        """测试 POST /create 接口"""
        random_name = f"KB-Router-Test-{uuid.uuid4().hex[:6]}"

        # 1. Payload (注意 retrieval_settings 字段名)
        payload = {
            "name": random_name,
            "description": "Router集成测试",
            "embedding_model": "bge-large-zh",
            "user_id": 99,
            "is_public": False,
            "a_settings": {
                "top_k": 3,
                "score_threshold": 0.6
            }
        }

        # 2. 发送请求
        response = client.post("/api/knowledge_base/create", json=payload)

        # 3. 验证
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["id"] > 0
        
        # 保存 ID 供后续测试 (如果需要链式测试，这里仅打印)
        print(f"\n[Create] 成功写入测试库，ID: {data['data']['id']}")
        return data["data"]["id"]

    def test_update_kb_api(self):
        """测试 Update 接口"""
        # 为了独立性，先创建一个
        kb_id = self.test_create_kb_api()
        
        update_payload = {
            "id": kb_id,
            "name": "KB-Router-Updated",
            "a_settings": {
                "top_k": 8
            }
        }
        
        response = client.post("/api/knowledge_base/update", json=update_payload)
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_list_kb_api(self):
        """测试 List 接口"""
        payload = {
            "page": 1,
            "page_size": 10,
            "keyword": "Router"
        }
        response = client.post("/api/knowledge_base/list", json=payload)
        assert response.status_code == 200
        result = response.json()
        assert "total" in result["data"]
        assert isinstance(result["data"]["items"], list)

    def test_detail_kb_api(self):
        """测试 Detail 接口，检查 settings 返回结构"""
        kb_id = self.test_create_kb_api()
        
        payload = {"id": kb_id}
        response = client.post("/api/knowledge_base/detail", json=payload)
        
        assert response.status_code == 200
        data = response.json()["data"]
        
        assert data["id"] == kb_id
        # 验证 retrieval_settings 是否正确返回
        assert "a_settings" in data
        # 如果前面存的是 top_k=3
        a_settings = data["a_settings"]
        if a_settings:
             assert a_settings["top_k"] == 3

    def test_delete_kb_api(self):
        """测试 Delete 接口"""
        kb_id = self.test_create_kb_api()
        
        # 注意：这里如果 Manager 没 Mock Qdrant，且测试环境连不上 Qdrant，
        # Delete 接口内部会 catch 异常并打印 log，但通常不会让接口 500。
        # Manager 代码逻辑是：Qdrant 失败不阻断 DB 删除。
        # 所以这里大概率能 pass，除非 Qdrant 库直接报错导致 crash。
        payload = {"id": kb_id}
        response = client.post("/api/knowledge_base/delete", json=payload)
        
        assert response.status_code == 200
        assert response.json()["success"] is True