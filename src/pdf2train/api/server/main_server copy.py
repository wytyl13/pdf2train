#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/15 17:40
@Author  : weiyutao
@File    : main_server.py
"""


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import logging
from datetime import datetime
from pathlib import Path
import argparse
from dotenv import load_dotenv, dotenv_values
import os
from openai import OpenAI
from contextlib import asynccontextmanager
import sys

# 全局配置
from pdf2train.core.config import core_config

# 各个底层业务
from pdf2train.api.service.base.minio_service import MinioService
from pdf2train.api.service.base.pdf2md_service import Pdf2MdService
from pdf2train.api.service.base.pdf_document_service import PdfDocumentService
from pdf2train.api.service.base.pipeline_task_service import PipelineTaskService
from pdf2train.api.service.base.document_chunk_service import DocumentChunkService
from pdf2train.api.service.base.chunk_service import ChunkService
from pdf2train.api.service.base.instruction_gen_service import InstructionGenService
from pdf2train.api.service.base.instruction_datum_service import InstructionDatumService
from pdf2train.api.service.base.llm_config_service import LLMConfigService
from pdf2train.api.service.base.embedding_service import EmbeddingService
from pdf2train.api.service.base.search_service import SearchService
from pdf2train.api.service.base.knowledge_base_service import KnowledgeBaseService
from pdf2train.api.service.base.update_doc_to_kb_service import UpdateDocToKbService

# 导入各个服务类
from pdf2train.api.server.base.minio_server import MinioServer
from pdf2train.api.server.base.pdf2md_server import Pdf2MdServer
from pdf2train.api.server.base.pdf_document_server import PdfDocumentServer
from pdf2train.api.server.base.pipeline_task_server import PipelineTaskServer
from pdf2train.api.server.base.document_chunk_server import DocumentChunkServer
from pdf2train.api.server.base.chunk_server import ChunkServer
from pdf2train.api.server.base.instruction_gen_server import InstructionGenServer
from pdf2train.api.server.base.instruction_datum_server import InstructionDatumServer
from pdf2train.api.server.base.llm_config_server import LLMConfigServer
from pdf2train.api.server.base.embedding_server import EmbeddingServer
from pdf2train.api.server.base.knowledge_base_server import KnowledgeBaseServer

from pdf2train.tool.h1_context_assembler import H1ContextAssembler
from pdf2train.tool.instruction_llm_generator import InstructionLLMGenerator

ROOT_DIRECTORY = Path(__file__).parent.parent.parent.parent.parent
ENV_PATH = str(ROOT_DIRECTORY / ".env")
environment = dotenv_values(ENV_PATH)
MINERU_API_URL = environment.get("MINERU_API_URL", "http://localhost:8000/file_parse")

logging.basicConfig(
    level=logging.INFO,  # 设置显示级别：DEBUG/INFO/WARNING/ERROR
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", # 格式：时间-名字-级别-内容
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)


class PDF2TrainMainServer:
    """主服务器类，统一管理所有服务"""
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        minio_config = core_config.minio_config
        
        # 1. 初始化各个底层业务
        # self.task_service = PipelineTaskService()
        # self.llm_config_service = LLMConfigService()
        # self.update_doc_to_kb_service = UpdateDocToKbService(llm_config_service=self.llm_config_service)
        # self.instruction_datum_service = InstructionDatumService(
        #     pipeline_task_service=self.task_service, 
        #     update_doc_to_kb_service=self.update_doc_to_kb_service,
        #     llm_config_service=self.llm_config_service
        # )
        # self.minio_service = MinioService(
        #     endpoint=minio_config.host,
        #     access_key=minio_config.username,
        #     secret_key=minio_config.password,
        #     secure=False,
        #     source_bucket_name="pdf-raw",
        #     target_bucket_name="pdf-processed",
        #     default_bucket="default",
        #     public_bucket="public-assets",
        #     buckets_to_create=[]
        # )
        # self.document_chunk_service = DocumentChunkService(
        #     pipeline_task_service=self.task_service, 
        #     update_doc_to_kb_service=self.update_doc_to_kb_service,
        #     llm_config_service=self.llm_config_service
        # )
        # self.pdf_document_service = PdfDocumentService(
        #     minio_service=self.minio_service,
        #     instruction_datum_service=self.instruction_datum_service,
        #     llm_config_service=self.llm_config_service,
        #     update_doc_to_kb_service=self.update_doc_to_kb_service,
        #     document_chunk_service=self.document_chunk_service,
        #     task_service=self.task_service,
        # )
        # self.pdf2md_service = Pdf2MdService(
        #     minio_service=self.minio_service,
        #     pdf_document_service=self.pdf_document_service,
        #     task_service=self.task_service,
        #     mineru_api_url=MINERU_API_URL,
        #     work_dir="/tmp/pdf2md_worker",
        #     llm_config_service=self.llm_config_service,
        # )
        # self.chunk_service = ChunkService(
        #     pdf_document_service=self.pdf_document_service,
        #     document_chunk_service=self.document_chunk_service,
        #     pipeline_task_service=self.task_service,
        #     minio_service=self.minio_service
        # )
        # self.instruction_llm_generator = InstructionLLMGenerator(llm_config_service=self.llm_config_service)
        # self.instruction_gen_service = InstructionGenService(
        #     assembler=H1ContextAssembler(),
        #     document_chunk_service=self.document_chunk_service,
        #     instruction_llm_generator=self.instruction_llm_generator,
        #     llm_config_service=self.llm_config_service,
        #     instruction_datum_service=self.instruction_datum_service,
        #     pipeline_task_service=self.task_service
        # )
        # self.embedding_service = EmbeddingService(
        #     document_chunk_service=self.document_chunk_service,
        #     pipeline_task_service=self.task_service,
        #     pdf_document_service=self.pdf_document_service,
        #     update_doc_to_kb_service=self.update_doc_to_kb_service,
        #     instruction_datum_service=self.instruction_datum_service
        # )
        # self.search_service = SearchService()
        # self.knowledge_base_service = KnowledgeBaseService(
        #     embedding_service=self.embedding_service,
        #     llm_config_service=self.llm_config_service,
        #     update_doc_to_kb_service=self.update_doc_to_kb_service
        # )
        
        
        # # 2. 初始化各个服务
        # self.minio_storage_server = MinioServer(service=self.minio_service, pdf_document_service=self.pdf_document_service)
        # self.pdf2md_server = Pdf2MdServer(self.pdf2md_service)
        # self.pdf_document_server = PdfDocumentServer(self.pdf_document_service, self.minio_service)
        # self.task_server = PipelineTaskServer(self.task_service)
        # self.document_chunk_server = DocumentChunkServer(document_chunk_service=self.document_chunk_service)
        # self.chunk_server = ChunkServer(chunk_service=self.chunk_service)
        # self.instruction_gen_server = InstructionGenServer(instruction_gen_service=self.instruction_gen_service)
        # self.instruction_datum_server = InstructionDatumServer(instruction_datum_service=self.instruction_datum_service)
        # self.llm_config_server = LLMConfigServer(llm_config_service=self.llm_config_service)
        # self.embedding_server = EmbeddingServer(
        #     embedding_service=self.embedding_service, 
        #     search_service=self.search_service, 
        #     llm_config_service=self.llm_config_service
        # )
        # self.knowledge_base_server = KnowledgeBaseServer(kb_service=self.knowledge_base_service)
        
        # 3. 初始化app并传入lifespan
        self.app = FastAPI(
            title="pdf2train server", 
            version="1.0.0",
            # lifespan=self.lifespan  # 绑定生命周期函数
        )
        
        # 4 设置应用
        self._setup_middleware()
        self._setup_base_routes()
        self._register_all_services()
    
    # @asynccontextmanager
    # async def lifespan(self, app: FastAPI):
    #     """
    #     FastAPI 生命周期管理
    #     1. 启动前：清理上次意外中断的任务
    #     2. 运行中：yield
    #     3. 关闭时：标记当前未完成的任务为中断
    #     """
    #     # 1. 启动时执行
    #     self.logger.info("🚀 服务启动，正在检查遗留的 Processing 任务...")
    #     await self._reset_stuck_tasks(stage="startup")
    #     # 2. 运行中
    #     yield 
    #     # 3. 关闭时执行
    #     self.logger.info("🛑 服务关闭，正在标记未完成任务为中断...")
    #     await self._reset_stuck_tasks(stage="shutdown")
    
    
    async def _reset_stuck_tasks(self, stage: str):
        """
        调用 task_service 执行实际的 SQL 更新
        """
        try:
            count = await self.task_service.reset_processing_tasks_to_failed()
            self.logger.info(f"[{stage}] 任务状态重置逻辑执行完毕。")
        except Exception as e:
            self.logger.error(f"[{stage}] 重置任务状态失败: {str(e)}")
    
    
    def _setup_middleware(self):
        """设置中间件"""
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_credentials=True,
            allow_headers=["*"],
        )
        
        @self.app.middleware("http")
        async def log_requests(request, call_next):
            self.logger.info(f"[收到请求] {request.method} {request.url}")
            response = await call_next(request)
            self.logger.info(f"[响应状态] {response.status_code}")
            return response
    
    
    def _setup_base_routes(self):
        """设置基础路由"""
        @self.app.get("/")
        async def root():
            return {"message": "pdf2train server", "version": "1.0.0"}
        
        @self.app.get("/api/health")
        async def health_check():
            return {
                "status": "ok", 
                "message": "API服务运行正常", 
                "timestamp": datetime.now().isoformat(),
                "services": ["device", "community", "user", "sleep"]
            }
    
    
    def _register_all_services(self):
        """注册所有服务的路由"""
        # 注册用户服务路由
        # self.minio_storage_server.register_routes(self.app)
        # self.pdf2md_server.register_routes(self.app)
        # self.pdf_document_server.register_routes(self.app)
        # self.task_server.register_routes(self.app)
        # self.document_chunk_server.register_routes(self.app)
        # self.chunk_server.register_routes(self.app)
        # self.instruction_gen_server.register_routes(self.app)
        # self.instruction_datum_server.register_routes(self.app)
        # self.llm_config_server.register_routes(self.app)
        # self.embedding_server.register_routes(self.app)
        # self.knowledge_base_server.register_routes(self.app)
        pass


    def run(
        self, 
        host: str = "0.0.0.0", 
        port: int = 8890,
        ssl_certfile: str = None,
        ssl_keyfile: str = None
    ):
        """启动服务器"""
        if ssl_certfile and ssl_keyfile:
            print(f"🔒 使用SSL证书: {ssl_certfile}")
            print(f"🔑 使用SSL密钥: {ssl_keyfile}")
        
        # 构建uvicorn运行参数
        run_kwargs = {
            "app": "api.server.main_server:app",
            "host": host,
            "port": port,
            "log_level": "info",
            "reload": True,
        }
        
        # 如果提供了SSL证书，则添加SSL配置
        if ssl_certfile and ssl_keyfile:
            run_kwargs.update({
                "ssl_certfile": ssl_certfile,
                "ssl_keyfile": ssl_keyfile
            })
        
        uvicorn.run(**run_kwargs)


def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="AeroSense综合API服务器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python main.py                    # 使用默认端口 8890
  python main.py --port 8080       # 指定端口为 8080
  python main.py -p 9000           # 指定端口为 9000 (简写)
  python main.py --host 127.0.0.1  # 指定主机地址
        """
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8890,
        help="服务器端口号 (默认: 8890)"
    )
    
    return parser.parse_args()

server = PDF2TrainMainServer()
app = server.app


if __name__ == "__main__":
    args = parse_arguments()
    
    server.run(
        host="0.0.0.0",
        port=args.port,
        ssl_certfile=None,
        ssl_keyfile=None
    )