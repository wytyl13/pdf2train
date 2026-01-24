#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/24 17:46
@Author  : weiyutao
@File    : chunk_router.py
"""

from fastapi import APIRouter, Depends, BackgroundTasks
from pdf2train.api.schema.chunk_schema import ChunkRunReq
from pdf2train.core.manager.chunk_manager import ChunkManager

from pdf2train.api.dependencies import get_chunk_manager

from pdf2train.utils.response import make_response # Assuming a standard response helper exists


router = APIRouter(prefix="/api/chunk", tags=["Chunk"])

@router.post("/run")
async def run_chunk_task(
    req: ChunkRunReq,
    background_tasks: BackgroundTasks,
    manager: ChunkManager = Depends(get_chunk_manager)
):
    """
    提交文档切分任务 (异步)
    """
    try:
        await manager.submit_chunk_task(
            doc_id=req.doc_id,
            chunk_size=req.chunk_size,
            overlap=req.chunk_overlap,
            background_tasks=background_tasks
        )
        return make_response(success=True, message="切分任务已提交后台处理", data={"doc_id": req.doc_id})
    except Exception as e:
        return make_response(False, message=f"{str(e)}", code=500)
    