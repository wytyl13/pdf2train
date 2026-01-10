#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/08 15:49
@Author  : weiyutao
@File    : embedding_server.py
"""

from pydantic import BaseModel, Field
from fastapi import FastAPI, BackgroundTasks, APIRouter
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from datetime import datetime
import logging
import traceback
from typing import Any

from api.service.embedding_service import EmbeddingService
from api.service.search_service import SearchService
from api.service.llm_config_service import LLMConfigService
from api.table.base.llm_enum import ModelType
from api.schema.qdrant_schema import MetadataUpdateRequest


class EmbeddingRunRequest(BaseModel):
    """触发向量化任务的请求参数"""
    doc_id: int = Field(..., description="文档ID")
    

    

class SearchRequest(BaseModel):
    query: str = Field(..., description="检索关键词")
    top_k: int = Field(default=5, description="召回数量")
    return_raw: bool = Field(default=False, description="是否返回原始结果")

class EmbeddingServer:
    """
    向量化服务接口
    负责接收 Embedding 请求 -> 异步调用 EmbeddingService -> 返回提交状态
    """

    def __init__(
        self, 
        embedding_service: EmbeddingService, 
        search_service: SearchService,
        llm_config_service: LLMConfigService
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.embedding_service = embedding_service
        self.search_service = search_service
        self.llm_config_service = llm_config_service
    def register_routes(self, app: FastAPI):
        """注册路由"""
        # 定义路由
        router = APIRouter(tags=["Embedding Server"])
        
        # 触发向量化
        router.post("/api/embedding/run")(self.run_embedding_task)
        
        router.post("/api/vector/search")(self.search_proxy)

        router.post("/api/vector/update_metadata")(self.update_metadata)
        
        # 将 router 挂载到 app
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
    async def search_proxy(self, request: SearchRequest):
        """
        POST 封装检索请求
        接收客户端请求 -> 调用 SearchService -> 返回统一响应格式
        """
        try:
            self.logger.info(f"🔎 收到检索代理请求: {request.query}")
            
            # 调用 service 发起内部 http 请求
            remote_response = await self.search_service.execute_search(
                query=request.query,
                top_k=request.top_k,
                return_raw=request.return_raw
            )
            
            # 如果远程返回成功，直接解包其 data
            if remote_response.get("success"):
                return self._response(
                    True, 
                    message="检索完成", 
                    data=remote_response.get("data")
                )
            else:
                return self._response(
                    False, 
                    message=remote_response.get("message", "远程检索失败"), 
                    code=500
                )

        except Exception as e:
            self.logger.error(f"检索代理异常: {traceback.format_exc()}")
            return self._response(False, f"检索失败: {str(e)}", code=500)


    async def run_embedding_task(self, request: EmbeddingRunRequest, background_tasks: BackgroundTasks):
        """
        POST 触发文档向量化
        """
        try:
            doc_id = request.doc_id
            self.logger.info(f"收到向量化请求: Doc {doc_id}")
            print(f"收到向量化请求: request {request}")
            print(f"收到向量化请求: Doc {doc_id}")

            # get llm config by doc_id
            llm_config = await self.llm_config_service.get_config_by_doc_id(doc_id, field_llm_name="embedding_llm_config")
            
            llm_config = await self.llm_config_service.get_active_config(model_type=ModelType.EMBEDDING.value) if not llm_config else llm_config
            print(f"llm_config: {llm_config}")
            model_name = llm_config.get("model_name")
            name = llm_config.get("name")
            base_url = llm_config.get("base_url")
            api_key = llm_config.get("api_key")
            print(f"model_name: {model_name}, base_url: {base_url}, api_key: {api_key}")
            # 添加到后台任务队列
            background_tasks.add_task(self._safe_run_embedding, doc_id, model_name, base_url, api_key) # 传递name而不是model_name
            return self._response(True, "向量化任务已提交后台处理")

        except Exception as e:
            self.logger.error(f"提交向量化任务失败: {traceback.format_exc()}")
            return self._response(False, f"提交失败: {str(e)}", code=500)


    async def update_metadata(
        self, 
        metadata_update_request: MetadataUpdateRequest
    ) -> int:
        """
        更新向量的元数据
        """
        try:
            self.logger.info(f"收到元数据更新请求: {metadata_update_request}")
            result =  await self.embedding_service.update_metadata(metadata_update_request)
            return self._response(True, "更新成功")
        except Exception as e:
            self.logger.error(f"元数据更新失败: {traceback.format_exc()}")
            return self._response(False, f"更新失败: {str(e)}", code=500)
        
        
    async def _safe_run_embedding(
        self, 
        doc_id: int,
        model_name: str,
        base_url: str,
        api_key: str
    ):
        """
        [后台任务包装] 安全执行业务逻辑，防止异常中断服务
        """
        try:
            await self.embedding_service.run_embedding_for_doc(doc_id, model_name, base_url, api_key)
        except Exception as e:
            self.logger.critical(f"后台向量化任务崩溃 Doc {doc_id}: {str(e)}")