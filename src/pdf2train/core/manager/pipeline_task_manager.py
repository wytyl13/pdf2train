#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 10:25
@Author  : weiyutao
@File    : pipeline_task_manager.py
"""

import logging
from typing import List, Dict, Any
from datetime import datetime

from pdf2train.core.table.pdf_document import DocStatus
from pdf2train.core.service.pipeline_task_service import PipelineTaskService
from pdf2train.core.schema.pipeline_task_dto import PipelineTaskCoreDTO, PipelineTaskUpdateDTO
from pdf2train.core.table.pipeline_task import PipelineTask, TaskLifecycle, TaskType
from pdf2train.core.service.pdf_document_service import PdfDocumentService, PdfDocUpdateDTO


class PipelineTaskManager:
    """
    Pipeline Task 业务逻辑层
    负责调度、状态流转计算、数据格式化
    """
    
    def __init__(
        self, 
        service: PipelineTaskService,
        pdf_document_service: PdfDocumentService
    ):
        self.service = service
        self.pdf_document_service = pdf_document_service
        self.logger = logging.getLogger(self.__class__.__name__)
    
    # 核心业务：任务初始化 & 更新状态 & 自动刷新父文档
    async def init_tasks_for_document(self, doc_id: int) -> bool:
        """
        [业务编排] 为新文档初始化默认流水线
        定义了步骤的顺序、名称和初始状态
        """
        return await self.service.init_tasks_for_document(doc_id)

    async def reset_processing_tasks_to_failed(self) -> int:
        """
        [系统级兜底] 重置所有异常中断的任务
        逻辑：
        1. 重置所有正在处理的任务状态
        2. 触发这些 doc_id 的父文档状态刷新，确保文档状态也变更为 FAILED
        """
        try:
            # 1. 重置所有正在处理的任务状态
            affected_doc_ids = await self.service.reset_processing_tasks_to_failed()

            # 2. 刷新父文档状态
            for doc_id in affected_doc_ids:
                await self._refresh_parent_doc_status(doc_id)
                self.logger.info(f"已同步刷新文档状态 DocID: {doc_id}")
            return len(affected_doc_ids)
        except Exception as e:
            import traceback
            error_info = f"重置挂起任务失败！{str(e)} \n {traceback.format_exc()}"
            self.logger.error(error_info)
            raise e

    async def update_task_status(self, task_id: int, update_dto: PipelineTaskUpdateDTO) -> None:
        """
        [业务编排] 更新任务状态
        1. 计算耗时 (Start/End Time)
        2. 更新 PipelineTask
        3. 重新计算并更新 Document 状态
        """
        # 1. 获取任务上下文
        task = await self.service.get_by_id(task_id)
        if not task:
            raise ValueError(f"Task ID {task_id} not found")

        doc_id = task.doc_id
        current_status = task.status
        db_start_time = task.start_time

        # 2. 业务逻辑: 自动补全时间字段
        now = datetime.now()
        target_status = update_dto.status

        # Case: 开始运行
        if target_status == TaskLifecycle.RUNNING.value and current_status != TaskLifecycle.RUNNING.value:
            update_dto.start_time = now
        
        # Case: 结束 (成功/失败)
        if target_status in [TaskLifecycle.SUCCESS.value, TaskLifecycle.FAILED.value]:
            update_dto.end_time = now
            # 计算耗时
            if db_start_time:
                if isinstance(db_start_time, str):
                    try:
                        db_start_time = datetime.fromisoformat(str(db_start_time))
                    except ValueError:
                        db_start_time = now
                
                cost = int((now - db_start_time).total_seconds() * 1000)
                update_dto.cost_ms = max(0, cost)
            
            # 成功则强制进度 100
            if target_status == TaskLifecycle.SUCCESS.value:
                update_dto.progress = 100

        # 3. 调用 Service 更新任务
        await self.service.update(task_id, update_dto)

        # 4. 触发父文档刷新 (Event Trigger)
        await self._refresh_parent_doc_status(doc_id)
        
    async def _refresh_parent_doc_status(self, doc_id: int):
        """[Internal] 计算父文档的聚合状态"""
        tasks = await self.service.get_by_doc_id(doc_id)
        if not tasks: return

        total_tasks = len(tasks)
        completed_count = 0
        total_progress_sum = 0
        failed_task = None
        has_running = False

        for t in tasks:
            s = t.status
            p = t.progress or 0
            
            if s == TaskLifecycle.SUCCESS.value:
                completed_count += 1
                p = 100
            elif s == TaskLifecycle.FAILED.value:
                if not failed_task: failed_task = t
            elif s == TaskLifecycle.RUNNING.value:
                has_running = True
            
            total_progress_sum += p

        # 聚合逻辑
        avg_progress = int(total_progress_sum / total_tasks) if total_tasks > 0 else 0
        
        new_status = DocStatus.RUNNING.value
        err_msg = None

        if failed_task:
            new_status = DocStatus.FAILED.value
            err_msg = f"Step {failed_task.step_order} Failed: {failed_task.error_message}"
        elif completed_count == total_tasks:
            new_status = DocStatus.SUCCESS.value
            avg_progress = 100
        elif not has_running and completed_count == 0 and not failed_task:
            new_status = DocStatus.PENDING.value
        
        if new_status == DocStatus.RUNNING.value and avg_progress >= 100:
            avg_progress = 99

        # 调用 Doc Service
        dto = PdfDocUpdateDTO(status=new_status, progress=avg_progress, process_error=err_msg)
        await self.pdf_document_service.update(doc_id, dto)
        
    # 数据展示：Dashboard & List
    async def get_tasks_by_doc(self, doc_id: int) -> List[PipelineTask]:
        """获取文档任务列表"""
        return await self.service.get_by_doc_id(doc_id)
    
    async def get_dashboard_stats(self) -> Dict[str, Any]:
        """获取 Dashboard 统计数据 (跨表聚合)"""
        # 并行获取数据
        doc_stats_map = await self.pdf_document_service.get_stats_group_by_status()
        task_stats_raw = await self.service.get_stats_group_by_type_and_status()

        # 组装数据
        stats = {
            "total_docs": sum(doc_stats_map.values()),
            "processing_docs": doc_stats_map.get(DocStatus.RUNNING.value, 0) + doc_stats_map.get(DocStatus.PENDING.value, 0),
            "completed_docs": doc_stats_map.get(DocStatus.SUCCESS.value, 0),
            "failed_docs": doc_stats_map.get(DocStatus.FAILED.value, 0),
            "task_stats": {}
        }

        # 映射 Task Type
        type_map = {
            TaskType.MINERU_EXTRACT.value: "pdf2md",
            TaskType.MARKDOWN_CHUNK.value: "chunk",
            TaskType.INSTRUCTION_GEN.value: "instruction",
            TaskType.QDRANT_INDEX.value: "embedding"
        }
        
        for name in type_map.values():
            stats["task_stats"][name] = {"total": 0, "success": 0, "failed": 0, "processing": 0}

        for t_type, t_status, count in task_stats_raw:
            key = type_map.get(t_type)
            if not key: continue
            
            target = stats["task_stats"][key]
            target["total"] += count
            
            if t_status == TaskLifecycle.SUCCESS.value:
                target["success"] += count
            elif t_status == TaskLifecycle.FAILED.value:
                target["failed"] += count
            elif t_status in [TaskLifecycle.RUNNING.value, TaskLifecycle.PENDING.value]:
                target["processing"] += count

        return stats
    
    async def get_recent_jobs(self, limit: int) -> List[Dict[str, Any]]:
        """获取最近任务 (组装圆点状态)"""
        # 1. 查文档
        recent_docs = await self.pdf_document_service.get_recent_docs(limit)
        if not recent_docs: return []

        # 2. 查任务状态
        doc_ids = [d.id for d in recent_docs]
        tasks_raw = await self.service.get_status_by_doc_ids(doc_ids)

        # 3. 内存映射
        task_map = {did: {} for did in doc_ids}
        for item in tasks_raw:
            task_map[item['doc_id']][item['task_type']] = item['status']

        # 4. 格式化
        result = []
        step_order = [TaskType.MINERU_EXTRACT.value, TaskType.MARKDOWN_CHUNK.value, 
                      TaskType.INSTRUCTION_GEN.value, TaskType.QDRANT_INDEX.value]

        for doc in recent_docs:
            current_map = task_map.get(doc.id, {})
            steps_status = [current_map.get(s, TaskLifecycle.PENDING.value) for s in step_order]
            
            result.append({
                "doc_id": doc.id,
                "file_name": doc.file_name,
                "global_status": doc.status,
                "create_time": doc.create_time,
                "steps_status": steps_status
            })
        
        return result
        
    