#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/23 09:41
@Author  : weiyutao
@File    : document_chunk_schema.py
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any, Union
from pdf2train.core.table.document_chunk import ChunkImageInfo


# === Request Objects (RPC Style) ===
class ChunkListReq(BaseModel):
    """Query chunk list"""
    document_id: int = Field(..., description="Document ID")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    keyword: Optional[str] = None
    id: Optional[str] = None


class ChunkUpdateReq(BaseModel):
    """Update chunk content"""
    id: str = Field(..., description="Chunk UUID")
    content: Optional[str] = Field(None, min_length=1)
    meta_info: Optional[Dict[str, Any]] = Field(None, description="Metadata dictionary")


class ChunkDeleteReq(BaseModel):
    """Delete single chunk"""
    id: str = Field(..., description="Chunk UUID")
    cascade_ids: Optional[List[str]] = Field(default=None, description="InstructionDatum UUIDs")

class ChunkDeleteCheckReq(BaseModel):
    """Delete single chunk"""
    id: str = Field(..., description="Chunk UUID")

class ChunkClearReq(BaseModel):
    """Delete single chunk"""
    doc_id: int = Field(..., description="document_id")
    cascade_ids: Optional[List[str]] = Field(default=None, description="InstructionDatum UUIDs")

class ChunkClearCheckReq(BaseModel):
    """Delete single chunk"""
    doc_id: int = Field(..., description="document_id")


class ChunkDeleteByDocReq(BaseModel):
    """Batch delete by document"""
    document_id: int = Field(..., description="Document ID")


class ChunkExportPretrainReq(BaseModel):
    """Export pretrain data by Doc IDs"""
    doc_ids: List[int] = Field(..., description="List of Document IDs")
    filename: str = Field(default="pretrain_corpus.jsonl")


class ChunkExportPretrainByKbReq(BaseModel):
    """Export pretrain data by KB IDs"""
    kb_ids: List[int] = Field(..., description="List of Knowledge Base IDs")
    filename: str = Field(default="kb_corpus.jsonl")


# === Response Data Objects ===
class ChunkItemRes(BaseModel):
    """Single Chunk display object"""
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    document_id: int
    chunk_index: int
    content: str
    meta_info: Optional[Dict[str, Any]] = None
    image_info: Optional[List[ChunkImageInfo]] = None
    token_count: int
    is_indexed: bool
    page_numbers: Optional[List[int]] = None
    qdrant_point_id: Optional[str] = None
    
