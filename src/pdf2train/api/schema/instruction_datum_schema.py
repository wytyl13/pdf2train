#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/24 14:00
@Author  : weiyutao
@File    : instruction_datum_schema.py
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Union, Dict, Any

class InstructionListReq(BaseModel):
    """
    指令列表查询请求
    对应 Router: /api/instruction/list
    """
    doc_id: int = Field(..., description="文档ID，必须指定")
    page: int = Field(default=1, ge=1, description="页码，默认1")
    page_size: int = Field(default=20, ge=1, le=100, description="每页数量，最大100")
    
    # 筛选条件
    is_valid: Optional[int] = Field(None, description="审核状态筛选: 1有效, -1无效, 0待审")
    keyword: Optional[str] = Field(None, description="关键词搜索 (匹配 question 或 answer)")
    type: Optional[str] = Field(None, description="类型")

class InstructionUpdateReq(BaseModel):
    """
    指令更新请求
    对应 Router: /api/instruction/update
    注意：所有字段均为 Optional，只更新传入的字段
    """
    id: str = Field(..., description="指令唯一ID (UUID)")
    
    # 核心内容 (对应数据库 InstructionDatum 表字段)
    question: Optional[str] = Field(None, description="指令/问题 (Instruction/Question)")
    answer: Optional[str] = Field(None, description="回答/输出 (Output/Answer)")
    system_prompt: Optional[str] = Field(None, description="系统提示词")
    
    # 高级字段
    chain_of_thought: Optional[str] = Field(None, description="思维链内容")
    ref_chunk_ids: Optional[List[str]] = Field(None, description="关联的 Chunk ID 列表")
    
    # 状态字段
    is_valid: Optional[int] = Field(None, description="审核状态变更: 1有效, -1无效, 0重置")

class InstructionDeleteReq(BaseModel):
    """
    删除单条指令请求
    对应 Router: /api/instruction/delete
    """
    id: str = Field(..., description="要删除的指令ID (UUID)")

class InstructionClearByDocReq(BaseModel):
    """
    按文档清空指令请求
    对应 Router: /api/instruction/clear_by_doc
    """
    doc_id: int = Field(..., description="目标文档ID")

class InstructionExportByKbReq(BaseModel):
    """
    按知识库导出请求
    对应 Router: /api/instruction/download_jsonl_by_kb
    """
    # 支持导出单个知识库或多个知识库合并导出
    kb_id: Union[int, List[int]] = Field(..., description="知识库ID (单个Int或Int列表)")
    
class InstructionDatumItemRes(BaseModel):
    """Single Chunk display object"""
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    doc_id: int
    task_id: int
    system_prompt: str
    question: str
    answer: str

    chunk_index_description: Optional[List[Any]] = None
    chain_of_thought: Optional[str] = None
    h1_title: Optional[str] = None
    type: str = "general"
    ref_chunk_ids: Optional[List[str]] = None
    meta_info: Optional[Dict[str, Any]] = None
    is_indexed: bool = False
    is_valid: int = 0
    qdrant_point_id: Optional[str] = None
    
    