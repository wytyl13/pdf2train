#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/25 12:10
@Author  : weiyutao
@File    : instruction_gen_schema.py
"""

from pydantic import BaseModel, Field
from typing import Optional

class InstructionGenRunReq(BaseModel):
    """
    [Request] 指令生成任务提交请求
    """
    doc_id: int = Field(..., description="文档ID")
    llm_config_name: Optional[str] = Field(None, description="指定LLM配置名称，不传则使用文档默认配置")