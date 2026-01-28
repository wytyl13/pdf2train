#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/20 22:18
@Author  : weiyutao
@File    : qdrant_dto.py
"""

from pydantic import BaseModel, Field, root_validator
from typing import List, Dict, Any, Optional, Union


class QdrantPayloadUpdateDTO(BaseModel):
    """
    更新向量元数据的请求参数
    """
    collection_name: str
    
    filter_key: str
    filter_value: Union[int, str, List[int]]
    
    # 要更新的元数据
    payload: Dict[str, Any]
    
class EmbeddingTaskDTO(BaseModel):
    """向量化任务上下文"""
    doc_id: int

class IngestBatchDTO(BaseModel):
    """单批次写入数据"""
    chunks: List[Dict[str, Any]]
    embedding_model: str

class MetadataUpdateDTO(BaseModel):
    """元数据更新上下文"""
    doc_ids: List[int]
    kb_id: int
    # Manager层解析后填入，Router层不需要传
    collection_name: Optional[str] = None
    
class ChunkPayload(BaseModel):
    text: str
    metadata: Dict[str, Any] = {}    

class EmbeddingConfigOverride(BaseModel):
    """
    允许请求动态指定 API 地址 (例如临时切换到云端)
    """
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: Optional[str] = None

class IngestRequest(BaseModel):
    chunks: List[ChunkPayload]
    # 可选：本次请求专用的 API 配置
    embed_config: Optional[EmbeddingConfigOverride] = None