#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 11:20
@Author  : weiyutao
@File    : test_pipeline_task_service.py
"""


import pytest
import uuid
from datetime import datetime
from pdf2train.core.service.pipeline_task_service import PipelineTaskService
from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.core.schema.pipeline_task_dto import PipelineTaskCoreDTO, PipelineTaskUpdateDTO
from pdf2train.core.table.pipeline_task import TaskType, TaskLifecycle
from pdf2train.core.table.pdf_document import DocStatus

@pytest.mark.asyncio
class TestPipelineTaskService:

    @pytest.fixture
    def services(self, db_session):
        """初始化 Service 并注入测试数据库 Session"""
        task_svc = PipelineTaskService()
        task_svc.sql_provider.session = db_session
        
        doc_svc = PdfDocumentService()
        doc_svc.sql_provider.session = db_session
        return task_svc, doc_svc

    async def _create_dummy_doc(self, doc_svc) -> int:
        """
        [Helper] 创建父文档以满足外键约束
        注意: file_hash 必须唯一，否则报错
        """
        unique_id = uuid.uuid4().hex
        data = {
            "bucket_name": "service-test-bucket",
            "object_name": f"test/{unique_id}.pdf",
            "file_name": f"test_doc_{unique_id}.pdf",
            "file_size": 1024,
            "status": DocStatus.PENDING.value,
            "file_hash": f"hash_{unique_id}",  # 唯一约束
            "user_name": "service_tester",
            "create_time": datetime.now(),
            "update_time": datetime.now()
        }
        # add_record 返回的是 ID (int)
        doc_id = await doc_svc.sql_provider.add_record(data)
        return doc_id

    async def test_create_batch_and_get(self, services):
        """测试: 批量创建任务 + 按文档ID查询"""
        task_svc, doc_svc = services
        doc_id = await self._create_dummy_doc(doc_svc)

        # 1. 准备数据
        dtos = [
            PipelineTaskCoreDTO(
                doc_id=doc_id, task_type=TaskType.MINERU_EXTRACT.value,
                step_order=1, task_name="MINERU_EXTRACT", status=TaskLifecycle.PENDING.value
            ),
            PipelineTaskCoreDTO(
                doc_id=doc_id, task_type=TaskType.MARKDOWN_CHUNK.value,
                step_order=2, task_name="MARKDOWN_CHUNK", status=TaskLifecycle.WAITING_PARENT.value
            )
        ]

        # 2. 批量创建
        success = await task_svc.create_batch(dtos)
        assert success is True

        # 3. 查询验证
        tasks = await task_svc.get_by_doc_id(doc_id)
        assert len(tasks) == 2
        assert tasks[0].task_name == "MINERU_EXTRACT"
        assert tasks[1].task_name == "MARKDOWN_CHUNK"
        print(f"\n[Service] create_batch & get_by_doc_id passed. DocID: {doc_id}")

    async def test_update_task(self, services):
        """测试: 更新单条任务 (状态、进度、结果)"""
        task_svc, doc_svc = services
        doc_id = await self._create_dummy_doc(doc_svc)
        # 先创建一条
        dto = PipelineTaskCoreDTO(
            doc_id=doc_id, task_type=10, step_order=1, task_name="UpdateTest", status=0
        )
        print("=======================================")
        print(doc_id)
        print(dto)
        print("=======================================")
        await task_svc.create_batch([dto])
        tasks = await task_svc.get_by_doc_id(doc_id)
        task_id = tasks[0].id

        # 执行更新
        update_dto = PipelineTaskUpdateDTO(
            status=100,
            progress=99,
            result_data={"foo": "bar"},
            cost_ms=500
        )
        await task_svc.update(task_id, update_dto)

        # 验证
        updated_task = await task_svc.get_by_id(task_id)
        assert updated_task.status == 100
        assert updated_task.progress == 99
        assert updated_task.result_data["foo"] == "bar"
        assert updated_task.cost_ms == 500
        print(f"[Service] update passed. TaskID: {task_id}")

    async def test_get_stats(self, services):
        """测试: 获取 Dashboard 统计 (Group By)"""
        task_svc, doc_svc = services
        # 确保库里有数据
        doc_id = await self._create_dummy_doc(doc_svc)
        await task_svc.create_batch([
            PipelineTaskCoreDTO(doc_id=doc_id, task_type=10, step_order=1, task_name="INSTRUCTION_GEN", status=100)
        ])

        stats = await task_svc.get_stats_group_by_type_and_status()
        assert isinstance(stats, list)
        assert len(stats) > 0
        # 结果应为元组 (type, status, count)
        assert len(stats[0]) == 3 
        print(f"[Service] get_stats passed. Rows: {len(stats)}")

    async def test_get_status_by_doc_ids(self, services):
        """测试: 批量获取任务状态 (用于最近列表)"""
        task_svc, doc_svc = services
        doc_id = await self._create_dummy_doc(doc_svc)
        await task_svc.create_batch([
            PipelineTaskCoreDTO(doc_id=doc_id, task_type=10, step_order=1, task_name="INSTRUCTION_GEN", status=10)
        ])

        res = await task_svc.get_status_by_doc_ids([doc_id])
        assert len(res) == 1
        assert res[0]["doc_id"] == doc_id
        assert res[0]["status"] == 10
        print(f"[Service] get_status_by_doc_ids passed.")