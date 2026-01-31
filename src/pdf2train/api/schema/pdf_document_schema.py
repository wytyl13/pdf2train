#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/18 13:34
@Author  : weiyutao
@File    : pdf_document_schema.py
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Union

from pdf2train.core.schema.pdf_document_dto import PdfDocCoreDTO, CoverInfoDTO, PdfDocRichDTO


class PdfDocCreateReq(BaseModel):
    """
    用户在上传文件时，可能附带的元数据。
    注意：这里不包含 id, create_time, file_size 等由系统自动生成的字段。
    """
    kb_id: Optional[int] = Field(default=None, description="所属知识库ID")
    author: Optional[str] = Field(default=None, description="作者")
    original_title: Optional[str] = Field(default=None, description="原标题")
    summary: Optional[str] = Field(default=None, description="摘要/简介")
    instruction_gen_llm_config_id: Optional[int] = None
    h_title_llm_config_id: Optional[int] = None
    embedding_llm_config_id: Optional[int] = None
    
    
class PdfDocUpdateReq(BaseModel):
    """
    更新文档时的参数。
    全都是 Optional，用户只传需要修改的字段。
    """
    id: int
    file_name: Optional[str] = None
    author: Optional[str] = None
    original_title: Optional[str] = None
    summary: Optional[str] = None
    kb_id: Optional[int] = None
    instruction_gen_llm_config_id: Optional[int] = None
    h_title_llm_config_id: Optional[int] = None
    embedding_llm_config_id: Optional[int] = None
    confirm_sync: bool = Field(default=False, description="是否强制同步并重建向量")


class DocListReq(BaseModel):
    """
    列表查询参数 (Query Params)
    """
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, le=100)
    keyword: Optional[str] = None
    kb_id: Optional[Union[int, List[int]]] = None
    status: Optional[int] = None
    filter_step_type: Optional[int] = None
    filter_step_status: Optional[Union[int, List[int]]] = None
    

class PaginatedDocRes(BaseModel):
    items: List[PdfDocRichDTO] # 定义这里面装的是对象
    total: int
    page: int
    size: int
    

class PdfDocDeleteReq(BaseModel):
    doc_id: int = Field(..., description="文档ID")
    
    
class PdfDocContentSaveReq(BaseModel):
    doc_id: int = Field(..., description="文档ID")
    content: str = Field(..., description="Markdown内容")


class PdfDocExportBooksReq(BaseModel):
    kb_id: Optional[int] = None
    doc_ids: Optional[List[int]] = None
    filter_step_type: Optional[int] = None
    filter_step_status: Optional[List[int]] = None
    keyword: Optional[str] = None


class PdfDocCountByKbReq(BaseModel):
    kb_id: int = Field(..., description="知识库ID")

class UnassignedReq(BaseModel):
    page: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None
