#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/24 14:00
@Author  : weiyutao
@File    : instruction_datum_router.py
"""

from io import BytesIO
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
import urllib.parse
from datetime import datetime

# 引入 Manager
from pdf2train.core.manager.instruction_datum_manager import InstructionDatumManager

# 引入 Schemas
from pdf2train.core.schema.base_schema import PageResult
from pdf2train.core.schema.instruction_datum_dto import (
    InstructionDatumCoreDTO,
    InstructionDatumFilterDTO,
    InstructionDatumUpdateDTO
)
from pdf2train.api.schema.instruction_datum_schema import (
    InstructionListReq,
    InstructionUpdateReq,
    InstructionDeleteReq,
    InstructionClearByDocReq,
    InstructionExportByKbReq,
    InstructionDatumItemRes
)

# 引入统一响应封装 (假设项目中有这个工具)
from pdf2train.utils.response import make_response

from pdf2train.api.dependencies import get_instruction_datum_manager

# 定义路由前缀和标签
router = APIRouter(prefix="/api/instruction", tags=["Instruction Datum"])


@router.post("/list")
async def list_instructions(
    req: InstructionListReq,
    manager: InstructionDatumManager = Depends(get_instruction_datum_manager)
):
    """
    查询指令列表 (分页)
    """
    try:
        dto_data_res: PageResult[InstructionDatumCoreDTO] = await manager.list_instructions(
                InstructionDatumFilterDTO(**req.model_dump(exclude_unset=True)), 
                req.page, 
                req.page_size
            )
        api_data_res: PageResult[InstructionDatumItemRes] = dto_data_res.map(InstructionDatumItemRes)
        return make_response(success=True, message="查询成功", data=api_data_res)
    except Exception as e:
            import traceback
            return make_response(False, f"查询失败！\n {str(e)} \n {traceback.format_exc()}", code=500)

@router.post("/update")
async def update_instruction(
    req: InstructionUpdateReq,
    manager: InstructionDatumManager = Depends(get_instruction_datum_manager)
):
    """
    更新指令内容
    注意: 
    1. 前端传参使用 Schema (question, answer, etc.)
    2. Manager 将负责将这些参数映射到 DTO 并处理 is_indexed 重置逻辑
    """
    success = await manager.update_instruction(req.id, InstructionDatumUpdateDTO(**req.model_dump(exclude={"id"}, exclude_unset=True)))
    if success:
        return make_response(True, "更新成功！")
    return make_response(False, "更新失败！", 500)    

@router.post("/delete")
async def delete_instruction(
    req: InstructionDeleteReq,
    manager: InstructionDatumManager = Depends(get_instruction_datum_manager)
):
    """
    删除单条指令
    """
    success = await manager.delete_instruction(req.id)
    if success:
        return make_response(success=True, message="删除成功")
    return make_response(success=False, message="指令不存在", code=500)

@router.post("/clear_by_doc")
async def clear_by_doc(
    req: InstructionClearByDocReq,
    manager: InstructionDatumManager = Depends(get_instruction_datum_manager)
):
    """
    按文档清空所有指令
    """
    try:
        count = await manager.clear_by_doc(req.doc_id)
        return make_response(success=True, message=f"已清空 {count} 条指令", data={"count": count})
    except Exception as e:
        return make_response(False, message=f"清空失败！", code=500)
    
@router.get("/preview/{doc_id}")
async def preview_data(
    doc_id: int,
    manager: InstructionDatumManager = Depends(get_instruction_datum_manager)
):
    """
    GET 预览数据
    对应原 Server: preview_data
    """
    # 注意：Manager 需要实现 get_preview_data，逻辑同 export 但不转 string
    data = await manager.service.export_for_finetuning(doc_id=doc_id)
    return make_response(success=True, message="获取成功", data=data)

@router.get("/download_jsonl/{doc_id}")
async def download_jsonl(
    doc_id: int,
    manager: InstructionDatumManager = Depends(get_instruction_datum_manager)
):
    """
    GET 下载单个文档 JSONL
    对应原 Server: download_jsonl
    """
    jsonl_str = await manager.export_finetuning_jsonl(doc_id=doc_id)
    
    if not jsonl_str:
        return make_response(False, "暂无有效数据", code=404)

    stream = BytesIO(jsonl_str.encode("utf-8"))
    filename = f"finetune_doc_{doc_id}.jsonl"
    encoded_filename = urllib.parse.quote(filename)
    
    return StreamingResponse(
        stream, 
        media_type="application/jsonl",
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}"}
    )
    
@router.get("/download_jsonl_all")
async def download_jsonl_all(
    manager: InstructionDatumManager = Depends(get_instruction_datum_manager)
):
    """
    GET 下载所有 JSONL
    对应原 Server: download_jsonl_all
    """
    jsonl_str = await manager.export_finetuning_jsonl(doc_id=None, kb_id=None)
    
    if not jsonl_str:
        return make_response(False, "暂无有效数据", code=404)
        
    stream = BytesIO(jsonl_str.encode("utf-8"))
    filename = f"finetune_all_{datetime.now().strftime('%Y%m%d')}.jsonl"
    encoded_filename = urllib.parse.quote(filename)
    
    return StreamingResponse(
        stream, 
        media_type="application/jsonl",
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}"}
    )
    
@router.post("/download_jsonl_by_kb")
async def download_jsonl_by_kb(
    req: InstructionExportByKbReq,
    manager: InstructionDatumManager = Depends(get_instruction_datum_manager)
):
    """
    POST 按知识库下载 JSONL
    对应原 Server: download_jsonl_by_kb
    """
    kb_ids = [req.kb_id] if isinstance(req.kb_id, int) else req.kb_id
    
    jsonl_str = await manager.export_finetuning_jsonl(doc_id=None, kb_id=kb_ids)
    
    if not jsonl_str:
         return make_response(False, "该知识库下暂无有效的微调数据", code=404)
         
    stream = BytesIO(jsonl_str.encode("utf-8"))
    
    kb_suffix = f"kb_{kb_ids[0]}" if len(kb_ids) == 1 else "multi_kb"
    filename = f"finetune_{kb_suffix}_{datetime.now().strftime('%Y%m%d')}.jsonl"
    encoded_filename = urllib.parse.quote(filename)
    
    return StreamingResponse(
        stream, 
        media_type="application/jsonl",
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}"}
    )