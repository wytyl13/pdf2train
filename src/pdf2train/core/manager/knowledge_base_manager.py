#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/19 17:19
@Author  : weiyutao
@File    : knowledge_base_manager.py
"""

import logging
from typing import Dict, Any, List, Optional

# import dto
from pdf2train.core.schema.knowledge_base_dto import KnowledgeBaseCoreDTO, KnowledgeBaseUpdateDTO
from pdf2train.core.schema.qdrant_dto import QdrantPayloadUpdateDTO

from pdf2train.core.table.knowledge_base import KnowledgeBase
from pdf2train.core.schema.retrieval_dto import RetrievalSettings

from pdf2train.core.schema.base_schema import PageResult
# import service
from pdf2train.core.service.knowledge_base_service import KnowledgeBaseService
from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.core.service.qdrant_service import QdrantService
from pdf2train.core.service.llm_config_service import LLMConfigService

class KnowledgeBaseManager:
    def __init__(
        self,
        kb_service: KnowledgeBaseService,
        pdf_document_service: PdfDocumentService,
        qdrant_service: QdrantService,
        llm_config_service: LLMConfigService,
    ):
        self.kb_service = kb_service
        self.pdf_document_service = pdf_document_service
        self.qdrant_service = qdrant_service
        self.llm_config_service = llm_config_service
        self.logger = logging.getLogger(self.__class__.__name__)
        
    
    # ================= 内部工具：字段转换 =================
    def _transform_settings_in(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """前端 (settings) -> 数据库 (_settings)"""
        if "settings" in data:
            settings = data.pop("settings")
            # 兼容 Pydantic 对象或字典
            if hasattr(settings, "model_dump"):
                data["_settings"] = settings.model_dump()
            elif isinstance(settings, dict):
                data["_settings"] = settings
        return data
    
    def _transform_settings_out(self, obj: Any) -> Dict[str, Any]:
        """数据库对象 -> 前端字典 (处理 _settings)"""
        if not obj:
            return None
        
        # 如果是 ORM 对象，转字典
        if not isinstance(obj, dict):
            # 过滤 SQLAlchemy 的内部字段
            data = {k: v for k, v in obj.__dict__.items() if not k.startswith('_sa_')}
        else:
            data = obj.copy()

        # 处理 settings
        raw_config = data.pop("_settings", None)
        data["settings"] = raw_config if raw_config else None
        
        # 清理旧字段（如果存在）
        data.pop("retrieval_settings", None)
        
        return data
    
    # ================= 业务接口 =================
    async def create_kb(self, dto: KnowledgeBaseCoreDTO) -> int:
        """创建知识库"""
        dto.vector_store_collection_name = dto.embedding_model
        return await self.kb_service.create(dto)
    
    async def update_kb(self, kb_id: int, dto: KnowledgeBaseUpdateDTO) -> bool:
        """更新知识库"""
        return await self.kb_service.update(kb_id, dto)
    
    async def get_kb_detail(self, kb_id: int) -> Dict[str, Any]:
        """获取详情"""
        return await self.kb_service.get_by_id(kb_id)
    
    async def get_kb_list(
        self, 
        page: Optional[int] = None, 
        page_size: Optional[int] = None, 
        keyword: str = None
    ) -> Dict[str, KnowledgeBaseCoreDTO | int]:
        """分页查询知识库列表"""
        try:
            db_result: Dict[str, KnowledgeBase | int] = await self.kb_service.search_paginated(page, page_size, keyword)
            default_settings: RetrievalSettings = RetrievalSettings()
            kb_list: List[KnowledgeBase] = db_result.get("items")
            if kb_list:
                for item in kb_list:
                    item.a_settings = default_settings if item.a_settings is None else item.a_settings
            return PageResult[KnowledgeBaseCoreDTO](**db_result)
        except Exception as e:
            raise ValueError(f"分页查询知识库列表失败！{str(e)}") from e
        
    async def delete_kb(self, kb_id: int) -> bool:
        """
        [复杂删除业务]
        1. 查 Collection Name
        2. 远程解绑向量（删除知识库只是删除引用，不删除知识库绑定文件的实际向量数据）
        3. 本地数据库更新pdf_document下的kb_id字段为null
        4. 本地数据库删除对应知识库
        """
        # 1. 查详情
        kb_info = await self.kb_service.get_by_id(kb_id)
        if not kb_info:
            self.logger.warning(f"删除失败: KB {kb_id} 不存在")
            return False

        # 2. 远程解绑向量 (Best Effort)
        try:
            # 兼容字典或对象访问
            embedding_model = getattr(kb_info, 'embedding_model', None) or kb_info.get('embedding_model')
            unbind_request = QdrantPayloadUpdateDTO(
                collection_name=embedding_model,
                filter_key="kb_id",
                filter_value=kb_id,
                payload={"kb_id": 0}
            )
            await self.qdrant_service.update_kb_id_in_payload(unbind_request)
        except Exception as e:
            self.logger.error(f"⚠️ 远程向量解绑失败 (不阻断删除流程): {e}")

        # 3. 本地事务删除
        return await self.kb_service.delete(kb_id)
    
    async def update_docs_to_kb(self, dto: QdrantPayloadUpdateDTO):
        """
        将文档批量关联到知识库
        1. 验证知识库存在
        2. 批量更新 pdf_document.kb_id = kb_id
        5. TODO: 更新 Qdrant Payload 中的 kb_id (如果已向量化)
        6. 返回更新的文档数量
        """
        try:
            # 1. 验证知识库存在+验证kb_id存在+更新 Qdrant Payload 中的 kb_id
            self.qdrant_service.update_kb_id_in_payload(dto)

            # 2. 更新数据库pdf_document.kb_id字段
            new_kb_id = dto.payload["kb_id"]
            doc_ids = dto.filter_value
            if not isinstance(doc_ids, list):
                doc_ids = [doc_ids]
            return self.pdf_document_service.update_kb_by_ids(doc_ids, new_kb_id)
        except Exception as e:
            raise e
    
    async def get_kb_names_map(self, kb_ids: List[int]) -> Dict[int, str]:
        """供其他 Manager 调用的辅助接口"""
        return await self.kb_service.get_names_by_ids(kb_ids)