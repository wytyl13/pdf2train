#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/23 10:53
@Author  : weiyutao
@File    : document_chunk_router.py
"""

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
import urllib.parse
import json
from io import BytesIO

# Schemas
from pdf2train.api.schema.document_chunk_schema import *

# Manager
from pdf2train.core.manager.document_chunk_manager import DocumentChunkManager
from pdf2train.utils.response import make_response
from pdf2train.core.schema.document_chunk_dto import DocumentChunkFilterDTO, DocumentChunkCoreDTO, DocumentChunkUpdateDTO
from pdf2train.api.schema.document_chunk_schema import ChunkItemRes
from pdf2train.core.schema.base_schema import PageResult
from pdf2train.api.dependencies import get_document_chunk_manager


router = APIRouter(prefix="/api/document_chunk", tags=["Document Chunk"])


@router.post("/list")
async def list_chunks(
    req: ChunkListReq,
    manager: DocumentChunkManager = Depends(get_document_chunk_manager)
):
    try:
        dto_data_res: PageResult[DocumentChunkCoreDTO] = await manager.list_chunks(
            DocumentChunkFilterDTO(**req.model_dump(exclude_unset=True)), 
            req.page, 
            req.page_size
        )
        api_data_res: PageResult[ChunkItemRes] = dto_data_res.map(ChunkItemRes)
        return make_response(True, "查询成功！", api_data_res)
    except Exception as e:
        import traceback
        return make_response(False, f"查询失败！\n {str(e)} \n {traceback.format_exc()}", code=500)
    
@router.post("/update")
async def update_chunk(
    req: ChunkUpdateReq,
    manager: DocumentChunkManager = Depends(get_document_chunk_manager)
):
    success = await manager.update_chunk(req.id, DocumentChunkUpdateDTO(**req.model_dump(exclude={"id"}, exclude_unset=True)))
    if success:
        return make_response(True, "更新成功！")
    return make_response(False, "更新失败！", None)    

@router.post("/delete")
async def delete_chunk(
    req: ChunkDeleteReq,
    manager: DocumentChunkManager = Depends(get_document_chunk_manager)
):
    success = await manager.delete_chunk(req.id)
    if success:
        return make_response(True, "删除成功！")
    return make_response(False, "删除失败！", code=500)

@router.get("/download/{doc_id}")
async def download_json(
    doc_id: int,
    manager: DocumentChunkManager = Depends(get_document_chunk_manager)
):
    data_list = await manager.export_chunks_json(doc_id)
    
    json_str = json.dumps(data_list, ensure_ascii=False, indent=2)
    stream = BytesIO(json_str.encode("utf-8"))
    
    filename = f"doc_{doc_id}_chunks.json"
    encoded_filename = urllib.parse.quote(filename)
    
    return StreamingResponse(
        stream, 
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}"}
    )

@router.get("/preview/{doc_id}")
async def preview_json(
    doc_id: int,
    manager: DocumentChunkManager = Depends(get_document_chunk_manager)
):
    data = await manager.export_chunks_json(doc_id)
    return make_response(True, "成功！", data)

@router.post("/download/stream-pretrain")
async def download_pretrain(
    req: ChunkExportPretrainReq,
    manager: DocumentChunkManager = Depends(get_document_chunk_manager)
):
    generator = await manager.download_pretrain_stream(req.doc_ids)
    
    filename = req.filename if req.filename.endswith(".jsonl") else f"{req.filename}.jsonl"
    encoded_filename = urllib.parse.quote(filename)
    
    return StreamingResponse(
        generator, 
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}"}
    )
    
@router.post("/download/stream-pretrain-by-kb")
async def download_pretrain_by_kb(
    req: ChunkExportPretrainByKbReq,
    manager: DocumentChunkManager = Depends(get_document_chunk_manager)
):
    generator = await manager.download_pretrain_stream_by_kb(req.kb_ids)
    
    filename = req.filename if req.filename.endswith(".jsonl") else f"{req.filename}.jsonl"
    encoded_filename = urllib.parse.quote(filename)
    
    return StreamingResponse(
        generator, 
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}"}
    )