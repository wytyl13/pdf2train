#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/17 12:46
@Author  : weiyutao
@File    : pdf_document_server.py
"""

from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse
from datetime import datetime
import logging
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from fastapi.encoders import jsonable_encoder
import traceback

# 导入刚才写的 Service
from api.service.pdf_document_service import PdfDocumentService
from api.service.minio_service import MinioService
# === Pydantic 请求模型 ===
class PdfDocRequest(BaseModel):
    id: Optional[int] = None
    file_name: Optional[str] = None
    bucket_name: Optional[str] = None
    object_name: Optional[str] = None
    file_size: Optional[int] = None
    
    # 元数据
    page_count: Optional[int] = None
    author: Optional[str] = None
    original_title: Optional[str] = None
    summary: Optional[str] = None
    
    # 状态
    status: Optional[List[int]] = None
    process_error: Optional[str] = None
    
    # 步骤筛选参数
    filter_step_type: Optional[int] = None
    filter_step_status: Optional[List[int]] = None
    
    
    # 用户
    user_name: Optional[str] = None
    page: int = 1, 
    page_size: int = 6
    keyword: Optional[str] = None
    
    
class ContentSaveRequest(BaseModel):
    id: int
    content: str
    

class PdfDocumentServer:
    """PDF 文档接口服务"""

    def __init__(self, pdf_document_service: PdfDocumentService, minio_service: MinioService):
        self.logger = logging.getLogger(self.__class__.__name__)
        # 初始化业务 Service
        self.pdf_document_service = pdf_document_service
        self.minio_service = minio_service
        self.minio_base_url = self.minio_service.endpoint
        self.protocol = "http://" if "localhost" in self.minio_base_url else "https://"
    
    def register_routes(self, app: FastAPI):
        """注册路由"""
        app.get("/api/pdf_document")(self.get_pdf_documents)
        app.post("/api/pdf_document/list")(self.post_get_pdf_documents)
        app.post("/api/pdf_document/save")(self.save_pdf_document)
        app.post("/api/pdf_document/update")(self.update_pdf_document)
        app.post("/api/pdf_document/delete")(self.delete_pdf_document)
        app.get("/api/pdf_document/statistics")(self.get_statistics)
        app.get("/api/pdf_document/chunk_count")(self.get_chunk_count)
        
        app.get("/api/pdf_document/content")(self.get_doc_content)
        app.get("/api/pdf_document/original_chunk/content")(self.get_original_chunk_content)
        app.post("/api/pdf_document/content/save")(self.save_doc_content)

    # === 辅助方法：生成统一响应 ===
    def _response(self, success: bool, message: str = "", data: Any = None, code: int = 200):
        return JSONResponse(
            status_code=code,
            content={
                "success": success,
                "message": message,
                "data": jsonable_encoder(data) if data is not None else None,
                "timestamp": datetime.now().isoformat()
            }
        )

    # === 接口实现 ===

    async def get_chunk_count(self, doc_id: int):
        try:
            chunk_count = await self.pdf_document_service.get_chunk_count(doc_id)
        except Exception as e:
            self.logger.error(f"获取切片数量失败 doc_id={doc_id}: {e}")
            return self._response(True, "查询成功", 0)
        return self._response(True, "查询成功", chunk_count)
    
    
    async def get_pdf_documents(
        self,
        id: Optional[int] = None,
        file_name: Optional[str] = None,
        bucket_name: Optional[str] = None,
        status: Optional[List[int]] = None,
        filter_step_type: Optional[int] = None,
        filter_step_status: Optional[List[int]] = None,
        keyword: Optional[str] = None,
        page: int = 1, 
        page_size: int = 6
    ):
        """GET 方式查询列表"""
        try:
            # 1. 组装查询条件
            condition = {}
            if id is not None: condition["id"] = id
            if file_name: condition["file_name"] = file_name
            if bucket_name: condition["bucket_name"] = bucket_name
            if status is not None: condition["status"] = status

            base_url = f"{self.pdf_document_service.protocol}{self.minio_base_url}"

            # 2. 调用 Service
            result_data = await self.pdf_document_service.get_document_list(
                condition, 
                page, 
                page_size,
                filter_step_type=filter_step_type,
                filter_step_status=filter_step_status,
                keyword=keyword
            )
            
            # 2. 直接返回
            return self._response(True, "查询成功", result_data)
        except Exception as e:
            self.logger.error(f"查询接口异常: {str(e)}")
            return self._response(False, f"查询失败: {str(e)}", code=500)


    async def get_statistics(self):
        """GET 获取文档统计概览"""
        try:
            # 调用 Service 获取聚合后的数据
            stats = await self.pdf_document_service.get_dashboard_statistics()
            
            return self._response(
                success=True, 
                message="获取统计成功", 
                data=stats
            )
        except Exception as e:
            self.logger.error(f"统计接口异常: {traceback.format_exc()}")
            return self._response(False, f"获取统计失败: {str(e)}", code=500)


    async def post_get_pdf_documents(
        self, 
        request: PdfDocRequest,
    ):
        """POST 方式查询列表"""
        return await self.get_pdf_documents(
            id=request.id,
            file_name=request.file_name,
            bucket_name=request.bucket_name,
            status=request.status,
            page=request.page,
            keyword=request.keyword,
            page_size=request.page_size,
            filter_step_type=request.filter_step_type,
            filter_step_status=request.filter_step_status
        )


    async def save_pdf_document(self, request: PdfDocRequest):
        """保存 PDF 记录"""
        try:
            # 1. 参数校验
            if not request.file_name or not request.bucket_name or not request.object_name:
                return self._response(False, "缺少必要参数(file_name, bucket_name, object_name)", code=400)

            # 2. 组装数据 (过滤掉 None 值)
            insert_data = request.dict(exclude_unset=True)
            # 移除 id，因为是新建
            if "id" in insert_data: del insert_data["id"]

            # 3. 调用 Service
            result = await self.pdf_document_service.create_document(insert_data)
            return self._response(True, "保存成功", result)
        except Exception as e:
            self.logger.error(f"保存接口异常: {traceback.format_exc()}")
            return self._response(False, f"保存失败: {str(e)}", code=500)


    async def update_pdf_document(self, request: PdfDocRequest):
        """更新 PDF 记录"""
        try:
            if not request.id:
                return self._response(False, "缺少文档ID", code=400)

            update_data = request.dict(
                exclude_unset=True, 
                exclude={"id", "object_name", "bucket_name", "create_time", "user_name"} 
            )

            if not update_data:
                return self._response(False, "没有要更新的字段", code=400)

            # 2. 调用 Service
            success = await self.pdf_document_service.update_document(request.id, update_data)
            return self._response(True, "更新成功") if success else self._response(False, "ID不存在", code=400)
        except Exception as e:
            self.logger.error(f"更新接口异常: {str(e)}")
            return self._response(False, f"更新失败: {str(e)}", code=500)


    async def delete_pdf_document(self, request: PdfDocRequest):
        """删除 PDF 记录"""
        try:
            # 1. 简单的参数校验
            if not request.id:
                return self._response(False, "缺少文档ID", code=400)
            # 2. 一行代码调用 Service
            success = await self.pdf_document_service.delete_document(request.id)
            
            # 3. 封装返回结果
            return self._response(True, "删除成功") if success else self._response(False, "删除失败", code=404)
        except Exception as e:
            self.logger.error(f"删除接口异常: {str(e)}")
            return self._response(False, f"删除失败: {str(e)}", code=500)
        
    
    async def get_doc_content(self, id: int):
        try:
            content = await self.pdf_document_service.get_markdown_content(id)
            return self._response(True, "获取成功", data={"content": content})
        except ValueError as e:
            return self._response(False, str(e), code=404)
        except Exception as e:
            self.logger.error(f"获取内容异常: {str(e)}")
            return self._response(False, f"系统错误: {str(e)}", code=500)


    async def get_original_chunk_content(self, id: int):
        try:
            content = await self.pdf_document_service.get_chunk_content(id)
            return self._response(True, "获取成功", data={"content": content})
        except ValueError as e:
            return self._response(False, str(e), code=404)
        except Exception as e:
            self.logger.error(f"获取内容异常: {str(e)}")
            return self._response(False, f"系统错误: {str(e)}", code=500)


    async def save_doc_content(self, request: ContentSaveRequest):
        try:
            await self.pdf_document_service.save_markdown_content(request.id, request.content)
            return self._response(True, "保存成功")
        except ValueError as e:
            return self._response(False, str(e), code=404)
        except Exception as e:
            self.logger.error(f"保存内容异常: {str(e)}")
            return self._response(False, f"系统错误: {str(e)}", code=500)