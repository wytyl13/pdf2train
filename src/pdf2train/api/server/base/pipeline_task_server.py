#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/23 15:40
@Author  : weiyutao
@File    : pipeline_task_server.py
"""

from fastapi import FastAPI, APIRouter
from fastapi.responses import JSONResponse
from datetime import datetime
import logging
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from fastapi.encoders import jsonable_encoder
import traceback

from pdf2train.api.service.base.pipeline_task_service import PipelineTaskService

# === 请求模型 ===
class TaskListRequest(BaseModel):
    doc_id: int

class TaskUpdateReq(BaseModel):
    """
    任务状态更新请求
    通常由后台 Worker 调用
    """
    task_id: int
    status: int  # TaskLifecycle (10: Running, 100: Success, -1: Failed)
    detailed_status: Optional[int] = None # ExtractStatus / ChunkStatus
    result_data: Optional[Dict[str, Any]] = None # {"bucket": "...", "path": "..."}
    error_message: Optional[str] = None


class RecentJobItem(BaseModel):
    """Dashboard 最近任务列表项"""
    doc_id: int
    file_name: str
    create_time: datetime
    # 整体状态 (用于右侧 Badge: 完成/处理中/失败)
    global_status: int 
    # 4个步骤的状态列表 (用于中间的 4 个圆点)
    # 顺序严格对应: [Extract, Chunk, Instruction, Index]
    # 值对应 TaskLifecycle: 0(Pending), 10(Running), 100(Success), -1(Failed)
    steps_status: List[int] 
    # 耗时 (用于显示 "耗时 3m" 等，可选)
    cost_seconds: Optional[int] = 0


class PipelineTaskServer:
    """流水线任务接口服务"""

    def __init__(self, pipeline_task_service: PipelineTaskService):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.service = pipeline_task_service
    
    def register_routes(self, app: FastAPI):
        """注册路由"""
        router = APIRouter(tags=["Pipeline Task Server"])
        # 获取某文档的任务详情 (给前端进度条用)
        router.get("/api/pipeline/tasks")(self.get_tasks_by_doc)
        
        # 内部/外部回调：更新任务状态
        router.post("/api/pipeline/update_status")(self.update_task_status)

        # Dashboard 聚合统计接口
        router.get("/api/dashboard/stats")(self.get_dashboard_stats)
        
        # get recent jobs
        router.get("/api/dashboard/recent-jobs")(self.get_recent_jobs)
        app.include_router(router)

    def _response(self, success: bool, message: str = "", data: Any = None, code: int = 200):
        return JSONResponse(
            status_code=code,
            content={
                "success": success,
                "message": message,
                "data": jsonable_encoder(data) if data is not None else None,
                "timestamp": datetime.now().isoformat()
            }
        )

    # === 接口实现 ===
    async def get_recent_jobs(self, limit: int = 5):
        """
        [GET] 获取最近处理的任务列表 (包含流水线圆点状态)
        """
        try:
            data = await self.service.get_recent_jobs(limit=limit)
            return self._response(True, "获取最近任务成功", data=data)
        except Exception as e:
            self.logger.error(f"获取最近任务失败: {traceback.format_exc()}")
            return self._response(False, f"获取失败: {str(e)}", code=500)
    
    async def get_dashboard_stats(self):
        """
        [GET] 获取仪表盘所有统计数据
        """
        try:
            stats = await self.service.get_dashboard_statistics()
            return self._response(True, "获取统计成功", data=stats)
        except Exception as e:
            self.logger.error(f"获取Dashboard数据失败: {traceback.format_exc()}")
            return self._response(False, f"获取失败: {str(e)}", code=500)
    
    async def get_tasks_by_doc(self, doc_id: int):
        """
        [GET] 获取指定文档的所有流水线任务状态
        前端可以通过轮询此接口来渲染 Steps 组件
        """
        try:
            tasks = await self.service.get_tasks_by_doc_id(doc_id)
            return self._response(True, "获取任务列表成功", data=tasks)
        except Exception as e:
            self.logger.error(f"获取任务列表失败: {traceback.format_exc()}")
            return self._response(False, f"获取失败: {str(e)}", code=500)

    async def update_task_status(self, request: TaskUpdateReq):
        """
        [POST] 更新任务状态
        注意：调用此接口会自动触发父文档(PdfDocument)的进度计算
        """
        try:
            # 调用 Service 更新
            await self.service.update_task_status(
                task_id=request.task_id,
                status=request.status,
                detailed_status=request.detailed_status,
                result_data=request.result_data,
                error_message=request.error_message
            )
            return self._response(True, "状态更新成功")
        except ValueError as e:
            return self._response(False, str(e), code=404)
        except Exception as e:
            self.logger.error(f"更新任务状态异常: {traceback.format_exc()}")
            return self._response(False, f"系统错误: {str(e)}", code=500)