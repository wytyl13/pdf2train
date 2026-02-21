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
from pdf2train.core.schema.qdrant_dto import EmbeddingConfigOverride
from pdf2train.core.schema.llm_config_dto import LLMConfigCoreDTO

class KnowledgeBaseCoreDTO(BaseModel):
    """创建知识库 DTO (数据库字段)"""
    model_config = ConfigDict(from_attributes=True)
    
    id: Optional[int] = None
    name: str
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    embedding_model_id: int
    user_id: int
    a_settings: Optional[RetrievalSettings] = None  # 注意: 数据库字段名为 _settings
    is_public: bool = False
    

class KnowledgeBaseCoreRichDTO(BaseModel):
    id: int
    embedding_model_id: int
    a_settings: RetrievalSettings
    rerank_model_config: LLMConfigCoreDTO
    collection_name: str
    embedding_config_override: EmbeddingConfigOverride
    
class KnowledgeBaseUpdateDTO(BaseModel):
    """更新知识库 DTO"""
    name: Optional[str] = None
    description: Optional[str] = None
    # embedding_model_id: Optional[int] = None # 一般不允许修改，如果想修改取消注解即可
    avatar_url: Optional[str] = None
    a_settings: Optional[RetrievalSettings] = None
    is_public: Optional[bool] = None
    

    

    
