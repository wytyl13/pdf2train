#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 11:03
@Author  : weiyutao
@File    : pipeline_task_router.py
"""

from fastapi import APIRouter, Depends, Query
from typing import List

from pdf2train.core.manager.pipeline_task_manager import PipelineTaskManager
from pdf2train.api.schema.pipeline_task_schema import TaskUpdateReq, TaskListReq, RecentJobsReq
from pdf2train.core.schema.pipeline_task_dto import PipelineTaskUpdateDTO
from pdf2train.utils.response import make_response
from pdf2train.api.dependencies import get_pipeline_task_manager

router = APIRouter(prefix="/api/pipeline", tags=["Pipeline Task"])
router_dashboard = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])

# Pipeline Task Routes
@router.get("/tasks")
async def get_tasks(
    doc_id: int = Query(..., description="文档ID"),
    manager: PipelineTaskManager = Depends(get_pipeline_task_manager)
):
    """获取文档的所有任务详情"""
    result = await manager.get_tasks_by_doc(doc_id)
    return make_response(True, "查询成功", result)

@router.post("/update_status")
async def update_status(
    req: TaskUpdateReq,
    manager: PipelineTaskManager = Depends(get_pipeline_task_manager)
):
    """
    [核心] 更新任务状态
    Worker 调用此接口汇报进度
    """
    # 将 API Schema 转换为 Core DTO
    core_dto = PipelineTaskUpdateDTO(
        status=req.status,
        progress=req.progress,
        result_data=req.result_data,
        error_message=req.error_message
    )
    
    # 传递 DTO 给 Manager
    await manager.update_task_status(
        task_id=req.task_id,
        update_dto=core_dto
    )
    
    return make_response(True, "更新成功")

# Dashboard Routes
@router_dashboard.get("/stats")
async def get_dashboard_stats(
    manager: PipelineTaskManager = Depends(get_pipeline_task_manager)
):
    """获取仪表盘统计数据"""
    stats = await manager.get_dashboard_stats()
    return make_response(True, "查询成功", stats)

@router_dashboard.get("/recent-jobs")
async def get_recent_jobs(
    limit: int = Query(default=5, ge=1, le=50),
    manager: PipelineTaskManager = Depends(get_pipeline_task_manager)
):
    """获取最近任务列表 (含圆点状态)"""
    jobs = await manager.get_recent_jobs(limit)
    return make_response(True, "查询成功", jobs)
