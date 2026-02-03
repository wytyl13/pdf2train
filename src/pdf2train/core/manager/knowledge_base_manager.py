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
from pdf2train.core.table.pdf_document import PdfDocument
from pdf2train.core.schema.retrieval_dto import RetrievalSettings
from pdf2train.core.table.llm_config import LLMConfig
from pdf2train.core.table.llm_enum import ModelType
from pdf2train.api.schema.knowledge_base_schema import KBUpdateDocsReq
from pdf2train.core.schema.base_schema import PageResult
# import service
from pdf2train.core.service.knowledge_base_service import KnowledgeBaseService
from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.core.service.qdrant_service import QdrantService
from pdf2train.core.service.llm_config_service import LLMConfigService
from pdf2train.core.service.document_chunk_service import DocumentChunkService
from pdf2train.core.service.instruction_datum_service import InstructionDatumService
from pdf2train.core.schema.qdrant_dto import EmbeddingTaskDTO



class KnowledgeBaseManager:
    def __init__(
        self,
        kb_service: KnowledgeBaseService,
        pdf_document_service: PdfDocumentService,
        qdrant_service: QdrantService,
        llm_config_service: LLMConfigService,
        document_chunk_service: DocumentChunkService,
        instruction_datum_service: InstructionDatumService
    ):
        self.kb_service = kb_service
        self.pdf_document_service = pdf_document_service
        self.qdrant_service = qdrant_service
        self.llm_config_service = llm_config_service
        self.document_chunk_service = document_chunk_service
        self.instruction_datum_service = instruction_datum_service
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
        return await self.kb_service.create(dto)
    
    async def update_kb(self, kb_id: int, dto: KnowledgeBaseUpdateDTO) -> bool:
        """更新知识库"""
        return await self.kb_service.update(kb_id, dto)
    
    async def get_kb_detail(self, kb_id: int) -> KnowledgeBase:
        """获取详情"""
        return await self.kb_service.get_by_id(kb_id)
    
    async def get_collection_name_by_kb_id(self, kb_id: int) -> Dict[str, Any]:
        """
        获取collection_name，注意统一使用model_name去定义collection_name
        这里要和agent后端接口处理一致
        """
        try:
            kb_data: KnowledgeBase =  await self.kb_service.get_by_id(kb_id)
            embedding_data: LLMConfig = await self.llm_config_service.get_by_id(kb_data.embedding_model_id)
            return embedding_data.model_name
        except Exception as e:
            import traceback
            raise ValueError(f"获取collection_name失败！\n{str(e)} \n{traceback.format_exc()}") from e
    
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
                    # default_embedding_config: LLMConfig = await self.llm_config_service.get_active_config(model_type=ModelType.EMBEDDING)
                    # item.embedding_model_id = default_embedding_config.id if item.embedding_model_id is None else item.embedding_model_id
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
        将文档批量关联到知识库或者从知识库批量解绑
        1. 验证知识库存在
        2. 批量更新 pdf_document.kb_id = kb_id
        5. TODO: 更新 Qdrant Payload 中的 kb_id (如果已向量化)
        6. 返回更新的文档数量
        """
        try:
            # 1. 验证知识库存在+验证kb_id存在+更新 Qdrant Payload 中的 kb_id
            await self.qdrant_service.update_kb_id_in_payload(dto)

            # 2. 更新数据库pdf_document.kb_id字段
            new_kb_id = dto.payload["kb_id"]
            doc_ids = dto.filter_value
            if not isinstance(doc_ids, list):
                doc_ids = [doc_ids]
            return await self.pdf_document_service.update_kb_by_ids(doc_ids, new_kb_id)
        except Exception as e:
            import traceback
            raise ValueError(f"操作失败！\n{str(e)} \n{traceback.format_exc()}") from e
    
    async def get_kb_names_map(self, kb_ids: List[int]) -> Dict[int, str]:
        """供其他 Manager 调用的辅助接口"""
        return await self.kb_service.get_names_by_ids(kb_ids)
    
    async def get_mismatch_docs(self, doc_ids: List[str], embedding_model_id: int) -> List[int]:
        try:
            docs: List[PdfDocument] = await self.pdf_document_service.get_by_ids(doc_ids)
            mismatch_docs = [d for d in docs if d.embedding_llm_config_id != embedding_model_id]
            return [d.id for d in mismatch_docs] if mismatch_docs else []
        except Exception as e:
            raise ValueError(f"获取不匹配的嵌入模型失败！{str(e)}") from e

    async def get_mismatch_need_embedding_docs(self, doc_ids: List[str], embedding_model_id: int) -> List[int]:
        try:
            docs: List[PdfDocument] = await self.pdf_document_service.get_by_ids(doc_ids, is_indexed=True)
            mismatch_docs = [d for d in docs if d.embedding_llm_config_id != embedding_model_id]
            return [d.id for d in mismatch_docs] if mismatch_docs else []
        except Exception as e:
            raise ValueError(f"获取不匹配的嵌入模型失败！{str(e)}") from e
    
    async def get_indexed_docs(self, doc_ids: List[int]) -> List[int]:
        """
        获取指定列表中已完成嵌入（Indexed=True）的所有文档 ID。
        
        原理：
        调用 Service 层的 get_by_ids 并开启 filter_indexed=True，
        底层会自动检查 pipeline_task 表中是否有状态为 SUCCESS 的 QDRANT_INDEX 任务。
        """
        if not doc_ids:
            return []

        try:
            # 1. 查询所有"已索引"的文档对象
            indexed_docs_objs: List[PdfDocument] = await self.pdf_document_service.get_by_ids(
                doc_ids, 
                is_indexed=True 
            )
            
            # 2. 提取 ID 列表
            return [d.id for d in indexed_docs_objs]

        except Exception as e:
            # 记录日志建议带上上下文
            error_msg = f"获取已索引文档失败: {str(e)}"
            # self.logger.error(error_msg) # 如果有 logger
            raise ValueError(error_msg) from e
    
    async def check_update_risk(self, doc_ids: List[str], embedding_model_id: int) -> dict:
        """
        检查文档关联更新的风险
        """
        # 1. 找出模型不一致的文档
        mismatch_ids = await self.get_mismatch_docs(doc_ids, embedding_model_id)
        if not mismatch_ids: return {"needs_confirm": False}

        # 2. 统计需要重新嵌入模型的数据条目数
        chunks_count_map = await self.document_chunk_service.get_indexed_counts_by_doc_ids(mismatch_ids)
        qa_count_map = await self.instruction_datum_service.get_indexed_counts_by_doc_ids(mismatch_ids)

        # 3. 计算切片总数
        total_chunks = sum(chunks_count_map.values())
        # 4. 计算 QA/指令 总数
        total_qas = sum(qa_count_map.values())
        # 5. 计算总影响数
        total_impact = total_chunks + total_qas
        # 6. 生成报告
        if total_impact > 0:
            return {
                "needs_confirm": True,
                "counts": total_impact,
                "msg": f"检测到 {len(mismatch_ids)} 个文档嵌入模型与知识库嵌入模型不一致。强制关联将重新嵌入，导致 **{total_chunks}条切片数据和{total_qas} 条已标注的 QA 数据永久丢失**！是否继续？"
            }
        else:
            return {"needs_confirm": False}