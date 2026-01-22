#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/19 16:28
@Author  : weiyutao
@File    : document_chunk_manager.py
"""

class DocumentChunkManager:
    def __init__(self, chunk_service: DocumentChunkService):
        self.chunk_service = chunk_service

    async def get_chunk_count(self, doc_id: int) -> int:
        """
        [业务] 获取切片数量
        """
        return await self.chunk_service.count_by_doc_id(doc_id)