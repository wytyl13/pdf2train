#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/20 21:10
@Author  : weiyutao
@File    : knowledge_base_schema.py
"""
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional, List

from pdf2train.core.schema.retrieval_dto import RetrievalSettings

class KBCreateReq(BaseModel):
    """创建知识库请求"""
    name: str = Field(..., description="知识库名称", max_length=100)
    description: Optional[str] = Field(None, description="知识库描述")
    avatar_url: Optional[str] = Field(None, description="知识库头像URL")
    embedding_model_id: Optional[int] = Field(None, description="向量模型id")
    user_id: int = Field(..., description="创建者用户ID")
    a_settings: Optional[RetrievalSettings] = Field(default_factory=RetrievalSettings)
    is_public: bool = Field(default=False, description="是否公开")
    
class KBUpdateReq(BaseModel):
    """更新知识库请求"""
    id: int = Field(..., description="知识库ID")
    name: Optional[str] = Field(None, description="知识库名称")
    # embedding_model_id: Optional[int] # 通常不允许更新 (会导致向量不一致)，如果想修改
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    a_settings: Optional[RetrievalSettings] = None
    is_public: Optional[bool] = None
    
class KBDeleteReq(BaseModel):
    """删除知识库请求"""
    id: int = Field(..., description="知识库ID")
    
class KBListReq(BaseModel):
    """知识库列表请求"""
    page: Optional[int] = Field(default=None, ge=1, description="页码")
    page_size: Optional[int] = Field(default=None, ge=1, le=100, description="每页数量")
    keyword: Optional[str] = Field(None, description="搜索关键词")
    
class KBDetailReq(BaseModel):
    """知识库详情请求"""
    id: int = Field(..., description="知识库ID")

class RelationAction(str, Enum):
    BIND = "bind"     # 关联
    UNBIND = "unbind" # 解绑

class KBUpdateDocsReq(BaseModel):
    """文档关联到知识库请求"""
    kb_id: Optional[int] = Field(default=None, description="知识库ID")
    doc_ids: List[int] = Field(..., description="文档ID列表")
    action: RelationAction = Field(default=RelationAction.BIND, description="操作类型：bind=关联, unbind=解绑")
    force: bool = Field(default=False, description="遇到模型不一致时，是否强制重置")
    

