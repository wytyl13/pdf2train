#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/10 20:46
@Author  : weiyutao
@File    : qdrant_schema.py
"""

from pydantic import BaseModel, Field
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