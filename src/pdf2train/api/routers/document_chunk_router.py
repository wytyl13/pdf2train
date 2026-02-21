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
from pdf2train.core.manager.instruction_datum_manager import InstructionDatumManager
from pdf2train.utils.response import make_response
from pdf2train.core.schema.document_chunk_dto import DocumentChunkFilterDTO, DocumentChunkCoreDTO, DocumentChunkUpdateDTO
from pdf2train.api.schema.document_chunk_schema import ChunkItemRes, ChunkDeleteCheckReq, ChunkClearCheckReq
from pdf2train.core.schema.base_schema import PageResult
from pdf2train.api.dependencies import get_document_chunk_manager
from pdf2train.api.dependencies import get_instruction_datum_manager

router = APIRouter(prefix="/api/document_chunk", tags=["Document Chunk"])
from pdf2train.utils.export_utils import list_to_jsonl_stream


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
    manager: DocumentChunkManager = Depends(get_document_chunk_manager),
    instruction_datum_manager: InstructionDatumManager = Depends(get_instruction_datum_manager)
):
    try:
        # 1. 删除chunk
        await manager.delete_chunk(req.id)
        # 2. 删除参考该chunk的指令数据集
        if req.cascade_ids:
            count = await instruction_datum_manager.delete_instructions_batch(req.cascade_ids)
        return make_response(True, f"删除成功！")
    except Exception as e:
        return make_response(success=False, message=str(e), code=500)
    
@router.post("/delete_by_id")
async def delete_by_id(
    req: ChunkClearReq,
    manager: DocumentChunkManager = Depends(get_document_chunk_manager),
    instruction_datum_manager: InstructionDatumManager = Depends(get_instruction_datum_manager)
):
    try:
        # 1. 根据doc_id删除全部chunk
        count = await manager.delete_chunks_by_doc_id(req.doc_id)
        
        # 2. 删除cascade_ids
        if req.cascade_ids:
            count = await instruction_datum_manager.delete_instructions_batch(req.cascade_ids)
        return make_response(True, f"删除成功！{str(count)}")
    except Exception as e:
        return make_response(False, str(e), code=500)

@router.post("/delete/check_chunk")
async def check_chunk_delete(
    req: ChunkDeleteCheckReq,
    instruction_datum_manager: InstructionDatumManager = Depends(get_instruction_datum_manager)
):
    try:
        # 1. 查关联 ID
        affected_ids = await instruction_datum_manager.check_cascade_impact([req.id])
        
        # 2. 无影响
        if not affected_ids:
            return make_response(
                success=True, 
                message="无级联影响", 
                data={
                    "need_confirm": False,
                    "warning_message": "此操作将永久删除该语义块数据，此操作不可撤销。确定要继续吗？",
                    "cascade_ids": []
                }
            )

        # 3. 有影响
        count = len(affected_ids)
        msg = f"检测到该切片关联了 {count} 条指令数据。继续操作将同时删除这些指令数据！"

        return make_response(
            success=True, 
            message="需确认", 
            data={
                "need_confirm": True,
                "warning_message": msg,
                "cascade_ids": affected_ids
            }
        )
    except Exception as e:
        return make_response(success=False, message=str(e), code=500)

@router.post("/delete/check_doc_id")
async def check_chunk_delete(
    req: ChunkClearCheckReq,
    instruction_datum_manager: InstructionDatumManager = Depends(get_instruction_datum_manager)
):
    # 1. 查关联 ID
    affected_ids = await instruction_datum_manager.check_cascade_impact_by_doc_id(req.doc_id)
    
    if not affected_ids:
        return make_response(True, "无级联影响", {
            "need_confirm": False,
            "warning_message": "此操作将永久删除该文档下的所有语义块数据，此操作不可撤销。确定要继续吗？",
            "cascade_ids": []
        })

    # 2. 有影响
    count = len(affected_ids)
    msg = f"检测到文档编号 {req.doc_id} 关联了 {count} 条指令数据。继续操作将同时删除这些指令数据！"

    return make_response(True, "需确认", {
        "need_confirm": True,
        "warning_message": msg,
        "cascade_ids": affected_ids
    })

@router.get("/download/{doc_id}")
async def download_json(
    doc_id: int,
    manager: DocumentChunkManager = Depends(get_document_chunk_manager)
):
    data_list = await manager.export_chunks_json(doc_id)
    stream = list_to_jsonl_stream(data_list)
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
    try:
        data = await manager.export_chunks_json(doc_id)
        return make_response(True, "成功！", data)
    except Exception as e:
        return make_response(success=False, message="导出失败！{str(e)}", code=500)

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
    generator = manager.download_pretrain_stream_by_kb(req.kb_ids)
    
    filename = req.filename if req.filename.endswith(".jsonl") else f"{req.filename}.jsonl"
    encoded_filename = urllib.parse.quote(filename)
    
    return StreamingResponse(
        generator, 
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}"}
    )