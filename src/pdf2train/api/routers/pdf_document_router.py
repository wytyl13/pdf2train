#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/19 15:41
@Author  : weiyutao
@File    : pdf_document_router.py
"""
from fastapi import APIRouter, Query, Depends, UploadFile, File, BackgroundTasks
from fastapi.responses import StreamingResponse
from typing import Dict, Union
from io import StringIO

from pdf2train.api.schema.pdf_document_schema import (
    PdfDocCreateReq, PdfDocUpdateReq, DocListReq, PdfDocContentSaveReq,
    PaginatedDocRes, PdfDocDeleteReq, UnassignedReq,
    PdfDocExportBooksReq, PdfDocCountByKbReq
)
from pdf2train.core.schema.pdf_document_dto import (
    PdfDocUpdateDTO, PdfDocFilterDTO, PdfDocRichDTO
)

from pdf2train.api.dependencies import get_pdf_manager
from pdf2train.core.manager.pdf_document_manager import PdfDocumentManager
from pdf2train.utils.response import make_response

router = APIRouter(prefix="/api/pdf_document", tags=["PDF Document"])


@router.post("/upload")
async def upload_document(
    meta: PdfDocCreateReq = Depends(),
    file: UploadFile = File(...),
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """
    上传文档
    """
    try:
        doc = await manager.upload_and_create(
            file=file,
            kb_id=meta.kb_id
        )
        if any([meta.author, meta.original_title, meta.summary, meta.instruction_gen_llm_config]):
            update_dto = PdfDocUpdateDTO(
                author=meta.author,
                original_title=meta.original_title,
                summary=meta.summary,
                instruction_gen_llm_config=getattr(meta, 'instruction_gen_llm_config', None),
                h_title_llm_config=getattr(meta, 'h_title_llm_config', None),
                embedding_llm_config=getattr(meta, 'embedding_llm_config', None)
            )
            # 这里的 doc 是 upload_and_create 返回的完整对象或字典，取 id 进行更新
            doc_id = doc.id if hasattr(doc, 'id') else doc['id']
            await manager.update(doc_id, update_dto)
        return make_response(True, "上传成功！", doc)
    except Exception as e:
        return make_response(False, f"上传失败！\n {str(e)}", code=500)
    
@router.post("/list", response_model=dict)
async def list_docs(
    req: DocListReq,
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """分页查询文档列表"""
    try:
        filter_dto = PdfDocFilterDTO(
            kb_id=req.kb_id,
            keyword=req.keyword,
            status=req.status,
            filter_step_type=req.filter_step_type,
            filter_step_status=req.filter_step_status
        )
        
        result: Dict[str, Union[PdfDocRichDTO, int]] = await manager.get_list_documents(req.page, req.page_size, filter_dto)
        result: PaginatedDocRes = PaginatedDocRes(**result)
        return make_response(True, "查询成功！", result)
    except Exception as e:
        return make_response(False, f"查询失败！{str(e)}", code=500)

@router.post("/update", response_model=dict)
async def update_doc(
    req: PdfDocUpdateReq,
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """更新文档元数据"""
    try:
        # 1. 构建 UpdateDTO
        dto_data = req.model_dump(
            exclude_unset=True, 
            exclude={"id", "confirm_sync"}
        )
        dto = PdfDocUpdateDTO(**dto_data)
        await manager.pdf_service.update(req.id, dto) 
        
        if req.confirm_sync:
            # 2. 触发相关逻辑，Manager 中似乎只有部分实现，这里保留接口
            pass

        return make_response(True, "更新成功")
    except Exception as e:
        import traceback
        return make_response(False, f"更新失败！{str(e)} \n {traceback.format_exc()}", code=500)
    
@router.post("/delete", response_model=dict)
async def delete_doc(
    req: PdfDocDeleteReq,
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """删除文档"""
    try:
        success = await manager.delete(req.doc_id)
        if success:
            return make_response(True, "删除成功")
        return make_response(False, "文档不存在或删除失败", code=404)
    except Exception as e:
        return make_response(False, f"删除异常: {str(e)}", code=500)

@router.post("/unassigned", response_model=dict)
async def get_unassigned_docs(
    req: UnassignedReq,
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """获取未分配知识库的文档"""
    # 提供的 Schema 中缺失 PdfDocUnassignedReq，这里直接用 Query 参数
    result = await manager.get_unassigned_documents(req.page, req.page_size, req.keyword)
    return make_response(True, "查询成功", result)

@router.get("/content", response_model=dict)
async def get_content(
    doc_id: int = Query(..., description="文档ID"),
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """获取Markdown内容"""
    try:
        content = await manager.get_markdown_content(doc_id)
        return make_response(True, "获取成功", {"content": content})
    except FileNotFoundError:
        return make_response(False, "文档不存在", code=404)
    except Exception as e:
        return make_response(False, str(e), code=500)
    
@router.post("/content/save", response_model=dict)
async def save_content(
    req: PdfDocContentSaveReq,
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """保存Markdown内容"""
    try:
        await manager.save_markdown_content(req.doc_id, req.content)
        return make_response(True, "保存成功")
    except Exception as e:
        return make_response(False, str(e), code=500)
    
@router.post("/export_books_jsonl")
async def export_books(
    req: PdfDocExportBooksReq,
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """导出书籍清单 (JSONL 流式下载)"""
    filter_dto = PdfDocFilterDTO(
        kb_id=req.kb_id,
        keyword=req.keyword,
        filter_step_type=req.filter_step_type,
        filter_step_status=req.filter_step_status
    )
    
    try:
        jsonl_content = await manager.export_books_jsonl(filter_dto)
        stream = StringIO(jsonl_content)
        return StreamingResponse(
            stream, 
            media_type="application/x-ndjson",
            headers={"Content-Disposition": "attachment; filename=books.jsonl"}
        )
    except Exception as e:
        return make_response(False, f"导出失败: {str(e)}", code=500)
    

@router.post("/get_doc_count_by_kb_id", response_model=dict)
async def get_doc_count(
    req: PdfDocCountByKbReq,
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """按知识库统计文档数"""
    count = await manager.get_doc_count_by_kb_id(req.kb_id)
    return make_response(True, "查询成功", {"count": count})

@router.get("/statistics", response_model=dict)
async def get_statistics(
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """获取统计概览"""
    stats = await manager.get_statistics()
    return make_response(True, "查询成功", stats)

@router.get("/chunk_count", response_model=dict)
async def get_chunk_count(
    doc_id: int = Query(...),
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """
    获取切片数量
    """
    # 模拟实现：假设 Document 有 task 结果包含 chunk count
    try:
        count = await manager.pdf_service.get_chunk_count(doc_id)
        return make_response(True, "查询成功", {"count": count})
    except Exception as e:
        return make_response(False, str(e), code=500)