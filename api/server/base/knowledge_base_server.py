#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/10 11:58
@Author  : weiyutao
@File    : knowledge_base_server.py
"""

from fastapi import FastAPI, APIRouter, Depends
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from datetime import datetime
import logging
from pydantic import BaseModel, Field
from typing import Optional, Any, List
import traceback

from api.service.knowledge_base_service import KnowledgeBaseService
from api.schema.retrieval_schema import RetrievalSettings
from api.service.knowledge_base_service import KnowledgeBaseService, MetadataUpdateRequest

class KBCreateRequest(BaseModel):
    """创建知识库请求"""
    name: str = Field(..., description="知识库名称", max_length=100)
    description: Optional[str] = Field(None, description="描述")
    avatar_url: Optional[str] = None
    
    # 核心配置
    embedding_model: str = Field("bge-large-zh", description="向量模型名称")
    vector_store_collection_name: Optional[str] = Field(None, description="集合名称(可选)")
    
    # 检索策略配置 (直接复用 Schema，利用 Pydantic 自动校验)
    settings: Optional[RetrievalSettings] = Field(default_factory=RetrievalSettings)
    
    user_id: int = Field(..., description="创建用户ID") # 实际场景中可能从 Token 获取
    is_public: bool = False

class KBUpdateRequest(BaseModel):
    """更新知识库请求"""
    id: int = Field(..., description="知识库ID")
    name: Optional[str] = None
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    
    # 允许更新检索策略
    settings: Optional[RetrievalSettings] = None
    
    is_public: Optional[bool] = None
    # embedding_model 通常不允许更新

class KBDeleteRequest(BaseModel):
    id: int = Field(..., description="知识库ID")

class KBListRequest(BaseModel):
    page: int = 1
    page_size: int = 20
    user_id: Optional[int] = None
    keyword: Optional[str] = None

class KBDetailRequest(BaseModel):
    id: int = Field(..., description="知识库ID")


class KnowledgeBaseServer:
    """
    知识库管理接口
    """

    def __init__(self, kb_service: KnowledgeBaseService):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.service = kb_service

    def register_routes(self, app: FastAPI):
        router = APIRouter(tags=["Knowledge Base"])
        
        router.post("/api/knowledge_base/create")(self.create_kb)
        router.post("/api/knowledge_base/delete")(self.delete_kb)
        router.post("/api/knowledge_base/update")(self.update_kb)
        router.post("/api/knowledge_base/list")(self.get_list)
        router.post("/api/knowledge_base/detail")(self.get_detail)
        router.post("/api/knowledge_base/update_docs")(self.update_docs_to_kb)
        
        app.include_router(router)

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

    async def update_docs_to_kb(
        self, 
        metadata_update_request: MetadataUpdateRequest
    ) :
        """
        将一批文档关联到指定知识库
        """
        try:
            result = await self.service.update_docs_to_kb(metadata_update_request)
        except Exception as e:
            self.logger.error(f"将文档添加到知识库失败: {traceback.format_exc()}")
            return self._response(False, f"操作失败: {str(e)}", code=500)
        return self._response(True, "操作成功", result)
    

    async def create_kb(self, request: KBCreateRequest):
        try:
            # exclude_unset=True 防止没传的字段覆盖默认值
            data = request.dict()
            
            # 注意：request.retrieval_settings 已经是 Pydantic 对象了
            # Service 层会处理它的序列化
            
            new_id = await self.service.create_kb(data)
            return self._response(True, "创建成功", {"id": new_id})
        except Exception as e:
            self.logger.error(f"创建知识库失败: {traceback.format_exc()}")
            return self._response(False, f"创建失败: {str(e)}", code=500)

    async def update_kb(self, request: KBUpdateRequest):
        try:
            data = request.dict(exclude_unset=True)
            kb_id = data.pop("id")
            
            success = await self.service.update_kb(kb_id, data)
            if success:
                return self._response(True, "更新成功")
            else:
                return self._response(False, "知识库不存在或更新失败", code=404)
        except Exception as e:
            self.logger.error(f"更新知识库失败: {traceback.format_exc()}")
            return self._response(False, f"更新失败: {str(e)}", code=500)


    async def delete_kb(self, request: KBDeleteRequest):
        try:
            success = await self.service.delete_kb(request.id)
            if success:
                return self._response(True, "删除成功")
            else:
                return self._response(False, "知识库不存在", code=404)
        except Exception as e:
            self.logger.error(f"删除知识库失败: {traceback.format_exc()}")
            return self._response(False, f"删除失败: {str(e)}", code=500)


    async def get_list(self, request: KBListRequest):
        try:
            result = await self.service.get_kb_list(
                page=request.page,
                page_size=request.page_size,
                user_id=request.user_id,
                keyword=request.keyword
            )
            return self._response(True, "查询成功", result)
        except Exception as e:
            self.logger.error(f"查询列表失败: {traceback.format_exc()}")
            return self._response(False, f"查询失败: {str(e)}", code=500)

    async def get_detail(self, request: KBDetailRequest):
        try:
            result = await self.service.get_kb_detail(request.id)
            if result:
                return self._response(True, "查询成功", result)
            else:
                return self._response(False, "知识库不存在", code=404)
        except Exception as e:
            self.logger.error(f"查询详情失败: {traceback.format_exc()}")
            return self._response(False, f"查询失败: {str(e)}", code=500)