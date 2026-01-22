#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/10 11:28
@Author  : weiyutao
@File    : knowledge_base.py
"""

from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from pdf2train.core.table.base import Base
from pdf2train.api.schema.retrieval_schema import RetrievalSettings

class KnowledgeBase(Base):
    """
    [知识库] 核心实体表
    采用 混合存储设计：
    1. 基础设施配置 (Embedding Model) -> SQL 独立列
    2. 运行时策略配置 (TopK, Threshold, Rerank) -> JSON 聚合列
    """
    __tablename__ = "knowledge_base"

    # === 1. 基础身份信息 ===
    id = Column(Integer, primary_key=True, autoincrement=True, index=True, comment="知识库ID")
    name = Column(String(100), nullable=False, index=True, comment="知识库名称")
    description = Column(Text, nullable=True, comment="描述")
    avatar_url = Column(String(255), nullable=True, comment="封面图标")

    # === 2. 基础设施配置 (Hard Configs) ===
    embedding_model = Column(String(50), default="bge-large-zh", nullable=False, comment="向量模型名称(不可随意更改)")
    vector_store_collection_name = Column(String(100), nullable=True, comment="Qdrant集合逻辑标识")

    # === 3. 运行时策略配置 (Soft Configs) ===
    # 数据库存的是 JSON: {"top_k": 5, "mode": "hybrid", ...}
    _settings = Column("retrieval_settings", JSON, nullable=True, comment="运行时检索策略配置(JSON)")

    # === 4. 权限与生命周期 ===
    user_id = Column(Integer, index=True, nullable=False, comment="创建者ID")
    is_public = Column(Boolean, default=False, comment="是否公开")
    is_deleted = Column(Boolean, default=False, comment="逻辑删除标记")

    # === 5. 审计字段 ===
    create_time = Column(DateTime(timezone=True), server_default=func.now())
    update_time = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    # === 6. 关联关系 (Cascade配置核心点) ===
    # 级联删除：删除 KnowledgeBase 时，自动物理删除其下的所有 PdfDocument
    documents = relationship(
        "PdfDocument", 
        back_populates="knowledge_base", 
        # cascade="all, delete-orphan",
        # passive_deletes=True 
    )

    # === 7. 核心逻辑属性 (Property) ===
    @property
    def settings(self) -> RetrievalSettings:
        """
        [读] 将数据库的 JSON 自动转为 Pydantic 对象
        使用: config = kb.settings
        """
        if not self._settings:
            return RetrievalSettings() # 返回默认配置
        try:
            return RetrievalSettings(**self._settings)
        except Exception:
            # 容错处理：防止脏数据导致崩溃
            return RetrievalSettings()

    @settings.setter
    def settings(self, config: RetrievalSettings):
        """
        [写] 将 Pydantic 对象自动转为 JSON 存入数据库
        使用: kb.settings = RetrievalSettings(top_k=10)
        """
        if isinstance(config, RetrievalSettings):
            self._settings = config.model_dump()
        elif isinstance(config, dict):
            self._settings = config
        else:
            self._settings = None

    def __repr__(self):
        return f"<KnowledgeBase(id={self.id}, name='{self.name}', model='{self.embedding_model}')>"
    
