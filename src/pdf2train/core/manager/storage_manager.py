#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/19 15:48
@Author  : weiyutao
@File    : storage_manager.py
"""

from typing import Optional, Dict, Any
from pdf2train.core.service.minio_service import MinioService

class StorageManager:
    def __init__(
        self, 
        minio_service: MinioService,
        
    ):
        self.minio_service = minio_service

    async def get_presigned_url(self, bucket: str, object_name: str, expires: int = 3600):
        """
        获取文件的临时访问链接
        """
        return await self.minio_service.generate_presigned_url(bucket, object_name, expires)

    async def physical_delete(self, bucket: str, object_name: str):
        """
        [管理员] 物理删除文件
        注意：此操作不联动业务数据库，仅用于清理垃圾文件
        """
        return await self.minio_service.remove_object(bucket, object_name)