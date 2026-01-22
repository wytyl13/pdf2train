#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/17 12:46
@Author  : weiyutao
@File    : pdf_document_server.py
"""

from fastapi import FastAPI, APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from datetime import datetime
import logging
from pydantic import BaseModel, Field
from typing import Optional, Any, List, Union
from fastapi.encoders import jsonable_encoder
import traceback
import urllib.parse
from io import StringIO
from fastapi import BackgroundTasks

# 导入刚才写的 Service
from pdf2train.api.service.base.pdf_document_service import PdfDocumentService
from pdf2train.api.service.base.minio_service import MinioService


# === Pydantic 请求模型 ===
class PdfDocRequest(BaseModel):
    doc_id: Optional[int] = None
    file_name: Optional[str] = None
    bucket_name: Optional[str] = None
    object_name: Optional[str] = None
    file_size: Optional[int] = None
    
    kb_id: Optional[Union[int, List[int]]] = None
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
    
    instruction_gen_llm_config: Optional[str] = None
    h_title_llm_config: Optional[str] = None
    embedding_llm_config: Optional[str] = None
    # 用户
    user_name: Optional[str] = None
    page: int = 1, 
    page_size: int = 6
    keyword: Optional[str] = None


class PdfDocUpdateRequest(BaseModel):
    """
    专门用于更新 PDF 文档元数据的请求模型
    """
    doc_id: int = Field(..., description="需要更新的文档 ID")
    kb_id: Optional[int] = Field(None, description="绑定的知识库 ID")
    embedding_llm_config: Optional[str] = Field(None, description="嵌入模型配置名称")
    instruction_gen_llm_config: Optional[str] = Field(None, description="指令生成模型配置")
    h_title_llm_config: Optional[str] = Field(None, description="多级标题生成模型配置")

    # 3. 普通元数据 (根据业务需要添加)
    file_name: Optional[str] = None
    author: Optional[str] = None
    original_title: Optional[str] = None
    summary: Optional[str] = None
    
    confirm_sync: bool = Field(
        default=False, 
        description="是否确认强制同步配置。False=仅检查冲突; True=执行更新(可能包含重建向量)"
    )

    class Config:
        schema_extra = {
            "example": {
                "doc_id": 10086,
                "kb_id": 5,
                "author": "Weiyutao",
                "embedding_llm_config": "local-bge-m3",
                "confirm_sync": False
            }
        }


class ModelConflictError(Exception):
    def __init__(self, conflict_type: str, message: str, detail: str):
        self.conflict_type = conflict_type  # "RE_EMBED_WARNING" 或 "SYNC_INFO"
        self.message = message
        self.detail = detail

class UnsignedRequest(BaseModel):
    page: int = 1, 
    page_size: int = 6
    keyword: Optional[str] = None


class ContentSaveRequest(BaseModel):
    doc_id: int
    content: str

class DocCountByKbId(BaseModel):
    kb_id: int
    

class PdfDocumentServer:
    """PDF 文档接口服务"""

    def __init__(self, pdf_document_service: PdfDocumentService, minio_service: MinioService):
        self.logger = logging.getLogger(self.__class__.__name__)
        # 初始化业务 Service
        self.pdf_document_service = pdf_document_service
        self.minio_service = minio_service
    
    def register_routes(self, app: FastAPI):
        """注册路由"""
        router = APIRouter(tags=["PDF Document Server"])
        router.get("/api/pdf_document")(self.get_pdf_documents)
        router.post("/api/pdf_document/list")(self.post_get_pdf_documents)
        router.post("/api/pdf_document/save")(self.save_pdf_document)
        router.post("/api/pdf_document/update")(self.update_pdf_document)
        router.post("/api/pdf_document/delete")(self.delete_pdf_document)
        router.get("/api/pdf_document/statistics")(self.get_statistics)
        router.get("/api/pdf_document/chunk_count")(self.get_chunk_count)
        router.post("/api/pdf_document/unassigned")(self.get_unassigned_docs)
        
        router.get("/api/pdf_document/content")(self.get_doc_content)
        router.get("/api/pdf_document/original_chunk/content")(self.get_original_chunk_content)
        router.post("/api/pdf_document/content/save")(self.save_doc_content)
        router.post("/api/pdf_document/export_books_jsonl")(self.export_books_jsonl)
        router.post("/api/pdf_document/get_doc_count_by_kb_id")(self.get_doc_count_by_kb_id)

        app.include_router(router)

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
        doc_id: Optional[int] = None,
        file_name: Optional[str] = None,
        bucket_name: Optional[str] = None,
        kb_id: Optional[Union[int, List[int]]] = None,
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
            if doc_id is not None: condition["id"] = doc_id
            if file_name: condition["file_name"] = file_name
            if bucket_name: condition["bucket_name"] = bucket_name
            if status is not None: condition["status"] = status
            if kb_id is not None: condition["kb_id"] = kb_id
            # 2. 调用 Service
            result_data = await self.pdf_document_service.get_document_list(
                condition, 
                page, 
                page_size,
                filter_step_type=filter_step_type,
                filter_step_status=filter_step_status,
                keyword=keyword,
                kb_id=kb_id
            )
            
            # 2. 直接返回
            return self._response(True, "查询成功", result_data)
        except Exception as e:
            self.logger.error(f"查询接口异常: {str(e)}")
            return self._response(False, f"查询失败: {str(e)}", code=500)


    async def export_books_jsonl(
        self,
        request: PdfDocRequest = None,
    ):
        """
        API 接口：下载 JSONL 格式的书籍清单
        """
        # 1. 获取 JSONL 字符串内容
        jsonl_content = await self.pdf_document_service.export_books_jsonl(
            filter_step_type=request.filter_step_type,
            filter_step_status=request.filter_step_status,
            keyword=request.keyword,
            kb_id=request.kb_id
        )
        
        # 2. 构造文件名 (URL 编码防止中文乱码)
        filename = "书籍清单_export.jsonl"
        encoded_filename = urllib.parse.quote(filename)
        
        # 3. 创建内存流
        stream = StringIO(jsonl_content)
        
        # 4. 返回流式响应，触发下载
        return StreamingResponse(
            stream,
            media_type="application/x-ndjson", # JSONL 标准 MIME 类型
            headers={
                "Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}"
            }
        )

    
    async def get_doc_count_by_kb_id(
        self, 
        request: DocCountByKbId,
    ):
        count = await self.pdf_document_service.get_doc_count_by_kb_id(request.kb_id)
        return self._response(True, "查询成功", count)
    
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


    async def get_unassigned_docs(
        self, 
        request: UnsignedRequest
    ):
        """
        获取可用文档列表 (未加入任何知识库的文档)
        """
        try:
            data = await self.pdf_document_service.get_unassigned_documents(
                page=request.page, 
                page_size=request.page_size, 
                keyword=request.keyword
            )
            return self._response(True, "获取成功", data)
        except Exception as e:
            return self._response(False, f"获取失败: {str(e)}", code=500)


    async def post_get_pdf_documents(
        self, 
        request: PdfDocRequest,
    ):
        """POST 方式查询列表"""
        return await self.get_pdf_documents(
            doc_id=request.doc_id,
            file_name=request.file_name,
            bucket_name=request.bucket_name,
            kb_id=request.kb_id,
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
            if "doc_id" in insert_data: del insert_data["doc_id"]

            # 3. 调用 Service
            result = await self.pdf_document_service.create_document(insert_data)
            return self._response(True, "保存成功", result)
        except Exception as e:
            self.logger.error(f"保存接口异常: {traceback.format_exc()}")
            return self._response(False, f"保存失败: {str(e)}", code=500)


    async def update_pdf_document(
        self, 
        request: PdfDocUpdateRequest,
        background_tasks: BackgroundTasks
    ):
        """更新 PDF 记录"""
        try:
            if not request.doc_id:
                return self._response(False, "缺少文档ID", code=400)

            update_data = request.dict(
                exclude_unset=True, 
                exclude={"id", "object_name", "bucket_name", "create_time", "user_name", "confirm_sync"} 
            )

            if not update_data:
                return self._response(False, "没有要更新的字段", code=400)

            # 2. 调用 Service
            success = await self.pdf_document_service.update_document(
                doc_id=request.doc_id, 
                data=update_data,
                confirm_sync=request.confirm_sync,
                background_tasks=background_tasks
            )
            return self._response(True, "更新成功") if success else self._response(False, "ID不存在", code=400)
        except ModelConflictError as e:
            return JSONResponse(
                status_code=409,
                content={
                    "success": False, 
                    "code": 409,
                    "type": e.conflict_type,  # 前端根据这个判断弹窗类型
                    "message": e.message,
                    "detail": e.detail,
                    "timestamp": datetime.now().isoformat()
                }
            )
        except Exception as e:
            self.logger.error(f"更新接口异常: {str(e)}")
            return self._response(False, f"更新失败: {str(e)}", code=500)


    async def delete_pdf_document(self, request: PdfDocRequest):
        """删除 PDF 记录"""
        try:
            # 1. 简单的参数校验
            if not request.doc_id:
                return self._response(False, "缺少文档ID", code=400)
            # 2. 一行代码调用 Service
            success = await self.pdf_document_service.delete_document(request.doc_id)
            
            # 3. 封装返回结果
            return self._response(True, "删除成功") if success else self._response(False, "删除失败", code=404)
        except Exception as e:
            self.logger.error(f"删除接口异常: {str(e)}")
            return self._response(False, f"删除失败: {str(e)}", code=500)
        
    
    async def get_doc_content(self, doc_id: int):
        try:
            content = await self.pdf_document_service.get_markdown_content(doc_id)
            return self._response(True, "获取成功", data={"content": content})
        except ValueError as e:
            return self._response(False, str(e), code=404)
        except Exception as e:
            self.logger.error(f"获取内容异常: {str(e)}")
            return self._response(False, f"系统错误: {str(e)}", code=500)


    async def get_original_chunk_content(self, doc_id: int):
        try:
            content = await self.pdf_document_service.get_chunk_content(doc_id)
            return self._response(True, "获取成功", data={"content": content})
        except ValueError as e:
            return self._response(False, str(e), code=404)
        except Exception as e:
            self.logger.error(f"获取内容异常: {str(e)}")
            return self._response(False, f"系统错误: {str(e)}", code=500)


    async def save_doc_content(self, request: ContentSaveRequest):
        try:
            await self.pdf_document_service.save_markdown_content(request.doc_id, request.content)
            return self._response(True, "保存成功")
        except ValueError as e:
            return self._response(False, str(e), code=404)
        except Exception as e:
            self.logger.error(f"保存内容异常: {str(e)}")
            return self._response(False, f"系统错误: {str(e)}", code=500)