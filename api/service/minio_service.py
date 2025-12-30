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
import uuid




class MinioService:
    """
    MinIO 核心业务逻辑层
    负责：底层 MinIO SDK 操作，不含 HTTP 响应封装
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

        # 初始上传客户端
        self.client = Minio(
            self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
        )
        
        # 初始化签名生成客户端
        self.signer_client = Minio(
            "localhost:9000",
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
            region="us-east-1"
        )
        
        # 确保存储桶存在
        if self.buckets_to_create:
            for bucket in self.buckets_to_create:
                self._ensure_bucket_exists(bucket)
                if "public" in bucket:
                    self._set_bucket_public(bucket)
                    self.logger.info(f"初始化: 桶 '{bucket}' 已自动设为公开。")
                else:
                    self.logger.info(f"初始化: 桶 '{bucket}' 保持默认(私有)。")
        
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
    def _extract_cover_image(self, file_data: bytes) -> bytes:
        """
        提取 PDF 第一页为 JPEG 图片字节流
        """
        try:
            # 打开 PDF (从内存)
            with fitz.open(stream=file_data, filetype="pdf") as doc:
                if doc.page_count < 1:
                    return None
                
                # 获取第一页
                page = doc[0]
                
                # 渲染为像素图 (matrix=fitz.Matrix(1, 1) 代表原缩放，2,2 则清晰度翻倍)
                pix = page.get_pixmap(matrix=fitz.Matrix(1, 1))
                
                # 转为 JPEG 字节
                img_bytes = pix.tobytes("jpeg")
                return img_bytes
        except Exception as e:
            self.logger.error(f"封面提取失败: {e}")
            return None


    async def upload_file_stream(self, bucket_name: str, object_name: str, file_data: bytes, content_type: str):
        """处理文件流上传"""
        target_bucket = bucket_name if bucket_name else self.default_bucket
        
        # 确保 Bucket 存在
        await self._run_async(self._ensure_bucket_exists, target_bucket)

        file_stream = io.BytesIO(file_data)
        file_size = len(file_data)

        user_metadata = {}
        is_pdf = False
        if content_type and "pdf" in content_type.lower():
            is_pdf = True
        elif object_name.lower().endswith(".pdf"):
            is_pdf = True
        cover_info = None
        cover_object_name = None
        
        if is_pdf:
            # 1. Extract Metadata
            pdf_meta = await self._run_async(self._extract_pdf_metadata, file_data)
            user_metadata.update(pdf_meta)
            user_metadata["file-category"] = "document"
        
            # 2. Extract & Upload Cover
            cover_bytes = await self._run_async(self._extract_cover_image, file_data)
            if cover_bytes:
                base_name = os.path.splitext(os.path.basename(object_name))[0]
                cover_object_name = f"covers/{base_name}_cover.jpg"
                await self._run_async(
                    self.client.put_object,
                    bucket_name=self.public_bucket,
                    object_name=cover_object_name, # 使用这个路径
                    data=io.BytesIO(cover_bytes),
                    length=len(cover_bytes),
                    content_type="image/jpeg"
                )
                cover_info = {
                    "bucket": self.public_bucket,
                    "path": cover_object_name
                }
        # 异步执行上传
        await self._run_async(
            self.client.put_object,
            bucket_name=target_bucket,
            object_name=object_name,
            data=file_stream,
            length=file_size,
            content_type=content_type,
        )
        
        return {
            "bucket": target_bucket,
            "object_name": object_name,
            "size": file_size,
            "metadata": user_metadata, # 返回提取到的元数据
            "content_type": content_type,
            "cover_info": cover_info
        }


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


    # ================= 内部专用方法 (供 Pdf2MdService 使用) =================
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
    
    
    def _extract_pdf_metadata(self, file_data: bytes) -> Dict[str, str]:
        """
        提取 PDF 元数据
        返回字典的 Key 尽量遵循 MinIO Header 规范 (kebab-case)
        """
        metadata = {}
        try:
            with io.BytesIO(file_data) as stream:
                reader = PdfReader(stream)
                
                # 1. 页数 (对应 DB: page_count)
                num_pages = len(reader.pages)
                metadata["pages"] = str(num_pages) 

                # 2. 标准信息
                info = reader.metadata
                if info:
                    # 作者 (对应 DB: author)
                    if info.author:
                        # 简单清洗，去除空字符
                        metadata["author"] = str(info.author).strip()
                    
                    # 标题 (对应 DB: original_title)
                    # MinIO 推荐用 original-title, 数据库用 original_title
                    if info.title:
                        metadata["original-title"] = str(info.title).strip()

                    # 我们可以把 Creator 放在 extra_data 里，这里先提取出来
                    if info.creator:
                        metadata["creator"] = str(info.creator).strip()

            self.logger.info(f"PDF 元数据提取成功: {metadata}")
            return metadata
        except Exception as e:
            self.logger.warning(f"PDF 元数据提取失败: {str(e)}")
            return {}
    
    
    async def internal_download_file(self, bucket: str, object_name: str, file_path: str):
        """下载文件到本地"""
        await self._run_async(self.client.fget_object, bucket, object_name, file_path)


    async def internal_upload_file(self, bucket: str, object_name: str, file_path: str):
        """上传本地文件到 MinIO"""
        await self._run_async(self._ensure_bucket_exists, bucket)
        await self._run_async(self.client.fput_object, bucket, object_name, file_path)