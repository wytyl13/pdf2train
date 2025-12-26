#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/24 13:24
@Author  : weiyutao
@File    : document_chunk.py
"""

from sqlalchemy import Column, Integer, String, DateTime, BigInteger, Text, Boolean, JSON, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from pydantic import BaseModel, Field
from typing import Optional, List, Any

from api.table.base.base import Base


class ChunkImageInfo(BaseModel):
    """
    [DTO] Chunk 中包含的图片信息结构
    对应 Markdown 解析器提取出的 images 列表
    """
    path: str = Field(..., description="图片路径 (MinIO path 或 URL)")
    description: Optional[str] = Field(None, description="图片描述/图注")
    
    
class DocumentChunk(Base):
    """
    文档切片 (Chunk) 信息表
    用于存储解析后的碎片化数据，是向量化和知识库检索的直接来源
    """
    __tablename__ = 'document_chunks'

    # === 核心主键 ===
    # 注意：这里使用 UUID 字符串作为主键，是为了方便与 Qdrant (Vector DB) 的 Point ID 保持一致
    # 如果你的架构强依赖 BigInteger 自增，也可以改回 BigInteger，但需要额外维护一个 uuid 字段给 Qdrant
    id = Column(String(36), primary_key=True, comment='Chunk唯一ID (UUID)')

    # === 关联信息 ===
    document_id = Column(BigInteger, ForeignKey('pdf_document.id', ondelete='CASCADE'), nullable=False, comment='所属文档ID')
    chunk_index = Column(Integer, nullable=False, comment='切片顺序索引 (0,1,2...)')

    # === 核心内容 ===
    content = Column(Text, nullable=False, comment='切片文本内容')

    # === 元数据 ===
    # 存储 H1-H6, file_name 等上下文信息
    meta_info = Column(JSON, default={}, comment='结构化元数据 (H1, H2, lengths...)')
    
    # 存储图片列表，使用 Pydantic 辅助解析
    image_info = Column(JSON, default=[], comment='包含的图片列表 [{"path":..., "desc":...}]')
    
    page_numbers = Column(JSON, default=[], comment='关联的页码列表 (可能跨页)')
    
    token_count = Column(Integer, default=0, comment='预估 Token 数量')

    # === 向量化状态 ===
    # 这是一个关键状态位：如果内容被编辑，需要将此位置为 False，触发重新向量化
    is_indexed = Column(Boolean, default=False, comment='是否已同步至向量库')
    qdrant_point_id = Column(String(36), nullable=True, comment='向量库中的对应ID (通常等于 id)')

    # === 审计 ===
    create_time = Column(DateTime(timezone=True), server_default=func.now(), comment='创建时间')
    update_time = Column(DateTime(timezone=True), onupdate=func.now(), comment='更新时间')

    # === 关联关系 ===
    # 反向关联回 Document，方便查询所属文档的详细信息
    document = relationship("PdfDocument", backref="chunks")

    @property
    def images(self) -> List[ChunkImageInfo]:
        """
        [读] 自动将数据库的 JSON 转为 List[ChunkImageInfo] 对象
        """
        if not self.image_info:
            return []
        try:
            # 兼容单个对象或列表
            data = self.image_info
            if isinstance(data, list):
                return [ChunkImageInfo.model_validate(item) for item in data]
            return []
        except Exception:
            return []

    @images.setter
    def images(self, items: List[ChunkImageInfo] | List[dict]):
        """
        [写] 自动序列化
        """
        if not items:
            self.image_info = []
            return
            
        json_data = []
        for item in items:
            if isinstance(item, ChunkImageInfo):
                json_data.append(item.model_dump())
            elif isinstance(item, dict):
                json_data.append(item)
        self.image_info = json_data