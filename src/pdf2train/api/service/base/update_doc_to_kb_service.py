#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/11 08:53
@Author  : weiyutao
@File    : update_doc_to_kb_service.py
"""
from datetime import datetime
import logging
from typing import Optional, List, Dict
from sqlalchemy import update
import httpx
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type


from pdf2train.core.provider.sql_provider import SqlProvider
from pdf2train.api.schema.qdrant_schema import (
    MetadataUpdateRequest, 
    UnbindKbId, 
    VectorDeleteRequest,
    IngestRequest
) 
from pdf2train.core.table.pdf_document import PdfDocument
from pdf2train.api.service.base.llm_config_service import LLMConfigService

WANGENG_VECTOR_URL = "http://wangeng:9040/api/vector/ingest"
WANGENG_VECTOR_UPDATE_METADATA_URL = "http://wangeng:9040/api/vector/update_metadata"  
WANGENG_VECTOR_DELETE_URL = "http://wangeng:9040/api/vector/delete"  

class UpdateDocToKbService:
    def __init__(
        self, 
        llm_config_service: LLMConfigService
    ):
        self.llm_config_service = llm_config_service
        self.logger = logging.getLogger(self.__class__.__name__)
        
    
    @retry(
        stop=stop_after_attempt(3), 
        wait=wait_fixed(2),
        retry=retry_if_exception_type(httpx.HTTPError) # 只重试网络错误
    )
    async def delete_vector(
        self, 
        vector_delete_request: VectorDeleteRequest
    ) -> int:
        """
        [独立抽取的 API 调用方法] 负责发送请求，包含重试逻辑
        """
        collection_name = vector_delete_request.collection_name
        filter_key = vector_delete_request.filter_key
        filter_value = vector_delete_request.filter_value
        filters = vector_delete_request.filters

        timeout_settings = httpx.Timeout(60.0, connect=10.0) # 设置合理的超时
        async with httpx.AsyncClient(timeout=timeout_settings) as client:
            response = await client.post(
                WANGENG_VECTOR_DELETE_URL,
                json={
                    "collection_name": collection_name,
                    "filter_key": filter_key,
                    "filter_value": filter_value,
                    "filters": filters
                },
            )
            response.raise_for_status() # 如果 4xx/5xx 直接抛出异常触发重试
            
            resp_data = response.json()
            is_success = resp_data.get("success") or (resp_data.get("status") == "success")
            if not is_success:
                raise Exception(f"API 业务错误: {resp_data}")
                
        return is_success
    
    
    @retry(
        stop=stop_after_attempt(3), 
        wait=wait_fixed(2),
        retry=retry_if_exception_type(httpx.HTTPError) # 只重试网络错误
    )
    async def call_vector_api(
        self, 
        ingest_request: IngestRequest
    ) -> int:
        """
        [独立抽取的 API 调用方法] 负责发送请求，包含重试逻辑
        供documents_chunk和instruction datum数据表单个chunk修改的时候同步向量库更新
        """
        
        payload_chunks: List[Dict] = ingest_request.chunks
        timeout_settings = httpx.Timeout(60.0, connect=10.0) # 设置合理的超时
        async with httpx.AsyncClient(timeout=timeout_settings) as client:
            response = await client.post(
                WANGENG_VECTOR_URL,
                json=ingest_request.model_dump(),
            )
            response.raise_for_status() # 如果 4xx/5xx 直接抛出异常触发重试
            
            resp_data = response.json()
            is_success = resp_data.get("success") or (resp_data.get("status") == "success")
            
            if not is_success:
                raise Exception(f"API 业务错误: {resp_data}")
                
        return len(payload_chunks)
    
    
    async def get_collection_name_by_doc_id(self, doc_id: int) -> Optional[str]:
        """
        [辅助方法] 根据文档 ID 获取对应的 Qdrant Collection Name
        逻辑：
        1. 查 PdfDocument 表，看该文档是否绑定了特定的 embed_model
        2. 如果有，解析其真实 model_name
        3. 如果没有 (None)，则获取系统当前默认的 Embedding 模型
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PdfDocument)
            
            # 1. 查询文档记录
            doc = await sql_provider.get_record_by_condition({"id": doc_id})
            if not doc:
                self.logger.warning(f"未找到 ID={doc_id} 的文档")
                return None

            # 2. 尝试从文档获取模型标识
            model_identifier = doc[0].get("embedding_llm_config", None)
            real_name = await self.llm_config_service.get_real_model_name(model_identifier)
            return real_name

        except Exception as e:
            self.logger.error(f"获取 Collection Name 失败 (doc_id={doc_id}): {e}")
            return None
        finally:
            if sql_provider: await sql_provider.close()
    
    
    @retry(
        stop=stop_after_attempt(3), 
        wait=wait_fixed(2),
        retry=retry_if_exception_type(httpx.HTTPError) # 只重试网络错误
    )
    async def update_metadata(
        self, 
        metadata_update_request: MetadataUpdateRequest
    ) -> int:
        """
        [独立抽取的 API 调用方法] 负责发送请求，包含重试逻辑
        """
        collection_name = metadata_update_request.collection_name
        filter_key = metadata_update_request.filter_key
        filter_value = metadata_update_request.filter_value
        payload = metadata_update_request.payload

        timeout_settings = httpx.Timeout(60.0, connect=10.0) # 设置合理的超时
        async with httpx.AsyncClient(timeout=timeout_settings) as client:
            response = await client.post(
                WANGENG_VECTOR_UPDATE_METADATA_URL,
                json={
                    "collection_name": collection_name,
                    "filter_key": filter_key,
                    "filter_value": filter_value,
                    "payload": payload
                },
            )
            response.raise_for_status() # 如果 4xx/5xx 直接抛出异常触发重试
            
            resp_data = response.json()
            is_success = resp_data.get("success") or (resp_data.get("status") == "success")
            if not is_success:
                raise Exception(f"API 业务错误: {resp_data}")
                
        return is_success
        
    
    async def unbind_kb_id(
        self, 
        unbind_request: UnbindKbId
    ):
        """
        [私有方法] 调用远程向量服务，将指定 kb_id 的向量归属设为 0
        使用现有的 update_metadata 接口
        """
        timeout_settings = httpx.Timeout(60.0, connect=10.0)
        collection_name = unbind_request.collection_name
        kb_id = unbind_request.kb_id
        
        payload_data = {
            "collection_name": collection_name,
            "filter_key": "kb_id",      # 筛选条件：字段名为 kb_id
            "filter_value": kb_id,      # 筛选值：当前知识库 ID
            "payload": {"kb_id": 0}     # 更新动作：设为 0 (公海)
        }

        async with httpx.AsyncClient(timeout=timeout_settings) as client:
            self.logger.info(f"正在请求远程向量解绑: {payload_data}")
            
            response = await client.post(
                WANGENG_VECTOR_UPDATE_METADATA_URL,
                json=payload_data,
            )
            
            # 检查 HTTP 状态码
            response.raise_for_status() 
            
            # 检查业务状态码
            resp_data = response.json()
            # 兼容 success 字段或 status 字段
            is_success = resp_data.get("success") is True
            
            if not is_success:
                error_msg = resp_data.get("message") or "未知错误"
                raise Exception(f"API 业务返回失败: {error_msg}")
            
            self.logger.info(f"远程向量解绑成功: {collection_name} -> kb_id={kb_id} 已释放")
    
    
    async def update_docs_to_kb(
        self, 
        metadata_update_request: MetadataUpdateRequest,
        update_sql: bool = True
    ) :
        """
        将一批文档关联到指定知识库
        """
        # x先更新collecttion_name
        collection_name = await self.llm_config_service.get_real_model_name(metadata_update_request.collection_name)
        
        # 1 更新pdf_document表的 kb_id 字段
        if "kb_id" not in metadata_update_request.payload:
            raise ValueError("元数据 payload 中必须包含 'kb_id' 字段")

        # 2. 再安全地获取值（此时值可能是 int，也可能是 None）
        new_kb_id = metadata_update_request.payload["kb_id"]
        
        # 获取文档 ID 列表
        doc_ids = metadata_update_request.filter_value
        if not isinstance(doc_ids, list):
            doc_ids = [doc_ids]
        
        # 确保 filter_key 是针对文档 ID 的 (防止误传其他条件导致 SQL 更新错误)
        # if metadata_update_request.filter_key != "doc_kb_id":
        #     raise ValueError("批量添加操作仅支持通过 'doc_kb_id' 进行筛选")
        qdrant_filter_key = "doc_kb_id" if metadata_update_request.filter_key == "doc_id" else metadata_update_request.filter_key
        sql_provider = None
        try:
            if update_sql:
                # 1. 更新 pdf_document 表的 kb_id 字段
                sql_provider = SqlProvider(model=PdfDocument)
                
                stmt = (
                    update(PdfDocument)
                    .where(PdfDocument.id.in_(doc_ids))
                    .values(
                        kb_id=new_kb_id,
                        update_time=datetime.now()
                    )
                )
                async with sql_provider.get_db_session() as session:
                    result = await session.execute(stmt)

            # 2 Update Payload: set kb_id = :kb_id where doc_id in :doc_ids
            metadata_ = metadata_update_request.copy()
            metadata_.filter_key = qdrant_filter_key  # 👈 修改为 Qdrant 中的字段名
            metadata_.collection_name = collection_name
            metadata_.payload = metadata_update_request.payload.copy()
            if new_kb_id is None:
                metadata_.payload["kb_id"] = 0
            await self.update_metadata(metadata_)

            return True

        except Exception as e:
            print(f"添加文档到知识库失败: {e}")
            raise e
        finally:
            if sql_provider:
                await sql_provider.close()