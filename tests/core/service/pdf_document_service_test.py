#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/19 12:25
@Author  : weiyutao
@File    : pdf_document_service_test.py
"""

import pytest
from datetime import datetime

# 引入 Service
from pdf2train.core.service.pdf_document_service import PdfDocumentService
# 引入 Table 枚举
from pdf2train.core.table.pdf_document import DocStatus
from pdf2train.core.table.pipeline_task import PipelineTask, TaskType, TaskLifecycle
# 引入 DTO
from pdf2train.core.schema.pdf_document_dto import PdfDocCoreDTO, PdfDocUpdateDTO, CoverInfoDTO
from pdf2train.core.schema.pdf_document_dto import PdfDocFilterDTO
from pdf2train.core.config import core_config

@pytest.mark.asyncio
class TestPdfDocumentService:

    @pytest.fixture
    def service(self, db_session):
        """
        初始化 Service，并注入内存数据库的 Session
        """
        svc = PdfDocumentService(sql_config=core_config.sql_config_test)
        svc.sql_provider.session = db_session
        return svc

    async def test_full_lifecycle(self, service, db_session):
        """
        测试：创建 -> 查询 -> 更新 -> 复杂筛选
        """
        # =======================================
        # 1. 测试 Create (使用 CoreDTO)
        # =======================================
        new_doc = PdfDocCoreDTO(
            id=0, # 占位
            file_name="paper_v1.pdf",
            file_hash="hash_abc_123",
            bucket_name="bucket-test",
            object_name="files/paper_v1.pdf",
            status=DocStatus.PENDING.value,
            user_name="tester",
            file_size=2048,
            create_time=datetime.now(),
            cover_info=CoverInfoDTO(bucket="assets", path="cover.jpg")
        )
        
        doc_id = await service.create(new_doc)
        assert doc_id > 0
        print(f"\n[Success] Created Doc ID: {doc_id}")

        # =======================================
        # 2. 测试 GetById
        # =======================================
        saved_doc = await service.get_by_id(doc_id)
        assert saved_doc.file_name == "paper_v1.pdf"
        # 验证 JSON 字段是否正确存取
        assert saved_doc.cover_info['bucket'] == "assets"
        print("[Success] Get By ID Verified")

        # =======================================
        # 3. 测试 Update (使用 UpdateDTO)
        # =======================================
        update_dto = PdfDocUpdateDTO(
            file_name="paper_final.pdf", # 改名
            status=DocStatus.RUNNING.value # 改状态
        )
        await service.update(doc_id, update_dto)
        
        updated_doc = await service.get_by_id(doc_id)
        assert updated_doc.file_name == "paper_final.pdf"
        assert updated_doc.status == DocStatus.RUNNING.value
        assert updated_doc.bucket_name == "bucket-test" # 没改的字段保持原样
        print("[Success] Update Verified")

        # =======================================
        # 4. 测试 Search (复杂关联查询)
        # =======================================
        # 手动造一个“任务失败”的场景
        # 直接操作 db_session 插入一个 Task
        task = PipelineTask(
            doc_id=doc_id,
            task_type=TaskType.MINERU_EXTRACT.value, # 假设 10
            status=TaskLifecycle.FAILED.value # 假设 -1
        )
        db_session.add(task)
        await db_session.commit()

        # 使用 FilterDTO 筛选“提取失败”的文档
        filter_dto = PdfDocFilterDTO(
            filter_step_type=TaskType.MINERU_EXTRACT.value,
            filter_step_status=TaskLifecycle.FAILED.value
        )
        
        items, total = await service.search_paginated(1, 10, filter_dto)
        
        assert total == 1
        assert items[0].id == doc_id
        print("[Success] Complex Search Verified")