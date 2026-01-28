#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/28 18:46
@Author  : weiyutao
@File    : qdrant_router.py
"""

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from typing import Any

from pdf2train.utils.response import make_response
from pdf2train.core.manager.qdrant_manager import QdrantManager
from pdf2train.core.schema.qdrant_dto import (
    EmbeddingTaskDTO, 
    MetadataUpdateDTO, 
    IngestBatchDTO
)
from pdf2train.api.schema.qdrant_schema import (
    EmbeddingRunRequest, 
    DocKbBindRequest, 
)
from pdf2train.api.dependencies import get_qdrant_manager

router = APIRouter(prefix="/api/qdrant", tags=["Qdrant 向量化管理"])

@router.post("/embedding", summary="触发文档向量化任务 (异步)")
async def run_embedding_task(
    req: EmbeddingRunRequest,
    background_tasks: BackgroundTasks,
    manager: QdrantManager = Depends(get_qdrant_manager)
):
    """
    [核心接口] 启动文档的向量化流程 (Chunks + Instructions)
    包含: 状态校验 -> 任务初始化 -> 异步执行(导出->嵌入->入库->更新状态)
    """
    try:
        # 1. 同步校验并初始化 Task
        task_id = await manager.validate_and_init_task(doc_id=req.doc_id)
        
        # 2. 提交异步任务
        result = await manager.submit_embedding_task(
            dto=EmbeddingTaskDTO(doc_id=req.doc_id),
            task_id=task_id,
            background_tasks=background_tasks
        )
        return make_response(
            success=True, 
            message="向量化任务已提交后台处理", 
            data={"task_id": task_id, "doc_id": req.doc_id, "status": result["status"]}
        )
    except Exception as e:
        return make_response(success=False, message=str(e), code=500)


@router.post("/bind_kb", summary="批量更新文档关联的知识库 (Metadata)")
async def bind_document_kb(
    req: DocKbBindRequest,
    manager: QdrantManager = Depends(get_qdrant_manager)
):
    """
    [元数据更新] 将一批文档绑定到指定的知识库 (KB ID)
    同时更新 SQL 数据库和 Qdrant 中的 payload
    """
    try:
        dto = MetadataUpdateDTO(
            doc_ids=req.doc_ids,
            kb_id=req.kb_id
        )
        await manager.update_metadata(dto)
        
        return make_response(True, f"成功更新 {len(req.doc_ids)} 个文档的知识库关联")
    except Exception as e:
        return make_response(False, str(e), code=500)


@router.post("/ingest/batch", summary="手动批量插入向量数据")
async def manual_batch_ingest(
    req: IngestBatchDTO,
    manager: QdrantManager = Depends(get_qdrant_manager)
):
    """
    [辅助接口] 手动推送数据到向量库
    通常用于测试或非标准流程的数据修复
    """
    try:
        count = await manager.ingest(req)
        return make_response(True, "入库成功", {"inserted_count": count})
    except Exception as e:
        return make_response(False, str(e), code=500)