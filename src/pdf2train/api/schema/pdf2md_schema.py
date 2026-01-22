#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 18:13
@Author  : weiyutao
@File    : pdf2md_schema.py
"""

from pydantic import BaseModel, Field
from typing import Optional

class Pdf2MdConvertReq(BaseModel):
    """PDF转Markdown请求"""
    doc_id: int = Field(..., description="文档ID")
    is_ocr: bool = Field(default=True, description="是否启用OCR")
    split_pages: int = Field(default=40, ge=1, le=200, description="分页大小")
