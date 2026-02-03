#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/02/03 15:57
@Author  : weiyutao
@File    : doc_relation_biz.py
"""

from typing import Dict, Any, List, Optional, Union
from fastapi import Depends, BackgroundTasks
from enum import Enum
from pydantic import BaseModel, Field, root_validator

from pdf2train.core.table.knowledge_base import KnowledgeBase

# 导入所有需要的 Manager
from pdf2train.core.manager.knowledge_base_manager import KnowledgeBaseManager
from pdf2train.core.manager.pdf_document_manager import PdfDocumentManager
from pdf2train.core.manager.qdrant_manager import QdrantManager

# 导入 DTO
from pdf2train.api.schema.knowledge_base_schema import RelationAction
from pdf2train.core.schema.qdrant_dto import EmbeddingTaskDTO, QdrantPayloadUpdateDTO, VectorDeleteRequest

class KBUpdateDocsReqDTO(BaseModel):
    """文档关联到知识库请求"""
    kb_id: Optional[int] = Field(default=None, description="知识库ID")
    doc_ids: List[int] = Field(..., description="文档ID列表")
    action: RelationAction = Field(default=RelationAction.BIND, description="操作类型：bind=关联, unbind=解绑")
    force: bool = Field(default=False, description="遇到模型不一致时，是否强制重置")


class DocRelationBiz:
    """
    [业务聚合层] 纯净的业务类，不依赖 API 层的 dependencies
    """
    def __init__(
        self,
        kb_manager: KnowledgeBaseManager,
        pdf_manager: PdfDocumentManager,
        qdrant_manager: QdrantManager
    ):
        self.kb_manager = kb_manager
        self.pdf_manager = pdf_manager
        self.qdrant_manager = qdrant_manager

    async def process_relation_update(
        self,
        req: KBUpdateDocsReqDTO,
        background_tasks: BackgroundTasks
    ) -> Dict[str, Any]:
        """
        核心逻辑：处理关联/解绑，包含风险检查、状态重置、任务提交
        """
        action_text = "关联" if req.action == RelationAction.BIND else "解绑"
        updated_count = 0
        mismatch_docs_count = 0
        try:
            # 1. 获取目标知识库信息以拿到 collection_name
            kb_info: KnowledgeBase = await self.kb_manager.get_kb_detail(req.kb_id)
            if not kb_info: raise ValueError("目标知识库不存在")
            
            collection_name = await self.kb_manager.get_collection_name_by_kb_id(kb_info.id)
            if not collection_name: raise ValueError("知识库配置异常: 缺少向量模型信息")
            target_kb_value = req.kb_id if req.action == RelationAction.BIND else None

            mismatch_docs = []
            mismatch_need_embedding_docs = []
            mismatch_need_embedding_docs_embedding_map = {}
            indexed_docs: List[int] = await self.kb_manager.get_indexed_docs(req.doc_ids)
            if req.action == RelationAction.BIND:
                mismatch_docs: List[int] = await self.kb_manager.get_mismatch_docs(req.doc_ids, kb_info.embedding_model_id)
                mismatch_need_embedding_docs: List[int] = await self.kb_manager.get_mismatch_need_embedding_docs(req.doc_ids, kb_info.embedding_model_id)
                mismatch_need_embedding_docs_embedding_map = await self.pdf_manager.get_collection_names_by_doc_ids(mismatch_need_embedding_docs)
            match_docs = list(set(req.doc_ids) - set(mismatch_docs))
            update_doc_ids = list(set(indexed_docs) - set(mismatch_need_embedding_docs))

            # 2. 检查
            if req.action == RelationAction.BIND and not req.force:
                # 调用 Manager 的检查方法 (下面会写)
                risk_report = await self.kb_manager.check_update_risk(req.doc_ids, kb_info.embedding_model_id)
                # 如果有风险 (needs_confirm = True)
                if risk_report["needs_confirm"]:
                    return {
                        "status": "CONFIRM_REQUIRED",
                        "msg": "存在模型冲突",
                        "data": {
                            "code": "CONFIRM_REQUIRED",
                            "warning_msg": risk_report["msg"],
                            "affected_counts": risk_report["counts"],
                            "no_update_count": len(match_docs),
                            "updated_count": len(update_doc_ids),
                            "mismatch_docs_count": len(mismatch_need_embedding_docs)
                        }
                    }

            # 3. 更新状态&&重新嵌入数据
            if req.action == RelationAction.BIND:
                # 3.1 这里只处理所有不匹配的文档，只要不匹配，不管是否嵌入，全部重置数据库状态
                await self.pdf_manager.reset_embedding_llm_config_id(
                    mismatch_docs, 
                    kb_info.embedding_model_id,
                    req.kb_id
                )
                # 3.2 嵌入mismatch_need_embedding_docs
                for doc_id in mismatch_need_embedding_docs:
                    # 删除doc_id对应的向量，然后重建，但是需要确保传递的collection_name是更改之前的
                    collection_name = mismatch_need_embedding_docs_embedding_map[doc_id]
                    del_req = VectorDeleteRequest(
                        collection_name=collection_name,
                        filter_key="doc_kb_id",
                        filter_value=doc_id
                    )
                    print("del_req=--======================")
                    print(del_req.collection_name)
                    print(del_req.filter_key)
                    print(del_req.filter_value)
                    print("del_req=--======================")
                    await self.qdrant_manager.delete_vectors(del_req)
                    await self.qdrant_manager.submit_embedding_task(
                        dto=EmbeddingTaskDTO(doc_id=doc_id),
                        task_id=None, # 传 None 即可，Manager 会查找或忽略
                        background_tasks=background_tasks
                    )
                    mismatch_docs_count += 1
            
            # 4. 处理匹配文档 (Match) -> 仅更新 KB ID
            if match_docs:
                new_kb_id = req.kb_id if req.action == RelationAction.BIND else None
                await self.pdf_manager.pdf_service.update_kb_by_ids(match_docs, new_kb_id)
            
            # 5. 构造 QdrantPayloadUpdateDTO，只更新已嵌入的数据。没有嵌入的不更新，没必要
            if update_doc_ids:
                qdrant_dto = QdrantPayloadUpdateDTO(
                    collection_name=collection_name,
                    filter_key="doc_id",
                    filter_value=update_doc_ids,
                    payload={"kb_id": target_kb_value}
                )
                # 3. 调用 Manager 执行批量更新
                updated_count = await self.kb_manager.update_docs_to_kb(qdrant_dto)
            # 7. 构造返回信息
            msg = f"成功{action_text} {updated_count} 个文档。"
            if mismatch_docs_count > 0:
                msg += f" 另有 {mismatch_docs_count} 个文档因模型不一致，正在后台重新向量化。"
                
            return {
                "status": "SUCCESS",
                "msg": msg,
                "data": {
                    "no_update_count": len(match_docs),
                    "updated_count": updated_count,
                    "mismatch_docs_count": mismatch_docs_count
                }
            }
        except Exception as e:
            raise ValueError(f"数据库配置失败！{str(e)}") from e

# 依赖注入辅助函数
def get_doc_relation_biz(
    biz: DocRelationBiz = Depends(DocRelationBiz)
) -> DocRelationBiz:
    return biz