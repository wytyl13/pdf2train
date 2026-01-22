#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/19 15:48
@Author  : weiyutao
@File    : dependencies.py
"""

from functools import lru_cache
from fastapi import Depends
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv, dotenv_values
# 导入配置和 Service
from pdf2train.core.config import core_config
from pdf2train.core.configs.sql_config import SqlConfig
from pdf2train.core.service.minio_service import MinioService
from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.core.service.llm_config_service import LLMConfigService
from pdf2train.core.service.knowledge_base_service import KnowledgeBaseService
from pdf2train.core.service.qdrant_service import QdrantService
from pdf2train.core.service.minio_service import MinioService
from pdf2train.core.service.pipeline_task_service import PipelineTaskService
from pdf2train.core.service.pdf2md_service import Pdf2MdService

# 导入 Manager
from pdf2train.core.manager.pdf_document_manager import PdfDocumentManager
from pdf2train.core.manager.storage_manager import StorageManager
from pdf2train.core.manager.llm_config_manager import LLMConfigManager
from pdf2train.core.manager.knowledge_base_manager import KnowledgeBaseManager
from pdf2train.core.manager.pipeline_task_manager import PipelineTaskManager
from pdf2train.core.manager.pdf2md_manager import Pdf2MdManager

ROOT_DIRECTORY = Path(__file__).parent.parent.parent.parent
ENV_PATH = str(ROOT_DIRECTORY / ".env")
environment = dotenv_values(ENV_PATH)
MINERU_API_URL = environment.get("MINERU_API_URL", "http://localhost:8000/file_parse")


# 默认返回 None，代表生产环境 (Service 内部会处理 None 为生产配置)
# 如果是测试环境需要传递测试环境的config，需要重写该函数
def get_sql_config() -> Optional[SqlConfig]:
    return None

# --- Service 工厂 ---
# @lru_cache()
def get_minio_service():
    conf = core_config.minio_config
    return MinioService(
        endpoint=conf.host,
        access_key=conf.username,
        secret_key=conf.password,
        secure=False
    )

# @lru_cache()
def get_pdf_service(
    sql_config: Optional[SqlConfig] = Depends(get_sql_config)
):
    return PdfDocumentService(sql_config)

# @lru_cache()
def get_llm_config_service(
    sql_config: Optional[SqlConfig] = Depends(get_sql_config)
):
    return LLMConfigService(sql_config)

def get_knowledge_base_service(
    sql_config: Optional[SqlConfig] = Depends(get_sql_config)
):
    return KnowledgeBaseService(sql_config)

def get_qdrant_service():
    return QdrantService()

def get_pipeline_task_service(
    sql_config: Optional[SqlConfig] = Depends(get_sql_config)
):
    return PipelineTaskService(sql_config)

def get_pdf2md_service(
    minio_service: Optional[MinioService] = Depends(get_minio_service),
    llm_config_service: Optional[LLMConfigService] = Depends(get_llm_config_service)
    
):
    return Pdf2MdService(
        minio_service=minio_service,
        mineru_api_url=MINERU_API_URL,
        llm_config_service=llm_config_service
    )
    

# --- Manager 工厂 (Router 直接调用这些) ---
def get_storage_manager(
    minio_service: MinioService = Depends(get_minio_service)
) -> StorageManager:
    return StorageManager(minio_service)

def get_pdf_manager(
    pdf_service: PdfDocumentService = Depends(get_pdf_service),
    minio_service: MinioService = Depends(get_minio_service),
    llm_config_service: LLMConfigService = Depends(get_llm_config_service),
    kb_service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> PdfDocumentManager:
    return PdfDocumentManager(pdf_service, minio_service, llm_config_service, kb_service)

def get_llm_config_manager(
    llm_config_service: LLMConfigService = Depends(get_llm_config_service),
    pdf_service: PdfDocumentService = Depends(get_pdf_service)
) -> LLMConfigManager:
    return LLMConfigManager(llm_config_service, pdf_service)

def get_knowledge_base_manager(
    kb_service: KnowledgeBaseService = Depends(get_knowledge_base_service),
    pdf_service: PdfDocumentService = Depends(get_pdf_service),
    qdrant_service: QdrantService = Depends(get_qdrant_service),
    llm_config_service: LLMConfigService = Depends(get_llm_config_service)
) -> LLMConfigManager:
    return KnowledgeBaseManager(kb_service, pdf_service, qdrant_service, llm_config_service)

def get_pipeline_task_manager(
    pipeline_task_service: LLMConfigService = Depends(get_pipeline_task_service),
    pdf_document_service: LLMConfigService = Depends(get_pdf_service)
) -> PipelineTaskManager:
    return PipelineTaskManager(pipeline_task_service, pdf_document_service)

def get_pdf2md_manager(
    pdf2md_service: Optional[Pdf2MdService] = Depends(get_pdf2md_service),
    pdf_document_service: Optional[PdfDocumentService] = Depends(get_pdf_service),
    pipeline_task_service: Optional[PipelineTaskService] = Depends(get_pipeline_task_service),
    llm_config_service: Optional[LLMConfigService] = Depends(get_llm_config_service)
):
    return Pdf2MdManager(
        pdf2md_service=pdf2md_service,
        pdf_document_service=pdf_document_service,
        pipeline_task_service=pipeline_task_service,
        llm_config_service=llm_config_service
    )

