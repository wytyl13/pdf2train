#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/16 16:45
@Author  : weiyutao
@File    : minio_service.py
"""

import logging
import asyncio
import io
from functools import partial
from typing import (
    List, 
    Optional,
    Dict
)
from minio import Minio
from datetime import datetime, timedelta
from pypdf import PdfReader
import fitz  # PyMuPDF
import io
import os
import json




class MinioService:
    """
    MinIO 基础设施层
    负责：文件的上传、下载、删除、元数据读取、预签名生成
    原则：不处理具体业务逻辑（如 PDF 解析），只管二进制流的存取
    """
    def __init__(
        self, 
        endpoint: str, 
        access_key: str, 
        secret_key: str, 
        secure: bool = False, 
        default_bucket: str = "default", 
        buckets_to_create: List[str] = None,
        source_bucket_name: str = "pdf-raw",
        target_bucket_name: str = "pdf-processed",
        public_bucket: str = "public-assets"
    ):
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.secure = secure
        self.default_bucket = default_bucket
        self.source_bucket_name = source_bucket_name
        self.target_bucket_name = target_bucket_name
        self.public_bucket = public_bucket
        self.buckets_to_create = buckets_to_create.extend([default_bucket, self.source_bucket_name, self.target_bucket_name, self.public_bucket]) if buckets_to_create else [default_bucket, self.source_bucket_name, self.target_bucket_name, self.public_bucket]
        self.logger = logging.getLogger("MinioService")

        # 1. 初始化上传客户端
        self.client = Minio(
            self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
        )
        
        # 2. 初始化签名生成客户端
        self.signer_client = Minio(
            "localhost:9000",
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
            region="us-east-1"
        )
        
        # 3. 初始化存储桶
        self._initialize_buckets()
    
    def _initialize_buckets(self):
        """初始化存储桶及权限"""
        if self.buckets_to_create:
            for bucket in set(self.buckets_to_create): # 使用 set 去重
                self._ensure_bucket_exists(bucket)
                # 如果桶名字包含 public，自动设为公开只读
                if "public" in bucket:
                    self._set_bucket_public(bucket)
                    self.logger.info(f"初始化: 桶 '{bucket}' 已自动设为公开。")
    
    async def _run_async(self, func, *args, **kwargs):
        """将同步操作转换为异步"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    def _ensure_bucket_exists(self, bucket_name: str):
        try:
            if not self.client.bucket_exists(bucket_name):
                self.client.make_bucket(bucket_name)
                self.logger.info(f"创建存储桶成功: {bucket_name}")
        except Exception as e:
            self.logger.error(f"检查存储桶失败: {str(e)}")

    def _set_bucket_public(self, bucket_name: str):
        """
        将指定桶设置为“公开只读”
        """
        try:
            # MinIO/S3 的标准策略 JSON
            policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"AWS": ["*"]}, # 允许任何人
                        "Action": ["s3:GetObject"],  # 只能读/下载
                        "Resource": [f"arn:aws:s3:::{bucket_name}/*"] # 针对该桶下所有文件
                    }
                ]
            }
            # 调用 MinIO 客户端设置策略
            self.client.set_bucket_policy(bucket_name, json.dumps(policy))
            self.logger.info(f"已将桶 {bucket_name} 权限设置为 Public Read")
        except Exception as e:
            self.logger.error(f"设置桶 {bucket_name} 策略失败: {e}")

    # ================= 文本读写 (MarkDown处理等) =================
    async def read_object_text(self, bucket_name: str, object_name: str) -> str:
        """
        [新增] 读取文本文件内容 (用于 Markdown 编辑)
        """
        try:
            # get_object 返回的是 HTTPResponse，需要 read()
            response = await self._run_async(self.client.get_object, bucket_name, object_name)
            try:
                # 读取字节并解码为 utf-8 字符串
                content_bytes = response.read()
                return content_bytes.decode('utf-8')
            finally:
                response.close()
                
        except Exception as e:
            self.logger.error(f"读取文本失败: {e}")
            raise e
        
    async def put_object_text(self, bucket_name: str, object_name: str, content: str, content_type: str = "text/markdown"):
        """
        [新增] 覆盖写入文本内容 (用于保存 Markdown)
        注意：这会直接覆盖原文件
        """
        try:
            # 将字符串转为字节流
            data_bytes = content.encode('utf-8')
            data_stream = io.BytesIO(data_bytes)
            length = len(data_bytes)
            
            await self._run_async(
                self.client.put_object,
                bucket_name=bucket_name,
                object_name=object_name,
                data=data_stream,
                length=length,
                content_type=content_type # 明确类型
            )
            return True
        except Exception as e:
            self.logger.error(f"写入文本失败: {e}")
            raise e

    # ================= 业务方法 (对应原 HTTP 接口逻辑) =================
    async def upload_file_stream(
        self, 
        bucket_name: str, 
        object_name: str, 
        file_data: bytes, 
        content_type: str,
        metadata: Optional[Dict[str, str]] = None
    ):
        """
        [核心] 流式上传文件
        
        Args:
            bucket_name: 目标桶名
            object_name: 文件路径 (如 kb_1/test.pdf)
            file_data: 文件二进制内容
            content_type: MIME 类型
            metadata: (可选) 自定义元数据字典，由 Manager 层传入，这里只负责透传
        """
        target_bucket = bucket_name if bucket_name else self.default_bucket
        
        # 1. 确保桶存在
        await self._run_async(self._ensure_bucket_exists, target_bucket)

        file_stream = io.BytesIO(file_data)
        file_size = len(file_data)

        # 2. 准备元数据 (确保不为 None)
        user_metadata = metadata if metadata else {}
        
        # 3. 执行上传
        # 注意：put_object 是同步阻塞的，必须用 _run_async 包裹
        upload_result = await self._run_async(
            self.client.put_object,
            bucket_name=target_bucket,
            object_name=object_name,
            data=file_stream,
            length=file_size,
            content_type=content_type,
            metadata=user_metadata
        )
        
        return {
            "bucket": target_bucket,
            "object_name": object_name,
            "size": file_size,
            "content_type": content_type,
            "etag": upload_result.etag,
            "version_id": upload_result.version_id
        }

    
    # ================= 通用文件操作 =================
    async def remove_object(self, bucket_name: Optional[str], object_name: str):
        """删除文件"""
        target_bucket = bucket_name if bucket_name else self.default_bucket
        await self._run_async(
            self.client.remove_object,
            bucket_name=target_bucket,
            object_name=object_name
        )
        return {"object_name": object_name}

    async def generate_presigned_url(self, bucket_name: Optional[str], object_name: str, expires: int):
        """获取预签名链接"""
        target_bucket = bucket_name if bucket_name else "pdf-raw"
        url = await self._run_async(
            self.signer_client.get_presigned_url,
            method="GET",
            bucket_name=target_bucket,
            object_name=object_name,
            expires=timedelta(seconds=expires) if expires else timedelta(hours=1)
        )
        return {
            "url": url,
            "bucket": target_bucket,
            "object_name": object_name,
            "expires_seconds": expires
        }

    async def list_bucket_objects(self, bucket_name: Optional[str], prefix: Optional[str] = None):
        """列出文件"""
        target_bucket = bucket_name if bucket_name else self.default_bucket
        
        # 定义内部同步函数以便放入线程池
        def _list_objects_sync():
            objects = self.client.list_objects(target_bucket, prefix=prefix, recursive=True)
            return [
                {
                    "object_name": obj.object_name,
                    "size": obj.size,
                    "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
                    "is_dir": obj.is_dir
                } 
                for obj in objects
            ]

        return await self._run_async(_list_objects_sync)

    async def get_object_stat(self, bucket_name: Optional[str], object_name: str):
        """获取单个文件的详细元数据"""
        target_bucket = bucket_name if bucket_name else self.default_bucket
        
        def _stat_sync():
            # stat_object 会发起 HEAD 请求，获取 Header 中的元数据
            stat = self.client.stat_object(target_bucket, object_name)
            
            # 处理元数据 key，minio SDK 返回的 metadata key 可能是大写或包含前缀
            # 这里做一个清洗，方便前端使用
            clean_meta = {}
            if stat.metadata:
                for k, v in stat.metadata.items():
                    # 去掉 x-amz-meta- 前缀（如果 SDK 没去的话），并转小写
                    # MinIO Python SDK通常会自动处理，直接返回干净的 key，但key通常是原始大小写
                    clean_meta[k.lower()] = v

            return {
                "bucket": stat.bucket_name,
                "object_name": stat.object_name,
                "size": stat.size,
                "last_modified": stat.last_modified.isoformat() if stat.last_modified else None,
                "content_type": stat.content_type,
                "metadata": clean_meta,  # <--- 这里就是我们要的元数据
                "etag": stat.etag
            }

        return await self._run_async(_stat_sync)
    
    # ================= 本地文件互传 (工具方法) =================
    async def internal_download_file(self, bucket: str, object_name: str, file_path: str):
        """下载文件到本地"""
        await self._run_async(self.client.fget_object, bucket, object_name, file_path)

    async def internal_upload_file(self, bucket: str, object_name: str, file_path: str):
        """上传本地文件到 MinIO"""
        await self._run_async(self._ensure_bucket_exists, bucket)
        await self._run_async(self.client.fput_object, bucket, object_name, file_path)