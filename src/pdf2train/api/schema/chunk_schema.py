#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/24 17:46
@Author  : weiyutao
@File    : chunk_schema.py
"""

from pydantic import BaseModel, Field

class ChunkRunReq(BaseModel):
    """文档切分请求 Schema"""
    doc_id: int = Field(..., description="文档ID")
    chunk_size: int = Field(default=500, ge=100, le=2000, description="切片大小 (Token数)")
    chunk_overlap: int = Field(default=50, ge=0, le=500, description="切片重叠大小")