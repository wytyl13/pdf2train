#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/20 10:50
@Author  : weiyutao
@File    : llm_config_dto.py
"""

from pydantic import BaseModel, ConfigDict
from typing import Optional


class LLMConfigCoreDTO(BaseModel):
    """
    [Service层契约] 用于创建配置
    特点：不包含 Enum 对象，只包含存入数据库的字符串
    """
    model_config = ConfigDict(from_attributes=True)
    
    name: str
    model_type: str 
    provider: str
    model_name: str
    api_key: str
    base_url: Optional[str] = None
    is_default: bool = False

class LLMConfigUpdateDTO(BaseModel):
    """
    [Service层契约] 用于更新配置
    特点：所有字段可选，用于 PATCH 更新
    """
    name: Optional[str] = None
    model_type: Optional[str] = None
    provider: Optional[str] = None
    model_name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    is_default: Optional[bool] = None
    
class ResetDefaulExceptDTO(BaseModel):
    model_type: str
    exclude_id: int