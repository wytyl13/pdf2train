#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/12 12:08
@Author  : weiyutao
@File    : minio_config.py
"""
import os

from .base import BaseConfig, Field

class LLMConfig(BaseConfig):
    model_name: str = "deepseek-chat"
    api_key: str = Field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""))
    base_url: str = "https://api.deepseek.com"
    temperature: float = 0.1
    max_tokens: int = 8192
