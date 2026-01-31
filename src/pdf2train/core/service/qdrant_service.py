#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/20 22:19
@Author  : weiyutao
@File    : qdrant_service.py
"""
import httpx
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
import logging
from typing import List, Dict

from pdf2train.core.schema.qdrant_dto import (
    QdrantPayloadUpdateDTO, 
    IngestRequest, 
    VectorDeleteRequest
)
from pdf2train.core.schema.qdrant_dto import VectorDeleteRequest
from pdf2train.core.service.llm_config_service import LLMConfigService


WANGENG_VECTOR_URL = "http://wangeng:9040/api/vector/ingest"
WANGENG_VECTOR_DELETE_URL = "http://wangeng:9040/api/vector/delete" 
WANGENG_QDRANT_UPDATE_METADATA_URL = "http://wangeng:9040/api/vector/update_metadata"  


class QdrantService:
    def __init__(
        self,
        llm_config_service: LLMConfigService
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.llm_config_service = llm_config_service
    
    @retry(
        stop=stop_after_attempt(3), 
        wait=wait_fixed(2),
        retry=retry_if_exception_type(httpx.HTTPError) # 只重试网络错误
    )
    async def ingest_api(
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
            response.raise_for_status()
            
            resp_data = response.json()
            is_success = resp_data.get("success") or (resp_data.get("status") == "success")
            if not is_success:
                raise Exception(f"API 业务错误: {resp_data}")
        return len(payload_chunks)
    
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
        条件删除向量数据
        """
        try:
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
        except Exception as e:
            raise ValueError(f"条件删除向量数据失败！{str(e)}") from e
    
    @retry(
        stop=stop_after_attempt(3), 
        wait=wait_fixed(2),
        retry=retry_if_exception_type(httpx.HTTPError) # 只重试网络错误
    )
    async def update_payload(
        self, 
        dto: QdrantPayloadUpdateDTO
    ) -> int:
        """
        [独立抽取的 API 调用方法] 负责发送请求，包含重试逻辑
        """
        
        timeout_settings = httpx.Timeout(60.0, connect=10.0) # 设置合理的超时
        async with httpx.AsyncClient(timeout=timeout_settings) as client:
            response = await client.post(
                WANGENG_QDRANT_UPDATE_METADATA_URL,
                json=dto.model_dump(),
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
    async def update_kb_id_in_payload(
        self, 
        dto: QdrantPayloadUpdateDTO
    ) -> int:
        """
        [独立抽取的 API 调用方法] 负责发送请求，包含重试逻辑
        """
        try:
            if "kb_id" not in dto.payload:
                    raise ValueError("update_doc_id_in_payload函数中元数据payload中必须包含 'kb_id' 字段")

            collection_name = await self.llm_config_service.get_real_model_name(dto.collection_name)
            if not collection_name:
                    raise ValueError(f"知识库：{dto.collection_name}不存在！")
            metadata_ = dto.copy()
            new_kb_id = dto.payload["kb_id"]
            # 兼容doc_id，后端接口只承认doc_kb_id
            qdrant_filter_key = "doc_kb_id" if dto.filter_key == "doc_id" else dto.filter_key
            metadata_.collection_name = collection_name
            metadata_.filter_key = qdrant_filter_key
            metadata_.payload = dto.payload.copy()
            if new_kb_id is None:
                    metadata_.payload["kb_id"] = 0
            timeout_settings = httpx.Timeout(60.0, connect=10.0) # 设置合理的超时
            async with httpx.AsyncClient(timeout=timeout_settings) as client:
                response = await client.post(
                    WANGENG_QDRANT_UPDATE_METADATA_URL,
                    json=metadata_.model_dump(),
                )
                response.raise_for_status() # 如果 4xx/5xx 直接抛出异常触发重试
                
                resp_data = response.json()
                is_success = resp_data.get("success") or (resp_data.get("status") == "success")
                if not is_success:
                    raise Exception(f"API 业务错误: {resp_data}")
            return is_success
        except Exception as e:
            raise