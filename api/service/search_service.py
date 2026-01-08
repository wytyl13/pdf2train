#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/08 19:04
@Author  : weiyutao
@File    : search_service.py
"""

import httpx
import logging
from typing import Any, Dict
from pydantic import BaseModel, Field

class SearchRequest(BaseModel):
    query: str = Field(..., description="检索关键词")
    top_k: int = Field(default=5, description="召回数量")
    return_raw: bool = Field(default=False, description="是否返回原始结果")

class SearchService:
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        # 内部检索服务的 base url
        self.base_url = "http://wangeng:9040/api/vector/search"

    async def execute_search(
        self, 
        query: str,
        top_k: int,
        return_raw: bool
    ) -> Dict[str, Any]:
        """封装对内部检索接口的 HTTP 调用"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    self.base_url,
                    json={
                        "query": query,
                        "top_k": top_k,
                        "return_raw": return_raw
                    }
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                self.logger.error(f"检索接口返回错误: {e.response.text}")
                raise Exception(f"远程检索服务异常: {e.response.status_code}")
            except Exception as e:
                self.logger.error(f"请求检索接口失败: {str(e)}")
                raise Exception(f"无法连接检索服务: {str(e)}")