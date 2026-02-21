#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/15 17:40
@Author  : weiyutao
@File    : main_server.py
"""


import sys
import uvicorn
import logging
import argparse
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from pdf2train.api.routers import llm_config_router
from pdf2train.api.routers import knowledge_base_router
from pdf2train.api.routers import pipeline_task_router
from pdf2train.api.routers import pdf_document_router
from pdf2train.api.routers import storage_router
from pdf2train.api.routers import pdf2md_router
from pdf2train.api.routers import document_chunk_router
from pdf2train.api.routers import instruction_datum_router
from pdf2train.api.routers import chunk_router
from pdf2train.api.routers import instruction_gen_router
from pdf2train.api.routers import qdrant_router
from pdf2train.api.routers import retrieval_router

from pdf2train.core.service.pipeline_task_service import PipelineTaskService
from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.core.manager.pipeline_task_manager import PipelineTaskManager


logger = logging.getLogger("MainServer")

def get_pipeline_task_manager():
    return PipelineTaskManager(
        service=PipelineTaskService(), 
        pdf_document_service=PdfDocumentService()
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True
)


async def _reset_stuck_tasks(stage: str):
    """
    调用 task_service 执行实际的 SQL 更新
    """
    try:
        pipeline_task_manager: PipelineTaskManager = get_pipeline_task_manager()
        count = await pipeline_task_manager.reset_processing_tasks_to_failed()
        logger.info(f"[{stage}] 任务状态重置逻辑执行完毕。")
    except Exception as e:
        logger.error(f"[{stage}] 重置任务状态失败: {str(e)}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 生命周期管理
    1. 启动前：清理上次意外中断的任务
    2. 运行中：yield
    3. 关闭时：标记当前未完成的任务为中断
    """
    # 1. 启动时执行
    logger.info("🚀 服务启动，正在检查遗留的 Processing 任务...")
    await _reset_stuck_tasks(stage="startup")
    # 2. 运行中
    yield 
    # 3. 关闭时执行
    logger.info("🛑 服务关闭，正在标记未完成任务为中断...")
    await _reset_stuck_tasks(stage="shutdown")

app = FastAPI(
    title="PDF2Train API Server",
    description="基于 LLM 的文档解析与训练平台",
    version="1.0.0",
    lifespan=lifespan,  # 挂载生命周期
    docs_url="/docs",
    redoc_url="/redoc"
)

# A. CORS 跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_credentials=True,
    allow_headers=["*"],
)

# B. 自定义请求日志 (保留你的逻辑)
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"[收到请求] {request.method} {request.url}")
    try:
        response = await call_next(request)
        logger.info(f"[响应状态] {response.status_code}")
        return response
    except Exception as e:
        logger.error(f"[请求处理异常] {str(e)}")
        raise e

# 注册路由
app.include_router(llm_config_router.router)
app.include_router(knowledge_base_router.router)
app.include_router(pdf_document_router.router)
app.include_router(pipeline_task_router.router)
app.include_router(storage_router.router)
app.include_router(pipeline_task_router.router_dashboard)
app.include_router(pdf2md_router.router)
app.include_router(document_chunk_router.router)
app.include_router(instruction_datum_router.router)
app.include_router(chunk_router.router)
app.include_router(instruction_gen_router.router)
app.include_router(qdrant_router.router)
app.include_router(retrieval_router.router)


@app.get("/")
async def root():
    return {"message": "pdf2train server", "version": "1.0.0"}

@app.get("/api/health")
async def health_check():
    """保留你详细的健康检查接口"""
    return {
        "status": "ok", 
        "message": "API服务运行正常", 
        "timestamp": datetime.now().isoformat(),
        "active_modules": [route.path for route in app.router.routes]
    }

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局兜底异常捕获"""
    logger.error(f"🔥 Global Exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": f"Internal Server Error: {str(exc)}",
            "data": None
        }
    )

def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="PDF2Train API Server")
    parser.add_argument("--port", "-p", type=int, default=8890, help="Server Port")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Server Host")
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_arguments()
    
    # 打印启动信息
    print(f"🌍 Server running at http://{args.host}:{args.port}")
    print(f"📚 Swagger UI at http://{args.host}:{args.port}/docs")

    # 启动 Uvicorn
    uvicorn.run(
        "pdf2train.api.server.main_server:app", 
        host=args.host, 
        port=args.port,
        reload=True, # 开发模式开启
        log_level="info"
    )