#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/20 21:07
@Author  : weiyutao
@File    : knowledge_base_router.py
"""

from fastapi import APIRouter, Depends
from typing import Any

from pdf2train.utils.response import make_response

# 1. 引入 Request Schema (前端契约)
from pdf2train.api.schema.knowledge_base_schema import (
    KBCreateReq,
    KBUpdateReq,
    KBDeleteReq,
    KBListReq,
    KBDetailReq,
    KBUpdateDocsReq
)

# 2. 引入 Core DTO (业务契约)
from pdf2train.core.schema.knowledge_base_dto import KnowledgeBaseCoreDTO, KnowledgeBaseUpdateDTO
from pdf2train.core.schema.qdrant_dto import QdrantPayloadUpdateDTO
from pdf2train.core.schema.base_schema import PageResult

# 3. 引入 Manager 和 Dependencies
from pdf2train.core.manager.knowledge_base_manager import KnowledgeBaseManager
from pdf2train.api.dependencies import get_knowledge_base_manager 

router = APIRouter(prefix="/api/knowledge_base", tags=["Knowledge Base"])

# ================= 接口实现 =================
@router.post("/create")
async def create_kb(
    req: KBCreateReq, 
    manager: KnowledgeBaseManager = Depends(get_knowledge_base_manager)
):
    """创建知识库"""
    try:
        # 1. Router 负责 Request -> DTO 的转换
        # 注意: Schema 中是 settings, DTO 中是 _settings
        dto = KnowledgeBaseCoreDTO(
            name=req.name,
            description=req.description,
            avatar_url=req.avatar_url,
            embedding_model_id=req.embedding_model_id,
            user_id=req.user_id,
            a_settings=req.a_settings,
            is_public=req.is_public
        )
        
        # 2. 调用 Manager
        new_id = await manager.create_kb(dto)
        return make_response(True, "创建成功", {"id": new_id})
    except Exception as e:
        return make_response(False, f"创建失败: {str(e)}", code=500)


@router.post("/update")
async def update_kb(
    req: KBUpdateReq, 
    manager: KnowledgeBaseManager = Depends(get_knowledge_base_manager)
):
    """更新知识库"""
    try:
        # 1. 提取需更新字段
        update_data = req.model_dump(exclude_unset=True, exclude={'id'})
        
        # 2. 构造 DTO
        dto = KnowledgeBaseUpdateDTO(**update_data)
        
        # 3. 调用 Manager
        success = await manager.update_kb(req.id, dto)
        if success:
            return make_response(True, "更新成功")
        return make_response(False, "知识库不存在或更新失败", code=404)
    except Exception as e:
        return make_response(False, f"更新失败: {str(e)}", code=500)


@router.post("/delete")
async def delete_kb(
    req: KBDeleteReq, 
    manager: KnowledgeBaseManager = Depends(get_knowledge_base_manager)
):
    """删除知识库"""
    try:
        success = await manager.delete_kb(req.id)
        if success:
            return make_response(True, "删除成功")
        return make_response(False, "知识库不存在", code=404)
    except Exception as e:
        return make_response(False, f"删除失败: {str(e)}", code=500)


@router.post("/list")
async def get_kb_list(
    req: KBListReq, 
    manager: KnowledgeBaseManager = Depends(get_knowledge_base_manager)
):
    """分页查询知识库列表"""
    try:
        result: PageResult[KnowledgeBaseCoreDTO] = await manager.get_kb_list(
            page=req.page, 
            page_size=req.page_size, 
            keyword=req.keyword
        )
        return make_response(True, "知识库查询成功！", result)
    except Exception as e:
        return make_response(False, f"{str(e)}", code=500)


@router.post("/detail")
async def get_kb_detail(
    req: KBDetailReq, 
    manager: KnowledgeBaseManager = Depends(get_knowledge_base_manager)
):
    """获取知识库详情"""
    try:
        data = await manager.get_kb_detail(req.id)
        return make_response(True, "查询成功", data) if data else make_response(False, "知识库不存在", code=404)
    except Exception as e:
        return make_response(False, f"查询失败: {str(e)}", code=500)


@router.post("/update_docs")
async def update_docs_relation(
    req: KBUpdateDocsReq,
    manager: KnowledgeBaseManager = Depends(get_knowledge_base_manager)
):
    """
    将文档关联到知识库
    """
    try:
        # 1. 获取目标知识库信息以拿到 collection_name
        kb_info = await manager.get_kb_detail(req.kb_id)
        if not kb_info:
            return make_response(False, "目标知识库不存在", code=404)
        
        # 兼容字典或对象访问
        collection_name = await manager.get_collection_name_by_kb_id(kb_info.get("id"))
        if not collection_name: return make_response(False, "知识库配置异常: 缺少向量模型信息", code=500)

        # 2. 构造 QdrantPayloadUpdateDTO
        qdrant_dto = QdrantPayloadUpdateDTO(
            collection_name=collection_name,
            filter_key="doc_id",
            filter_value=req.doc_ids,
            payload={"kb_id": req.kb_id}
        )

        # 3. 调用 Manager 执行批量更新
        updated_count = await manager.update_docs_to_kb(qdrant_dto)
        
        return make_response(True, f"成功关联 {updated_count} 个文档", {"updated_count": updated_count})
        
    except Exception as e:
        return make_response(False, f"关联文档失败: {str(e)}", code=500)
