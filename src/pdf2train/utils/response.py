#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/20 21:21
@Author  : weiyutao
@File    : response.py
"""
from fastapi.encoders import jsonable_encoder
from datetime import datetime
from typing import Any

def make_response(success: bool, message: str = "", data: Any = None, code: int = 200):
    """统一响应封装"""
    return {
        "success": success,
        "message": message,
        "data": jsonable_encoder(data) if data is not None else None,
        "timestamp": datetime.now().isoformat()
    }