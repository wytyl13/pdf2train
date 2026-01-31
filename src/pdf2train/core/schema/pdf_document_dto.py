#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/18 18:09
@Author  : weiyutao
@File    : pdf_document_dto.py
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, Any, Union, List
from datetime import datetime

class CoverInfoDTO(BaseModel):
    bucket: str
    path: str

class PdfDocCoreDTO(BaseModel):
    """
    [Core DTO] 对应 PdfDocument 表
    包含所有数据库字段，作为 Service -> Manager 的标准传输对象
    """
    model_config = ConfigDict(from_attributes=True)
    
    id: int = None # 创建的时候忽略该id字段
    bucket_name: str
    object_name: str
    file_name: str
    content_type: str = "application/pdf"
    file_size: int = 0
    page_count: int = 0
    cover_info: Optional[CoverInfoDTO] = None
    author: Optional[str] = None
    original_title: Optional[str] = None
    summary: Optional[str] = None
    status: int
    file_hash: Optional[str] = None
    progress: int = 0
    kb_id: Optional[int] = None
    process_error: Optional[str] = None
    instruction_gen_llm_config_id: int
    h_title_llm_config_id: int
    embedding_llm_config_id: int
    user_name: str
    create_time: Optional[datetime] = datetime.now()
    update_time: Optional[datetime] = None
    

class PdfDocRichDTO(PdfDocCoreDTO):
    """
    [Rich DTO] 增强版 DTO
    Manager 层处理完业务逻辑（拼接URL、查名称）后返回此对象。
    它位于 Core 层，所以 Manager 可以安全引用。
    """
    download_url: Optional[str] = None
    cover_url: Optional[str] = None
    kb_name: Optional[str] = "未关联知识库"
    file_size_display: Optional[str] = None
    # 前端专用显示字段
    chunks_count: Optional[int] = None
    instruction_count: Optional[int] = None
    instruction_gen_llm_config: str
    h_title_llm_config: str
    embedding_llm_config: str


class PdfDocUpdateDTO(BaseModel):
    """
    [Update DTO] 用于 Service 层接收更新数据
    特点：
    1. 不包含 id (id 是函数的单独参数)
    2. 不包含 create_time, user_name (这些通常不允许改)
    3. 所有字段都是 Optional，支持局部更新 (PATCH)
    """
    file_name: Optional[str] = None
    status: Optional[int] = None
    progress: Optional[int] = None
    process_error: Optional[str] = None
    author: Optional[str] = None
    summary: Optional[str] = None
    original_title: Optional[str] = None
    cover_info: Optional[CoverInfoDTO] = None
    kb_id: Optional[int] = None
    instruction_gen_llm_config_id: Optional[int] = None
    h_title_llm_config_id: Optional[int] = None
    embedding_llm_config_id: Optional[int] = None
    

class PdfDocFilterDTO(BaseModel):
    """
    [Filter DTO] 用于 Service 层接收筛选条件
    替代原本的 filters: Dict
    """
    keyword: Optional[str] = None
    kb_id: Optional[Union[int, List[int]]] = None
    status: Optional[int] = None
    filter_step_type: Optional[int] = None
    filter_step_status: Optional[Union[int, List[int]]] = None