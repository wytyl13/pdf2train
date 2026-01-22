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

from pdf2train.api.service.base.embedding_service import EmbeddingService
from pdf2train.api.service.base.search_service import SearchService
from pdf2train.api.service.base.llm_config_service import LLMConfigService
from pdf2train.core.table.llm_enum import ModelType
from pdf2train.api.schema.qdrant_schema import MetadataUpdateRequest, IngestRequest, EmbeddingConfigOverride, EmbeddingRunRequest



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
        
        # 触发向量化，按照单个文件大批次修改，batch，常用
        router.post("/api/embedding/run")(self.run_embedding_task)
        
        router.post("/api/vector/search")(self.search_proxy)

        router.post("/api/vector/update_metadata")(self.update_metadata)

        # 单个或小批量修改，非batch，一般不用
        router.post("/api/vector/ingest")(self.ingest)
        
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
    async def ingest(self, request: IngestRequest):
        """
        POST 向量数据写入/入库接口
        场景：接收处理好的文本切片(chunks)和配置，直接写入向量数据库
        """
        try:
            # 1. 简单参数校验与日志
            chunk_count = len(request.chunks)
            if chunk_count == 0:
                return self._response(False, "写入失败: Chunks 列表为空", code=400)

            model_name = request.embed_config.model_name if request.embed_config else "unknown"
            self.logger.info(f"📥 收到向量Ingest请求: Count={chunk_count}, Model={model_name}")

            # 2. 调用 Service 层执行逻辑
            result = await self.embedding_service.call_vector_api(request)

            # 3. 返回统一响应
            return self._response(
                success=True, 
                message=f"成功处理 {chunk_count} 条数据的写入请求", 
                data=result
            )

        except Exception as e:
            self.logger.error(f"❌ 向量写入异常: {traceback.format_exc()}")
            return self._response(False, f"写入失败: {str(e)}", code=500)
    
    
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

            # get embedding_config_override by doc_id
            embedding_config_override: EmbeddingConfigOverride = await self.llm_config_service.get_embedding_config_override(doc_id=doc_id)
            
            # 添加到后台任务队列
            background_tasks.add_task(
                self._safe_run_embedding, 
                doc_id, 
                embed_config_override = embedding_config_override
            )
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
        embed_config_override: EmbeddingConfigOverride 
    ):
        """
        [后台任务包装] 安全执行业务逻辑，防止异常中断服务
        """
        try:
            await self.embedding_service.run_embedding_for_doc(doc_id, embed_config_override=embed_config_override)
        except Exception as e:
            self.logger.critical(f"后台向量化任务崩溃 Doc {doc_id}: {str(e)}")