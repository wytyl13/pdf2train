#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/24 18:53
@Author  : weiyutao
@File    : document_chunk_server.py
"""

from fastapi import FastAPI, APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.encoders import jsonable_encoder
from datetime import datetime
import logging
from pydantic import BaseModel, Field
from typing import Optional, Any, List
import traceback
import json
import io
import urllib.parse
from typing import Dict

# 导入业务 Service
from pdf2train.api.service.base.document_chunk_service import DocumentChunkService


# === Pydantic 请求模型 ===

class ChunkListRequest(BaseModel):
    """查询切片列表请求"""
    doc_id: int = Field(..., description="文档ID (必填)")
    chunk_id: Optional[str] = None
    page: int = 1
    page_size: int = 20
    keyword: Optional[str] = None


class ChunkUpdateRequest(BaseModel):
    """更新切片内容请求"""
    chunk_id: str = Field(..., description="切片UUID")
    content: Optional[str] = Field(None, min_length=1, description="新的切片内容")
    meta_info: Optional[Dict[str, Any]] = Field(None, description="新的元数据字典")


class ChunkDeleteRequest(BaseModel):
    """删除单个切片请求"""
    chunk_id: str = Field(..., description="切片UUID")


class ExportPretrainRequest(BaseModel):
    """导出预训练数据请求"""
    doc_ids: List[int] = Field(..., description="需要导出的文档ID列表")
    filename: str = Field("pretrain_corpus.txt", description="下载文件的名称 (包含扩展名)")
    
    
class ExportPretrainByKbRequest(BaseModel):
    """根据知识库ID导出预训练数据请求"""
    kb_ids: List[int] = Field(..., description="知识库ID列表 (通常是UUID字符串)")
    filename: str = Field("kb_corpus.txt", description="下载文件的名称")
    


class DocumentChunkServer:
    """
    文档切片 (Chunk) 接口服务
    负责处理前端关于 Knowledge Base 详情页的请求
    """

    def __init__(self, document_chunk_service: DocumentChunkService):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.document_chunk_service = document_chunk_service

    def register_routes(self, app: FastAPI):
        """注册路由"""
        router = APIRouter(tags=["Document Chunk Server"])
        # 查询列表 (Knowledge Base 详情页主要接口)
        router.post("/api/document_chunk/list")(self.get_chunk_list)
        
        # 更新切片内容 (人工修正)
        router.post("/api/document_chunk/update")(self.update_chunk)
        
        # 删除单个切片 (人工清洗)
        router.post("/api/document_chunk/delete")(self.delete_chunk)

        # 删除某个id的所有切片
        router.post("/api/document_chunk/delete_by_id")(self.delete_chunk_by_id)
        
        # 下载最新的json chunk数据
        router.get("/api/document_chunk/download/{doc_id}")(self.download_latest_json)
        
        # 获取最新的json内容
        router.get("/api/document_chunk/preview/{doc_id}")(self.get_chunk_json_content)

        # 流式下载预训练数据根据doc_ids
        router.post("/api/document_chunk/download/stream-pretrain")(self.download_stream_pretrain)
        
        # 流式下载预训练数据根据kb_ids
        router.post("/api/document_chunk/download/stream-pretrain-by-kb")(self.download_stream_pretrain_by_kb)
        
        app.include_router(router)

    # === 辅助方法：生成统一响应 (与 PdfDocumentServer 保持一致) ===
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
    async def download_stream_pretrain(self, request: ExportPretrainRequest):
        """
        POST 流式下载预训练数据
        不生成临时文件，一边查库拼接一边返回
        """
        try:
            # 1. 校验
            if not request.doc_ids:
                return self._response(False, "文档ID列表不能为空", code=400)

            # 2. 处理文件名
            import os
            base_name = os.path.splitext(request.filename)[0]
            filename = f"{base_name}.jsonl"
            encoded_filename = urllib.parse.quote(filename)
            encoded_filename = urllib.parse.quote(filename)

            # 3. 获取生成器 (调用 Service 层的流式生成方法)
            # 注意：请确保 Service 层已经实现了 generate_pretrain_content_stream 方法
            content_generator = self.document_chunk_service.generate_pretrain_content_stream(request.doc_ids)

            # 4. 返回 StreamingResponse
            return StreamingResponse(
                content_generator,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}",
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no"  # 关键：禁用 Nginx 缓冲，防止大文件超时
                }
            )

        except Exception as e:
            self.logger.error(f"流式下载预训练数据失败: {traceback.format_exc()}")
            # 流式响应开始后很难返回 JSON 错误，但在开始前可以
            return self._response(False, f"下载启动失败: {str(e)}", code=500)
    
    
    async def download_stream_pretrain_by_kb(self, request: ExportPretrainByKbRequest):
        """
        POST 根据 KB_ID 列表流式下载预训练数据
        """
        try:
            # 1. 校验
            if not request.kb_ids:
                return self._response(False, "KB ID列表不能为空", code=400)

            # 2. 处理文件名
            import os
            base_name = os.path.splitext(request.filename)[0]
            filename = f"{base_name}.jsonl"
            encoded_filename = urllib.parse.quote(filename)

            # 3. 获取生成器
            content_generator = self.document_chunk_service.generate_pretrain_content_stream_by_kb_ids(request.kb_ids)

            # 4. 返回流式响应
            return StreamingResponse(
                content_generator,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}",
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no"
                }
            )

        except Exception as e:
            self.logger.error(f"按KB导出下载失败: {traceback.format_exc()}")
            return self._response(False, f"下载启动失败: {str(e)}", code=500)
    
    
    async def download_latest_json(self, doc_id: int):
        """GET 下载最新的 Chunk JSON (基于数据库实时生成)"""
        try:
            # 1. 从数据库获取最新数据
            data_list = await self.document_chunk_service.export_chunks_as_json(doc_id)
            
            if not data_list:
                return self._response(False, "未找到数据", code=404)

            # 2. 序列化为 JSON 字符串
            json_str = json.dumps(data_list, ensure_ascii=False, indent=2)
            
            # 3. 转换为字节流
            stream = io.BytesIO(json_str.encode("utf-8"))
            
            # 4. 生成文件名 (这里可以查一下 doc 的 filename，为了演示简写了)
            filename = f"doc_{doc_id}_latest.json"
            encoded_filename = urllib.parse.quote(filename)
            
            # 5. 返回流式响应
            return StreamingResponse(
                stream, 
                media_type="application/json", 
                headers={
                    "Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}"
                }
            )

        except Exception as e:
            self.logger.error(f"下载失败: {traceback.format_exc()}")
            return self._response(False, f"下载失败: {str(e)}", code=500)


    async def get_chunk_json_content(self, doc_id: int):
        """
        GET 获取 JSON 内容 (用于前端预览/编辑器展示)
        返回标准 JSON 结构，而不是文件流
        """
        try:
            # 1. 复用同一个 Service 方法
            data_list = await self.document_chunk_service.export_chunks_as_json(doc_id)
            
            # 2. 直接返回标准响应结构
            # 这样前端 axios 拿到的直接就是 JSON 对象，可以直接渲染在编辑器里
            return self._response(True, "获取成功", data=data_list)

        except Exception as e:
            self.logger.error(f"获取 JSON 内容异常: {traceback.format_exc()}")
            return self._response(False, f"获取失败: {str(e)}", code=500)


    async def get_chunk_list(self, request: ChunkListRequest):
        """POST 查询某文档下的切片列表"""
        try:
            # 1. 简单校验
            if not request.doc_id:
                return self._response(False, "缺少文档ID", code=400)

            # 2. 调用 Service
            result = await self.document_chunk_service.get_chunk_list(
                doc_id=request.doc_id,
                chunk_id=request.chunk_id,
                page=request.page,
                page_size=request.page_size,
                keyword=request.keyword
            )
            
            return self._response(True, "查询成功", result)

        except Exception as e:
            self.logger.error(f"查询 Chunk 列表异常: {traceback.format_exc()}")
            return self._response(False, f"查询失败: {str(e)}", code=500)


    async def update_chunk(self, request: ChunkUpdateRequest):
        """POST 更新切片信息 (内容 / 元数据)"""
        try:
            # 1. 校验参数：必须至少更新一项
            if request.content is None and request.meta_info is None:
                return self._response(False, "内容和元数据不能同时为空", code=400)

            if not request.chunk_id:
                return self._response(False, "缺少切片ID", code=400)

            # 2. 调用 Service
            success = await self.document_chunk_service.update_chunk_info(
                chunk_id=request.chunk_id, 
                content=request.content,
                meta_info=request.meta_info,
            )

            if success:
                msg_list = []
                if request.content: msg_list.append("内容")
                if request.meta_info: msg_list.append("元数据")
                return self._response(True, f"{' & '.join(msg_list)} 更新成功")
            else:
                return self._response(False, "切片不存在或更新失败", code=404)

        except Exception as e:
            self.logger.error(f"更新 Chunk 异常: {traceback.format_exc()}")
            return self._response(False, f"更新失败: {str(e)}", code=500)


    async def delete_chunk(self, request: ChunkDeleteRequest):
        """POST 删除单个切片"""
        try:
            # 1. 校验
            if not request.chunk_id:
                return self._response(False, "缺少切片ID", code=400)

            # 2. 调用 Service
            success = await self.document_chunk_service.delete_chunk(request.chunk_id)

            if success:
                # TODO: 实际上这里最好能异步触发删除向量库(Qdrant)中的对应数据
                return self._response(True, "删除成功")
            else:
                return self._response(False, "切片不存在或删除失败", code=404)

        except Exception as e:
            self.logger.error(f"删除 Chunk 异常: {traceback.format_exc()}")
            return self._response(False, f"删除失败: {str(e)}", code=500)
        
        
    async def delete_chunk_by_id(self, request: ChunkListRequest):
        """POST 删除单个切片"""
        try:
            # 1. 校验
            if not request.doc_id:
                return self._response(False, "缺少doc_id", code=400)

            # 2. 调用 Service
            success = await self.document_chunk_service.delete_chunks_by_doc_id(request.doc_id)

            if success:
                # TODO: 实际上这里最好能异步触发删除向量库(Qdrant)中的对应数据
                return self._response(True, "删除成功")
            else:
                return self._response(False, f"{request.doc_id} chunks不存在或删除失败", code=404)

        except Exception as e:
            self.logger.error(f"{request.doc_id} chunks 异常: {traceback.format_exc()}")
            return self._response(False, f"删除失败: {str(e)}", code=500)