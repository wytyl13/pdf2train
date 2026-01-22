#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/10 20:46
@Author  : weiyutao
@File    : qdrant_schema.py
"""

from pydantic import BaseModel, Field, root_validator
from typing import List, Dict, Any, Optional, Union


class MetadataUpdateRequest(BaseModel):
    """
    更新向量元数据的请求参数
    """
    collection_name: str
    
    filter_key: str
    filter_value: Union[int, str, List[int]]
    
    # 要更新的元数据
    payload: Dict[str, Any]
    
    
class UnbindKbId(BaseModel):
    """
    解绑kb_id
    """
    collection_name: str
    kb_id: int
    
    
class VectorDeleteRequest(BaseModel):
    """
    向量删除请求参数 (升级版)
    支持 单条件 (filter_key + filter_value) 或 多条件 (filters)
    """
    collection_name: str
    
    # === 兼容旧模式 (单条件) ===
    filter_key: Optional[str] = None
    filter_value: Optional[Union[int, str, List[int], List[str]]] = None
    
    # === 新增：多条件模式 ===
    # 例如: {"doc_kb_id": 19, "type": "instruction"}
    filters: Optional[Dict[str, Union[int, str, List[int], List[str]]]] = None
    
    @root_validator(pre=True)
    def check_filters(cls, values):
        # 校验：要么传 key+value，要么传 filters，不能什么都不传
        key, val = values.get('filter_key'), values.get('filter_value')
        filters = values.get('filters')
        
        if not (key and val) and not filters:
            raise ValueError("必须提供 filter_key/filter_value 或 filters 其中之一")
        return values



class ChunkPayload(BaseModel):
    text: str
    metadata: Dict[str, Any] = {}


class EmbeddingRunRequest(BaseModel):
    """触发向量化任务的请求参数"""
    doc_id: int = Field(..., description="文档ID")


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