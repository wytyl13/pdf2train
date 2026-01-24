#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/23 09:42
@Author  : weiyutao
@File    : document_chunk_dto.py
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any, List
from pdf2train.core.table.document_chunk import ChunkImageInfo

class DocumentChunkCoreDTO(BaseModel):
    """DTO for Creating Chunks"""
    
    # 开启读取对象属性的能力
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: int
    content: str
    chunk_index: int
    token_count: int
    meta_info: Optional[Dict[str, Any]] = {}
    image_info: Optional[List[ChunkImageInfo]] = []
    is_indexed: bool = False
    qdrant_point_id: Optional[str] = None
    page_numbers: Optional[List[int]] = []

class DocumentChunkUpdateDTO(BaseModel):
    """DTO for Updating Chunks"""
    content: Optional[str] = None
    token_count: Optional[int] = None
    meta_info: Optional[Dict[str, Any]] = None
    is_indexed: Optional[bool] = None 

class DocumentChunkFilterDTO(BaseModel):
    """DTO for Filtering/Searching (DB parameters)"""
    document_id: int
    id: Optional[str] = None
    keyword: Optional[str] = None