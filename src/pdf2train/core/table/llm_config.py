#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/30 11:27
@Author  : weiyutao
@File    : llm_config.py
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.sql import func

from pdf2train.core.table.base import Base

class LLMConfig(Base):
    __tablename__ = "sys_llm_configs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, comment="主键ID")
    name = Column(String(100), nullable=False, unique=True, comment="配置名称，如 DeepSeek-V3")
    provider = Column(String(50), nullable=False, comment="提供商，如 deepseek, openai")
    model_name = Column(String(100), nullable=False, comment="模型名称，如 deepseek-chat")
    
    model_type = Column(String(50), nullable=False, default="llm", index=True, comment="模型类型: llm, embedding, rerank")
    # 注意：生产环境中 API Key 建议加密存储
    api_key = Column(String(500), nullable=False, comment="API Key")
    
    # Base URL 是兼容 OpenAI 接口的关键，DeepSeek 为 https://api.deepseek.com/v1
    base_url = Column(String(255), nullable=True, comment="API Base URL")
    
    is_default = Column(Boolean, default=False, comment="是否为该类型的默认配置")
    
    created_at = Column(DateTime, default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), comment="更新时间")

    def __repr__(self):
        return f"<ModelConfig(name={self.name}, type={self.model_type})>"