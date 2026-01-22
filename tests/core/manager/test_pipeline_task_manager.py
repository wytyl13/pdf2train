#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 11:19
@Author  : weiyutao
@File    : test_pipeline_task_manager.py
"""


import pytest
import uuid
from datetime import datetime
from pdf2train.core.manager.pipeline_task_manager import PipelineTaskManager
from pdf2train.core.service.pipeline_task_service import PipelineTaskService
from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.core.schema.pipeline_task_dto import PipelineTaskUpdateDTO
from pdf2train.core.table.pipeline_task import TaskLifecycle, TaskType
from pdf2train.core.table.pdf_document import DocStatus

@pytest.mark.asyncio
class TestPipelineTaskManager:

    @pytest.fixture
    def manager(self, db_session):
        """初始化 Manager"""
        task_svc = PipelineTaskService()
        task_svc.sql_provider.session = db_session
        doc_svc = PdfDocumentService()
        doc_svc.sql_provider.session = db_session
        return PipelineTaskManager(service=task_svc, pdf_document_service=doc_svc)

    async def _create_doc(self, manager) -> int:
        """Helper: 创建文档"""
        unique_id = uuid.uuid4().hex
        data = {
            "bucket_name": "manager-test-bucket",
            "object_name": f"doc/{unique_id}.pdf",
            "file_name": "logic_test.pdf",
            "file_size": 2048,
            "status": DocStatus.PENDING.value,
            "file_hash": f"hash_{unique_id}",
            "user_name": "manager_tester",
            "create_time": datetime.now(),
            "update_time": datetime.now()
        }
        return await manager.pdf_document_service.sql_provider.add_record(data)

    async def test_init_logic(self, manager):
        """测试: init_tasks_for_document (标准4步流程)"""
        doc_id = await self._create_doc(manager)

        # 1. 初始化
        await manager.init_tasks_for_document(doc_id)

        # 2. 验证
        tasks = await manager.get_tasks_by_doc(doc_id)
        assert len(tasks) == 4
        
        # 验证顺序和初始状态
        t1, t2, t3, t4 = tasks
        assert t1.task_type == TaskType.MINERU_EXTRACT.value
        assert t1.status == TaskLifecycle.PENDING.value  # 第一个 Pending
        
        assert t2.task_type == TaskType.MARKDOWN_CHUNK.value
        assert t2.status == TaskLifecycle.WAITING_PARENT.value # 后续 Waiting

        print(f"\n[Manager] init_tasks passed. DocID: {doc_id}")

    async def test_update_flow_running(self, manager):
        """测试: 任务开始 -> 父文档变成 Running"""
        doc_id = await self._create_doc(manager)
        await manager.init_tasks_for_document(doc_id)
        tasks = await manager.get_tasks_by_doc(doc_id)
        task_id = tasks[0].id

        # 触发: 更新为 Running
        await manager.update_task_status(task_id, PipelineTaskUpdateDTO(status=TaskLifecycle.RUNNING.value))

        # 验证: 任务有了开始时间
        t = await manager.service.get_by_id(task_id)
        assert t.start_time is not None
        
        # 验证: 父文档状态变为 Running
        doc = await manager.pdf_document_service.get_by_id(doc_id)
        assert doc.status == DocStatus.RUNNING.value
        print("[Manager] update_flow_running passed.")

    async def test_update_flow_success(self, manager):
        """测试: 任务成功 -> 计算耗时 -> 父文档进度增加"""
        doc_id = await self._create_doc(manager)
        await manager.init_tasks_for_document(doc_id)
        tasks = await manager.get_tasks_by_doc(doc_id)
        task_id = tasks[0].id

        # 模拟任务先 Running
        await manager.update_task_status(task_id, PipelineTaskUpdateDTO(status=TaskLifecycle.RUNNING.value))
        
        # 触发: 更新为 Success
        await manager.update_task_status(task_id, PipelineTaskUpdateDTO(status=TaskLifecycle.SUCCESS.value))

        # 验证: 任务自动计算了 cost_ms, 且 progress 强制 100
        t = await manager.service.get_by_id(task_id)
        assert t.status == TaskLifecycle.SUCCESS.value
        assert t.progress == 100
        assert t.cost_ms >= 0
        assert t.end_time is not None

        # 验证: 父文档进度 (1/4 完成 => 25%)
        doc = await manager.pdf_document_service.get_by_id(doc_id)
        assert doc.progress == 25
        print("[Manager] update_flow_success passed.")

    async def test_update_flow_fail(self, manager):
        """测试: 任务失败 -> 父文档 Failed"""
        doc_id = await self._create_doc(manager)
        await manager.init_tasks_for_document(doc_id)
        tasks = await manager.get_tasks_by_doc(doc_id)
        task_id = tasks[0].id

        # 触发: 更新为 Failed
        err_msg = "Test Error"
        await manager.update_task_status(task_id, PipelineTaskUpdateDTO(
            status=TaskLifecycle.FAILED.value,
            error_message=err_msg
        ))

        # 验证: 父文档状态
        doc = await manager.pdf_document_service.get_by_id(doc_id)
        assert doc.status == DocStatus.FAILED.value
        assert err_msg in doc.process_error
        print("[Manager] update_flow_fail passed.")

    async def test_dashboard_aggregation(self, manager):
        """测试: 仪表盘数据结构"""
        await self._create_doc(manager) # 确保有数据
        stats = await manager.get_dashboard_stats()
        
        # 验证必需字段
        assert "total_docs" in stats
        assert "processing_docs" in stats
        assert "task_stats" in stats
        assert "pdf2md" in stats["task_stats"]
        print("[Manager] dashboard_aggregation passed.")

    async def test_recent_jobs(self, manager):
        """测试: 最近任务列表 (圆点状态)"""
        doc_id = await self._create_doc(manager)
        await manager.init_tasks_for_document(doc_id)
        
        jobs = await manager.get_recent_jobs(limit=5)
        
        assert len(jobs) >= 1
        item = jobs[0]
        # 验证字段是否存在
        assert "steps_status" in item
        # 验证是否有4个圆点
        assert len(item["steps_status"]) == 4
        print("[Manager] recent_jobs passed.")