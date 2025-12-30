#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/27 16:28
@Author  : weiyutao
@File    : instruction_gen_server.py
"""

from pydantic import BaseModel, Field
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from datetime import datetime
import logging
import traceback
from typing import Any
import threading

from api.service.instruction_gen_service import InstructionGenService


class InstructionGenServerRequest(BaseModel):
    """触发切分任务的请求"""
    doc_id: int = Field(..., description="文档ID")
    


class InstructionGenServer:
    """
    切分流程控制接口
    负责触发、监控切分任务 (Extract -> Chunking -> Load)
    """

    def __init__(
        self, 
        instruction_gen_service: InstructionGenService,
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.instruction_gen_service = instruction_gen_service
        

    def register_routes(self, app: FastAPI):
        """注册路由"""
        # 手动触发切分任务
        app.post("/api/instruction/run")(self.run)

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

    async def run(self, request: InstructionGenServerRequest, background_tasks: BackgroundTasks):
        """
        POST 触发文档切分任务
        使用 BackgroundTasks 异步执行，接口立即返回
        """
        try:
            doc_id = request.doc_id
            def run_sync_wrapper():
                import asyncio
                asyncio.run(self.instruction_gen_service.run(doc_id=doc_id))
            
            t = threading.Thread(target=run_sync_wrapper, daemon=True)
            t.start()
            
            return self._response(
                True, 
                message=f"文档 {doc_id} 的指令生成任务已在后台启动",
                data={"doc_id": doc_id, "status": "submitted"}
            )
        except Exception as e:
            self.logger.error(f"任务提交失败: {traceback.format_exc()}")
            return self._response(False, f"任务提交失败: {str(e)}", code=500)