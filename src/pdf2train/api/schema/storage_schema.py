#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 08:23
@Author  : weiyutao
@File    : storage_schema.py
"""

from typing import Optional
from pydantic import BaseModel, Field


class GetUrlReq(BaseModel):
    bucket_name: str | None = Field(default=None, min_length=1, description="桶名称")
    object_name: str = Field(..., min_length=1, description="对象路径/文件名")
    expires: int = Field(default=3600, description="过期时间(秒)")

class DeleteFileReq(BaseModel):
    bucket_name: str = Field(..., min_length=1, description="桶名称")
    object_name: str = Field(..., min_length=1, description="对象路径/文件名")