#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/23 14:40
@Author  : weiyutao
@File    : instruction_datum_dto.py
"""


from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any, List, Union

class InstructionDatumCoreDTO(BaseModel):
    """
    数据库存储DTO - 对应 InstructionDatum 表的创建
    """
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    doc_id: int
    task_id: int  # 必填，关联 pipeline_task
    system_prompt: str
    question: str
    answer: str
    # 默认为空列表或None的字段
    chunk_index_description: Optional[List[Any]] = None
    chain_of_thought: Optional[str] = None
    h1_title: Optional[str] = None
    type: str = "general"
    ref_chunk_ids: Optional[List[str]] = None
    meta_info: Optional[Dict[str, Any]] = None
    # 状态字段
    is_indexed: bool = False
    is_valid: int = 0  # 默认为待审
    qdrant_point_id: Optional[str] = None

class InstructionDatumUpdateDTO(BaseModel):
    """
    数据库更新DTO - 对应 update 操作
    """
    system_prompt: Optional[str] = None
    question: Optional[str] = None
    answer: Optional[str] = None
    chain_of_thought: Optional[str] = None
    h1_title: Optional[str] = None
    type: Optional[str] = None
    ref_chunk_ids: Optional[List[str]] = None
    meta_info: Optional[Dict[str, Any]] = None
    is_valid: Optional[int] = None
    is_indexed: Optional[bool] = None
    qdrant_point_id: Optional[str] = None

class InstructionDatumFilterDTO(BaseModel):
    """
    查询筛选DTO
    """
    doc_id: Optional[int] = None
    is_valid: Optional[int] = None
    keyword: Optional[str] = None # 用于 question 或 answer 模糊搜
    type: Optional[str] = None
    
