#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/12 12:06
@Author  : weiyutao
@File    : base.py
"""

import os
import yaml
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional

class BaseConfig(BaseModel):
    """WanGeng 配置基类，处理多源加载逻辑"""
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        return cls(**data)

    @classmethod
    def from_yaml(cls, yaml_path: str):
        if not os.path.exists(yaml_path):
            return cls()  # 如果文件不存在，返回默认值
        with open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data or {})