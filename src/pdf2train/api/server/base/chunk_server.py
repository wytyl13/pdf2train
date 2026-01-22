#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/24 19:25
@Author  : weiyutao
@File    : chunk_server.py
"""

from pydantic import BaseModel, Field
from fastapi import FastAPI, BackgroundTasks, APIRouter
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from datetime import datetime
import logging
import traceback
from typing import Any

from pdf2train.api.service.base.chunk_service import ChunkService



class ChunkRunRequest(BaseModel):
    """触发切分任务的请求"""
    doc_id: int = Field(..., description="文档ID")
    


class ChunkServer:
    """
    切分流程控制接口
    负责触发、监控切分任务 (Extract -> Chunking -> Load)
    """

    def __init__(self, chunk_service: ChunkService):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.chunk_service = chunk_service

    def register_routes(self, app: FastAPI):
        """注册路由"""
        router = APIRouter(tags=["Chunk Server"])
        # 手动触发切分任务
        router.post("/api/chunk/run")(self.run_chunk_task)
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

    async def run_chunk_task(self, request: ChunkRunRequest, background_tasks: BackgroundTasks):
        """
        POST 触发文档切分任务
        使用 BackgroundTasks 异步执行，接口立即返回
        """
        try:
            doc_id = request.doc_id
            
            # 1. (可选) 这里可以先简单检查一下文档是否存在，或者由 Service 内部检查
            
            self.logger.info(f"收到切分请求: Doc {doc_id}")

            # 2. 将耗时任务添加到后台队列
            # 注意：这里我们不等待它执行完，而是直接告诉前端“任务已提交”
            background_tasks.add_task(self._safe_run_chunking, doc_id)

            return self._response(True, "切分任务已提交后台处理")

        except Exception as e:
            self.logger.error(f"提交切分任务失败: {traceback.format_exc()}")
            return self._response(False, f"提交失败: {str(e)}", code=500)


    async def _safe_run_chunking(self, doc_id: int):
        """
        [内部包装] 用于在后台任务中捕获异常，防止 crash
        """
        try:
            await self.chunk_service.run_document_chunking(doc_id)
        except Exception as e:
            self.logger.critical(f"后台切分任务崩溃 Doc {doc_id}: {str(e)}")