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

# 导入各个底层业务
from api.service.minio_service import MinioService
from api.service.pdf2md_service import Pdf2MdService
from api.service.pdf_document_service import PdfDocumentService
from api.service.pipeline_task_service import PipelineTaskService
from api.service.document_chunk_service import DocumentChunkService
from api.service.chunk_service import ChunkService
from api.service.instruction_gen_service import InstructionGenService
from api.service.instruction_datum_service import InstructionDatumService
from api.service.llm_config_service import LLMConfigService
from api.service.embedding_service import EmbeddingService
from api.service.search_service import SearchService

# 导入各个服务类
from api.server.base.minio_server import MinioServer
from api.server.base.pdf2md_server import Pdf2MdServer
from agent.config.sql_config import SqlConfig
from api.server.base.pdf_document_server import PdfDocumentServer
from api.server.base.pipeline_task_server import PipelineTaskServer
from api.server.base.document_chunk_server import DocumentChunkServer
from api.server.base.chunk_server import ChunkServer
from api.server.base.instruction_gen_server import InstructionGenServer
from api.server.base.instruction_datum_server import InstructionDatumServer
from api.server.base.llm_config_server import LLMConfigServer
from api.server.base.embedding_server import EmbeddingServer

from tool.h1_context_assembler import H1ContextAssembler
from tool.instruction_llm_generator import InstructionLLMGenerator

ROOT_DIRECTORY = Path(__file__).parent.parent.parent
SQL_CONFIG_PATH = str(ROOT_DIRECTORY / "config" / "yaml" / "postgresql.yaml")
ENV_PATH = str(ROOT_DIRECTORY / ".env")
REDIS_CONFIG_PATH = str(ROOT_DIRECTORY / "config" / "yaml" / "redis_config.yaml")
MINIO_CONFIG_PATH = str(ROOT_DIRECTORY / "config" / "yaml" / "minio_config.yaml")
environment = dotenv_values(ENV_PATH)
MINERU_API_URL = environment.get("MINERU_API_URL", "http://localhost:8000/file_parse")

class AeroSenseMainServer:
    """主服务器类，统一管理所有服务"""
    
    def __init__(self, sql_config_path: str = SQL_CONFIG_PATH):
        self.sql_config_path = sql_config_path
        self.app = FastAPI(title="pdf2train server", version="1.0.0")
        self.logger = logging.getLogger(self.__class__.__name__)
        
        minio_config = SqlConfig.from_file(MINIO_CONFIG_PATH)
        
        # 初始化各个底层业务
        self.task_service = PipelineTaskService(sql_config_path=SQL_CONFIG_PATH)
        self.llm_config_service = LLMConfigService(sql_config_path=self.sql_config_path)
        self.instruction_datum_service = InstructionDatumService(
            sql_config_path=self.sql_config_path,
            pipeline_task_service=self.task_service
        )
        self.minio_service = MinioService(
            endpoint=minio_config.host,
            access_key=minio_config.username,
            secret_key=minio_config.password,
            secure=False,
            source_bucket_name="pdf-raw",
            target_bucket_name="pdf-processed",
            default_bucket="default",
            public_bucket="public-assets",
            buckets_to_create=[]
        )
        self.document_chunk_service = DocumentChunkService(
            sql_config_path=SQL_CONFIG_PATH,
            pipeline_task_service=self.task_service
        )
        self.pdf_document_service = PdfDocumentService(
            sql_config_path=SQL_CONFIG_PATH, 
            minio_service=self.minio_service,
            document_chunk_service=self.document_chunk_service,
            task_service=self.task_service,
            instruction_datum_service=self.instruction_datum_service,
            llm_config_service=self.llm_config_service
        )
        self.pdf2md_service = Pdf2MdService(
            minio_service=self.minio_service,
            pdf_document_service=self.pdf_document_service,
            work_dir="/tmp/pdf2md_worker",
            task_service=self.task_service,
            llm_config_service=self.llm_config_service,
            mineru_api_url=MINERU_API_URL
        )
        self.chunk_service = ChunkService(
            pdf_document_service=self.pdf_document_service,
            document_chunk_service=self.document_chunk_service,
            pipeline_task_service=self.task_service,
            minio_service=self.minio_service
        )
        self.instruction_llm_generator = InstructionLLMGenerator(
            llm_config_service=self.llm_config_service
        )
        self.instruction_gen_service = InstructionGenService(
            assembler=H1ContextAssembler(),
            document_chunk_service=self.document_chunk_service,
            instruction_llm_generator=self.instruction_llm_generator,
            llm_config_service=self.llm_config_service,
            instruction_datum_service=self.instruction_datum_service,
            pipeline_task_service=self.task_service
        )
        self.embedding_service = EmbeddingService(
            sql_config_path=self.sql_config_path,
            document_chunk_service=self.document_chunk_service,
            pipeline_task_service=self.task_service,
        )
        self.search_service = SearchService()
        
        
        # 初始化各个服务
        self.minio_storage_server = MinioServer(service=self.minio_service, pdf_document_service=self.pdf_document_service)
        self.pdf2md_server = Pdf2MdServer(self.pdf2md_service)
        self.pdf_document_server = PdfDocumentServer(self.pdf_document_service, self.minio_service)
        self.task_server = PipelineTaskServer(self.task_service)
        self.document_chunk_server = DocumentChunkServer(document_chunk_service=self.document_chunk_service)
        self.chunk_server = ChunkServer(chunk_service=self.chunk_service)
        self.instruction_gen_server = InstructionGenServer(instruction_gen_service=self.instruction_gen_service)
        self.instruction_datum_server = InstructionDatumServer(instruction_datum_service=self.instruction_datum_service)
        self.llm_config_server = LLMConfigServer(llm_config_service=self.llm_config_service)
        self.embedding_server = EmbeddingServer(embedding_service=self.embedding_service, search_service=self.search_service)
        
        # 设置应用
        self._setup_middleware()
        self._setup_base_routes()
        self._register_all_services()
    
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
        self.minio_storage_server.register_routes(self.app)
        self.pdf2md_server.register_routes(self.app)
        self.pdf_document_server.register_routes(self.app)
        self.task_server.register_routes(self.app)
        self.document_chunk_server.register_routes(self.app)
        self.chunk_server.register_routes(self.app)
        self.instruction_gen_server.register_routes(self.app)
        self.instruction_datum_server.register_routes(self.app)
        self.llm_config_server.register_routes(self.app)
        self.embedding_server.register_routes(self.app)

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

server = AeroSenseMainServer()
app = server.app


if __name__ == "__main__":
    args = parse_arguments()
    
    server.run(
        host="0.0.0.0",
        port=args.port,
        ssl_certfile=None,
        ssl_keyfile=None
    )