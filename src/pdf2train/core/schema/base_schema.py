#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/23 11:08
@Author  : weiyutao
@File    : base_schema.py
"""

from typing import Generic, TypeVar, List, Type
from pydantic import BaseModel

T = TypeVar("T")
U = TypeVar("U")

class PageResult(BaseModel, Generic[T]):
    """通用的分页响应结构"""
    items: List[T]
    total: int
    page: int = 1
    page_size: int = 20
    
    def map(self, target_type: Type[U]) -> "PageResult[U]":
        """
        将当前分页结果中的 items 转换为另一种类型 (U)，
        同时保留 total, page, page_size 等元信息。
        """
        # 自动转换列表中的每一项
        # 前提：target_type (如 ChunkItemRes) 必须开启 from_attributes=True
        new_items = [target_type.model_validate(item) for item in self.items]
        print(new_items)
        # 返回新的 PageResult 对象
        return PageResult[U](
            items=new_items,
            total=self.total,
            page=self.page,
            page_size=self.page_size
        )