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