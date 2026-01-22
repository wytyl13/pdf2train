#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 11:19
@Author  : weiyutao
@File    : test_pipeline_task_router.py
"""



#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 15:10
@Author  : weiyutao
@File    : test_pipeline_task_router.py
@Desc    : Router层集成测试 (API Endpoint Integration)
"""

import pytest
import uuid
from datetime import datetime
from fastapi.testclient import TestClient

from pdf2train.api.server.main_server import app 
from pdf2train.api.dependencies import get_sql_config
from pdf2train.core.configs.sql_config import SqlConfig
from pdf2train.core.config import core_config
from pdf2train.core.table.pdf_document import PdfDocument, DocStatus
from pdf2train.core.table.pipeline_task import PipelineTask, TaskType, TaskLifecycle
from pdf2train.core.provider.sql_provider import SqlProvider

client = TestClient(app)

# --- Mock 数据库配置 ---
def get_test_sql_config() -> SqlConfig:
    return core_config.sql_config_test

@pytest.fixture(scope="function", autouse=True)
def override_db_config():
    """覆盖依赖，强制 API 使用测试数据库"""
    app.dependency_overrides[get_sql_config] = get_test_sql_config
    yield 
    app.dependency_overrides.clear()

# --- 测试类 ---
class TestPipelineTaskRouter:

    async def _setup_data(self):
        """
        [Helper] 绕过 API 直接插库，为 GET 请求准备数据
        """
        # 1. 插入文档
        doc_provider = SqlProvider(model=PdfDocument, sql_config=core_config.sql_config_test)
        unique_id = uuid.uuid4().hex
        
        doc_data = {
            "bucket_name": "router-bucket",
            "object_name": f"obj/{unique_id}.pdf",
            "file_name": f"router_test_{unique_id}.pdf",
            "file_size": 100,
            "status": DocStatus.RUNNING.value,
            "file_hash": f"hash_{unique_id}",
            "user_name": "api_tester",
            "create_time": datetime.now(),
            "update_time": datetime.now()
        }
        # 注意: 取决于 Provider 实现，add_record 可能返回 ID (int)
        doc_id = await doc_provider.add_record(doc_data)

        # 2. 插入任务
        task_provider = SqlProvider(model=PipelineTask, sql_config=core_config.sql_config_test)
        task_data = {
            "doc_id": doc_id,
            "task_type": TaskType.MINERU_EXTRACT.value,
            "step_order": 1,
            "status": TaskLifecycle.RUNNING.value,
            "task_name": "RouterStep1"
        }
        task_id = await task_provider.add_record(task_data)
        
        await doc_provider.close()
        await task_provider.close()
        return doc_id, task_id

    @pytest.mark.asyncio
    async def test_get_tasks_api(self):
        """API测试: GET /api/pipeline/tasks"""
        doc_id, _ = await self._setup_data()
        
        response = client.get(f"/api/pipeline/tasks?doc_id={doc_id}")
        
        assert response.status_code == 200
        res = response.json()
        assert res["success"] is True
        assert len(res["data"]) >= 1
        assert res["data"][0]["doc_id"] == doc_id
        print(f"\n[API] GET /tasks passed. DocID: {doc_id}")

    @pytest.mark.asyncio
    async def test_update_status_api(self):
        """API测试: POST /api/pipeline/update_status"""
        _, task_id = await self._setup_data()
        
        payload = {
            "task_id": task_id,
            "status": 100,
            "progress": 100,
            "result_data": {"url": "http://api.test"},
            "error_message": None
        }
        
        response = client.post("/api/pipeline/update_status", json=payload)
        
        assert response.status_code == 200
        res = response.json()
        assert res["success"] is True
        print(f"[API] POST /update_status passed. TaskID: {task_id}")

    @pytest.mark.asyncio
    async def test_dashboard_stats_api(self):
        """API测试: GET /api/dashboard/stats"""
        await self._setup_data() # 确保有数据
        
        response = client.get("/api/dashboard/stats")
        
        assert response.status_code == 200
        res = response.json()
        assert res["success"] is True
        assert "task_stats" in res["data"]
        print("[API] GET /dashboard/stats passed.")

    @pytest.mark.asyncio
    async def test_recent_jobs_api(self):
        """API测试: GET /api/dashboard/recent-jobs"""
        await self._setup_data()
        
        response = client.get("/api/dashboard/recent-jobs?limit=5")
        
        assert response.status_code == 200
        res = response.json()
        assert isinstance(res["data"], list)
        if len(res["data"]) > 0:
            assert "steps_status" in res["data"][0]
        print("[API] GET /recent-jobs passed.")