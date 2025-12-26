#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/15 17:49
@Author  : weiyutao
@File    : minio_server.py
"""

from fastapi import FastAPI, UploadFile, File, Form, APIRouter
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
from pydantic import BaseModel
from typing import Optional
import os
import json
import uuid


from api.service.minio_service import MinioService # 导入业务层
from api.service.pdf_document_service import PdfDocumentService


class FileOperationInfo(BaseModel):
    bucket_name: Optional[str] = None
    object_name: Optional[str] = None
    expires: Optional[int] = 3600

class MinioServer:
    """
    MinIO HTTP 接口服务类
    负责：参数解析、调用 Service、封装 JSON 响应
    """
    def __init__(self, service: MinioService, pdf_document_service: PdfDocumentService = None):
        self.service = service # 注入业务实例
        self.pdf_document_service = pdf_document_service
        self.router = APIRouter()
        self._register_routes()
        self.logger = service.logger # 复用 logger
        
    def _register_routes(self):
        self.router.add_api_route("/api/storage/upload", self.upload_file, methods=["POST"])
        self.router.add_api_route("/api/storage/delete", self.delete_file, methods=["POST"])
        self.router.add_api_route("/api/storage/url", self.get_presigned_url, methods=["POST"])
        self.router.add_api_route("/api/storage/list", self.list_files, methods=["POST"]) # 你之前提到的漏掉的接口
        self.router.add_api_route("/api/storage/detail", self.get_file_detail, methods=["POST"])
    # ================= 接口实现 =================

    async def upload_file(
        self,
        file: UploadFile = File(...),
        bucket_name: Optional[str] = Form(None),
        file_name: Optional[str] = Form(None)
    ):
        """POST请求 - 上传文件"""
        try:
            # 1. Determine File Name
            file_name = file_name if file_name else file.filename
            ext = os.path.splitext(file.filename)[1].lower()
            if not ext: ext = ".bin" # 兜底后缀

            unique_id = str(uuid.uuid4())
            generated_object_name = f"{unique_id}{ext}"
            
            # 2. Determine Bucket
            target_bucket = bucket_name if bucket_name else "pdf-raw"
            file_data = await file.read()

            # 3. Call Service
            result_data = await self.service.upload_file_stream(
                bucket_name=target_bucket,  # 使用处理后的 target_bucket
                object_name=generated_object_name,  # 使用处理后的 target_filename
                file_data=file_data,
                content_type=file.content_type
            )

            # 4. Construct DB Record
            meta = result_data.get("metadata", {})
            cover_info_data = result_data.get("cover_info")
            
            if cover_info_data:
                pass
            
            db_data = {
                "file_name": file_name,
                "bucket_name": result_data["bucket"],
                "object_name": result_data["object_name"],
                "file_size": result_data["size"],
                "content_type": result_data["content_type"],
                "user_name": "user_name",
                "status": 0, # 0 表示上传成功未处理 (PENDING)
                
                # 映射 PDF 元数据 (注意类型转换)
                "page_count": int(meta.get("pages", 0)), # str -> int
                "author": meta.get("author"),
                "original_title": meta.get("original-title"), # kebab-case -> snake_case
                "cover_info": cover_info_data,
                
                "process_error": None,
            }
            
            # 5. Create in DB
            if self.pdf_document_service:
                await self.pdf_document_service.create_document(db_data)
            
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "message": "文件上传成功",
                    "data": result_data, # 包含 url, bucket, size 等
                    "timestamp": datetime.now().isoformat()
                }
            )
        except Exception as e:
            self.logger.error(f"文件上传失败: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"success": False, "message": f"文件上传失败: {str(e)}", "timestamp": datetime.now().isoformat()}
            )

    async def delete_file(self, file_info: FileOperationInfo):
        """
        POST请求 - 纯物理删除 MinIO 文件
        注意：此方法不涉及任何业务表（如 pdf_document）的联动清理
        """
        try:
            # 1. 参数校验
            if not file_info.object_name or not file_info.bucket_name:
                return self._error_response(400, "请提供 bucket_name 和 object_name")

            # 2. 直接调用 MinIO 服务执行物理删除
            # MinIO/S3 的特性是：即使文件本身不存在，调用 remove_object 也不会报错（幂等性）
            # 只有连接失败或权限不足时才会抛出异常
            await self.service.remove_object(
                bucket_name=file_info.bucket_name,
                object_name=file_info.object_name
            )
            
            self.logger.info(f"MinIO文件删除指令已执行: {file_info.bucket_name}/{file_info.object_name}")

            # 3. 返回成功响应
            return JSONResponse(
                status_code=200,
                content={
                    "success": True, 
                    "message": "文件删除成功", 
                    "data": {
                        "bucket": file_info.bucket_name,
                        "object": file_info.object_name
                    }, 
                    "timestamp": datetime.now().isoformat()
                }
            )
        except Exception as e:
            self.logger.error(f"MinIO删除操作异常: {str(e)}")
            return self._error_response(500, f"删除文件失败: {str(e)}")

    async def get_presigned_url(self, file_info: FileOperationInfo):
        """POST请求 - 获取预签名链接"""
        try:
            if not file_info.object_name:
                return self._error_response(400, "请提供文件名")

            # 调用业务层
            result_data = await self.service.generate_presigned_url(
                bucket_name=file_info.bucket_name,
                object_name=file_info.object_name,
                expires=file_info.expires
            )

            return JSONResponse(
                status_code=200,
                content={
                    "success": True, 
                    "message": "获取链接成功", 
                    "data": result_data, 
                    "timestamp": datetime.now().isoformat()
                }
            )
        except Exception as e:
            self.logger.error(f"获取链接失败: {str(e)}")
            return self._error_response(500, f"获取链接失败: {str(e)}")

    async def list_files(self, file_info: FileOperationInfo):
        """POST请求 - 列出文件"""
        try:
            # 调用业务层 (object_name 作为 prefix)
            files_list = await self.service.list_bucket_objects(
                bucket_name=file_info.bucket_name,
                prefix=file_info.object_name 
            )

            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "data": files_list,
                    "count": len(files_list),
                    "timestamp": datetime.now().isoformat()
                }
            )
        except Exception as e:
            self.logger.error(f"获取文件列表失败: {str(e)}")
            return self._error_response(500, f"获取文件列表失败: {str(e)}")

    async def get_file_detail(self, file_info: FileOperationInfo):
        """POST请求 - 获取文件详情（含元数据）"""
        try:
            if not file_info.object_name:
                return self._error_response(400, "请提供文件名")

            # 调用业务层
            result_data = await self.service.get_object_stat(
                bucket_name=file_info.bucket_name,
                object_name=file_info.object_name
            )

            return JSONResponse(
                status_code=200,
                content={
                    "success": True, 
                    "message": "获取文件详情成功", 
                    "data": result_data, 
                    "timestamp": datetime.now().isoformat()
                }
            )
        except Exception as e:
            # 文件不存在时 stat_object 会抛出异常
            self.logger.error(f"获取文件详情失败: {str(e)}")
            return self._error_response(404, f"文件不存在或获取失败: {str(e)}")

    def _error_response(self, code: int, message: str):
        """辅助方法：生成错误响应"""
        return JSONResponse(
            status_code=code,
            content={
                "success": False, 
                "message": message, 
                "timestamp": datetime.now().isoformat()
            }
        )

    def register_routes(self, app: FastAPI):
        """注册路由到 FastAPI 实例"""
        app.include_router(self.router)