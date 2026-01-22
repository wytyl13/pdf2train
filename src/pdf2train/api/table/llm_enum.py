#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/30 11:30
@Author  : weiyutao
@File    : llm_enum.py
"""

from enum import Enum

class ModelType(str, Enum):
    """模型类型"""
    LLM = "llm"
    EMBEDDING = "embedding"
    RERANK = "rerank"


class LLMProvider(str, Enum):
    """
    LLM 提供商枚举
    """
    # --- 国际主流 ---
    OPENAI = "OpenAI"
    ANTHROPIC = "Anthropic"
    GOOGLE = "Gemini"

    # --- 国内主流 (通常完美兼容 OpenAI 接口) ---
    DEEPSEEK = "DeepSeek"
    ALIYUN = "Qwen"
    MINIMAX = "MiniMax"

    # --- 本地/开源框架 (OpenAI 接口兼容) ---
    OLLAMA = "Ollama"
    VLLM = "Vllm"