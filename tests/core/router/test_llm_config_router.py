#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/20 12:32
@Author  : weiyutao
@File    : test_llm_config_router.py
"""

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/20 12:32
@Author  : weiyutao
@File    : test_llm_config_router.py
@Desc    : Router 层集成测试 (连接真实的测试数据库，无 Mock)
"""

import pytest
import uuid
from fastapi.testclient import TestClient

# 引入你的 FastAPI app
from pdf2train.api.server.main_server import app 

# 引入配置依赖
from pdf2train.api.dependencies import get_sql_config
from pdf2train.core.configs.sql_config import SqlConfig
from pdf2train.core.config import core_config

# 初始化 Client
client = TestClient(app)

# ==========================================
# 1. 配置测试数据库环境
# ==========================================

def get_test_sql_config() -> SqlConfig:
    """指向测试数据库"""
    return core_config.sql_config_test

@pytest.fixture(scope="function", autouse=True)
def override_db_config():
    """
    [核心逻辑]
    拦截 get_sql_config 依赖，强制将其替换为 get_test_sql_config。
    测试期间，Service 会自动连接到测试库。
    """
    app.dependency_overrides[get_sql_config] = get_test_sql_config
    
    yield  # 运行测试用例
    
    app.dependency_overrides.clear() # 清理依赖覆盖

# ==========================================
# 2. 集成测试用例
# ==========================================

class TestLLMConfigRouter:
    
    def test_create_config_api(self):
        """测试 POST /create 接口 (真实写入 DB)"""
        # 生成一个随机名，防止多次运行测试报 UniqueConstraint 错误
        random_name = f"DeepSeek-Test-{uuid.uuid4().hex[:6]}"

        # 1. 准备 Payload
        payload = {
            "name": random_name,
            "model_type": "llm",
            "provider": "DeepSeek",
            "model_name": "deepseek-chat",
            "api_key": "sk-frontend-key-real",
            "base_url": "https://api.deepseek.com",
            "is_default": True
        }

        # 2. 发送请求
        response = client.post("/api/llm_config/create", json=payload)

        # 3. 验证 HTTP 响应
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        
        # 4. 【关键修正】真实 DB 的 ID 是自增的，不能断言 == 888
        assert data["data"]["id"] > 0
        print(f"\n[Create] 成功写入测试库，ID: {data['data']['id']}")

    def test_create_config_validation_error(self):
        """测试参数校验失败 (Pydantic 拦截，不查库)"""
        bad_payload = {
            "name": "Bad-Config",
            "model_type": "llm",
            "provider": "UnknownProvider", # <--- 错误的枚举值
            "model_name": "deepseek-chat",
            "api_key": "sk-test"
        }

        response = client.post("/api/llm_config/create", json=bad_payload)
        
        # 验证返回 422 Unprocessable Entity
        assert response.status_code == 422

    def test_default_config_api(self):
        """测试获取默认配置接口 (先存后取)"""
        # 1. 【前置步骤】先往数据库存一条 is_default=True 的数据
        # 因为这是集成测试，如果不存，库里可能是空的，查不到数据
        setup_name = f"Default-Config-{uuid.uuid4().hex[:6]}"
        setup_payload = {
            "name": setup_name,
            "model_type": "llm",
            "provider": "DeepSeek",
            "model_name": "deepseek-chat",
            "api_key": "sk-setup-key",
            "is_default": True # 设为默认
        }
        create_resp = client.post("/api/llm_config/create", json=setup_payload)
        assert create_resp.status_code == 200

        # 2. 发起查询请求
        query_payload = {"model_type": "llm"}
        response = client.post("/api/llm_config/default_config", json=query_payload)
        
        # 3. 验证结果
        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True
        # 验证查出来的名字就是我们刚才存进去的名字
        print("==========================================")
        print(result["data"])
        print("==========================================")
        assert result["data"]["name"] == setup_name
        # 验证是否脱敏 (包含星号)
        assert "****" in result["data"]["api_key"]