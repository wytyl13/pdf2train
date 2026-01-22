#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 08:40
@Author  : weiyutao
@File    : pipeline_task_dto.py
"""

from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime

class PipelineTaskCoreDTO(BaseModel):
    """创建任务 DTO"""
    doc_id: int
    task_type: int  # 1:PDF2MD, 2:Chunk, 3:Instruction, 4:Embedding
    step_order: int
    task_name: str
    status: int = 0  # 0:待执行, 10:执行中, 100:成功, -1:失败
    progress: int = 0
    result_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None

class PipelineTaskUpdateDTO(BaseModel):
    """更新任务 DTO"""
    status: Optional[int] = None
    detailed_status: Optional[int] = None
    progress: Optional[int] = None
    result_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    cost_ms: Optional[int] = None