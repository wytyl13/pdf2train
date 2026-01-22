#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 08:38
@Author  : weiyutao
@File    : pipeline_task_schema.py
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime

class TaskListReq(BaseModel):
    """查询任务列表请求"""
    doc_id: int = Field(..., description="文档ID")

class TaskUpdateReq(BaseModel):
    """更新任务状态请求"""
    task_id: int = Field(..., description="任务ID")
    status: int = Field(..., description="任务状态: 0待执行, 10执行中, 100成功, -1失败")
    progress: Optional[int] = Field(None, ge=0, le=100, description="进度 0-100")
    result_data: Optional[Dict[str, Any]] = Field(None, description="任务结果数据")
    error_message: Optional[str] = None

class DashboardStatsReq(BaseModel):
    """Dashboard统计请求 (可扩展筛选条件)"""
    pass

class RecentJobsReq(BaseModel):
    """最近任务请求"""
    limit: int = Field(default=5, ge=1, le=50, description="返回数量")