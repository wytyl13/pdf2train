#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/10 11:58
@Author  : weiyutao
@File    : knowledge_base_service.py
"""

import logging
from typing import Dict, Any, Optional
from sqlalchemy import update, delete
from datetime import datetime

from pdf2train.core.table.knowledge_base import KnowledgeBase
from pdf2train.core.provider.sql_provider import SqlProvider
from pdf2train.core.table.knowledge_base import KnowledgeBase
from pdf2train.api.service.base.embedding_service import EmbeddingService, MetadataUpdateRequest
from pdf2train.api.service.base.update_doc_to_kb_service import UpdateDocToKbService, UnbindKbId
from pdf2train.core.table.pdf_document import PdfDocument
from pdf2train.api.service.base.llm_config_service import LLMConfigService

class KnowledgeBaseService:
    def __init__(
        self, 
        embedding_service: EmbeddingService,
        llm_config_service: LLMConfigService,
        update_doc_to_kb_service: UpdateDocToKbService
    ):
        self.embedding_service = embedding_service
        self.llm_config_service = llm_config_service
        self.update_doc_to_kb_service = update_doc_to_kb_service
        self.logger = logging.getLogger(self.__class__.__name__)


    async def update_docs_to_kb(
        self, 
        metadata_update_request: MetadataUpdateRequest,
        update_sql: bool = True
    ) :
        """
        将一批文档关联到指定知识库
        """
        return await self.embedding_service.update_docs_to_kb(
            metadata_update_request=metadata_update_request,
            update_sql=update_sql
        )
        

    async def create_kb(self, data: Dict[str, Any]) -> int:
        """创建知识库"""
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=KnowledgeBase)
            
            # === 特殊字段处理 ===
            # 将前端传来的 retrieval_settings (可能是 Pydantic 对象或字典)
            # 转换为数据库列名 _settings
            settings = data.pop("settings", None)
            if settings:
                if hasattr(settings, "model_dump"):
                    data["_settings"] = settings.model_dump()
                elif isinstance(settings, dict):
                    data["_settings"] = settings
            
            # 默认值处理
            if not data.get("vector_store_collection_name"):
                # 如果没传，暂时留空或生成默认规则，后续逻辑统一处理
                pass

            res_id = await sql_provider.add_record(data)
            return res_id
        except Exception as e:
            self.logger.error(f"创建知识库异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def update_kb(self, kb_id: int, data: Dict[str, Any]) -> bool:
        """更新知识库"""
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=KnowledgeBase)

            # === 特殊字段处理 ===
            settings = data.pop("settings", None)
            if settings is not None:
                # 注意：这里我们覆盖更新配置
                if hasattr(settings, "model_dump"):
                    data["_settings"] = settings.model_dump()
                elif isinstance(settings, dict):
                    data["_settings"] = settings
            
            result = await sql_provider.update_record(kb_id, data)
            return result
        except Exception as e:
            self.logger.error(f"更新知识库异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def delete_kb(self, kb_id: int) -> bool:
        """
        [安全删除模式]
        删除知识库，但保留文档文件。
        1. Vector层 (远程): 调用 Wangeng 服务，将关联向量标记为无主 (kb_id=0)
        2. SQL层 (本地): 将关联文档解除绑定 (kb_id=NULL)
        3. SQL层 (本地): 物理删除知识库记录
        """
        sql_provider = None
        try:
            # 1. 获取 KB 信息，为了获取 Collection Name
            kb_info = await self.get_kb_detail(kb_id)
            if not kb_info:
                self.logger.warning(f"删除知识库失败: 未找到 ID={kb_id} 的记录")
                return False

            collection_name = await self.llm_config_service.get_real_model_name(kb_info.get("embedding_model"))
            # 2. 解绑远程向量
            try:
                unbind_request = UnbindKbId(
                    collection_name=collection_name,
                    kb_id=kb_id
                )
                await self.update_doc_to_kb_service.unbind_kb_id(unbind_request)
            except Exception as e:
                self.logger.error(f"⚠️ 远程向量解绑失败，可能导致僵尸向量数据: {e}")

            # 3. 处理数据库
            sql_provider = SqlProvider(model=KnowledgeBase)
            
            async with sql_provider.get_db_session() as session:
                async with session.begin(): # 开启事务
                    
                    # 3.1 手动解绑文档 (kb_id -> NULL)
                    stmt_unbind = (
                        update(PdfDocument)
                        .where(PdfDocument.kb_id == kb_id)
                        .values(kb_id=None, update_time=datetime.now())
                    )
                    await session.execute(stmt_unbind)

                    # 3.2 删除知识库记录
                    stmt_delete_kb = (
                        delete(KnowledgeBase)
                        .where(KnowledgeBase.id == kb_id)
                    )
                    await session.execute(stmt_delete_kb)

            return True

        except Exception as e:
            self.logger.error(f"删除知识库异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def delete_kb_bake(
        self, 
        kb_id: int
    ) -> bool:
        """
        删除知识库
        注意：由于数据库设置了 cascade="all, delete-orphan"，
        删除 KB 会自动物理删除关联的 PdfDocument 记录。
        TODO: 此处未来需要添加清理 Qdrant 向量数据的逻辑 (根据 kb_id 清除 payload)
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=KnowledgeBase)
            # 物理删除
            result = await sql_provider.delete_record(kb_id, hard_delete=True)
            return result
        except Exception as e:
            self.logger.error(f"删除知识库异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()


    async def get_kb_list(self, page: int = 1, page_size: int = 20, user_id: int = None, keyword: str = None) -> Dict[str, Any]:
        """获取知识库列表"""
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=KnowledgeBase)
            
            condition = {}
            if user_id:
                condition["user_id"] = user_id
            filters = []
            if keyword:
                filters.append(KnowledgeBase.name.like(f"%{keyword}%"))
            # 如果有关键词搜索 (假设 SqlProvider 支持 like 搜索，或者自行构建 SQL)
            # 这里暂时只支持精确匹配或基础筛选，复杂筛选需扩展 SqlProvider
            
            result = await sql_provider.get_records_paginated(
                page=page, 
                page_size=page_size, 
                condition=condition,
                filters=filters,
                order_by=KnowledgeBase.create_time.desc()
            )
            
            # === 数据转换 ===
            # 将数据库的 _settings 转换为前端友好的 retrieval_settings
            items = result.get("items", []) if isinstance(result, dict) else result
            processed_items = []
            for item in items:
                if not isinstance(item, dict):
                    item = {k: v for k, v in item.__dict__.items() if not k.startswith('_sa_')}
                
                # A. 拿出 _settings (原始数据)
                # B. 同时把它从 item 里删掉 (pop)
                raw_config = item.pop("_settings", None)
                
                # C. 赋值给新名字 "settings"
                item["settings"] = raw_config if raw_config else None
                
                # D. 确保也没有 retrieval_settings 这个key (防止万一)
                item.pop("retrieval_settings", None)

                processed_items.append(item)
            
            if isinstance(result, dict):
                result["items"] = processed_items
            else:
                result = processed_items
                
            return result
        except Exception as e:
            self.logger.error(f"查询知识库列表异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def get_kb_detail(self, kb_id: int) -> Optional[Dict[str, Any]]:
        """获取单个知识库详情"""
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=KnowledgeBase)
            records = await sql_provider.get_record_by_condition({"id": kb_id})
            
            if not records:
                return None
            
            record = records[0]
            if not isinstance(record, dict):
                 record = {k: v for k, v in record.__dict__.items() if not k.startswith('_sa_')}
            
            raw_config = record.pop("_settings", None)
            record["settings"] = raw_config if raw_config else None
            
            return record
        except Exception as e:
            self.logger.error(f"查询知识库详情异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()