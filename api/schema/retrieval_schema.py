#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/10 11:53
@Author  : weiyutao
@File    : retrieval_schema.py
"""

from pydantic import BaseModel, Field, model_validator
from enum import Enum
from typing import Optional

class RetrievalMode(str, Enum):
    VECTOR = "vector"
    KEYWORD = "keyword"
    HYBRID = "hybrid"

class HybridConfig(BaseModel):
    alpha: float = Field(default=0.5, ge=0.0, le=1.0, description="向量权重(1.0为纯向量)")

class RerankConfig(BaseModel):
    enable: bool = Field(default=False)
    model_name: str = Field(default="bge-reranker-large")
    top_n: int = Field(default=20, description="重排序候选数量")

class RetrievalSettings(BaseModel):
    """
    [运行时策略] 随时可调的配置
    """
    top_k: int = Field(default=5, ge=1, description="最终返回数量")
    score_threshold: float = Field(default=0.4, ge=0.0, le=1.0, description="相似度阈值")
    mode: RetrievalMode = Field(default=RetrievalMode.VECTOR)
    rerank: RerankConfig = Field(default_factory=RerankConfig)
    hybrid_params: Optional[HybridConfig] = Field(default=None)

    @model_validator(mode='after')
    def check_hybrid(self):
        if self.mode == RetrievalMode.HYBRID and self.hybrid_params is None:
            self.hybrid_params = HybridConfig(alpha=0.5)
        return self