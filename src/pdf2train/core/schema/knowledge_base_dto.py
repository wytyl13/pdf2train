#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/20 21:22
@Author  : weiyutao
@File    : knowledge_base_dto.py
"""

from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any

from pdf2train.core.schema.retrieval_dto import RetrievalSettings

class KnowledgeBaseCoreDTO(BaseModel):
    """创建知识库 DTO (数据库字段)"""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    name: str
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    embedding_model: str
    vector_store_collection_name: str
    user_id: int
    a_settings: Optional[RetrievalSettings] = None  # 注意: 数据库字段名为 _settings
    is_public: bool = False
    
class KnowledgeBaseUpdateDTO(BaseModel):
    """更新知识库 DTO"""
    name: Optional[str] = None
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    a_settings: Optional[RetrievalSettings] = None
    is_public: Optional[bool] = None
    

    

    
