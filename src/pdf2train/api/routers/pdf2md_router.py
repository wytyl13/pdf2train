#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 21:42
@Author  : weiyutao
@File    : pdf2md_router.py
"""
from pathlib import Path
from dotenv import load_dotenv, dotenv_values

# dependencies.py
from pdf2train.core.manager.pdf2md_manager import Pdf2MdManager
# 假设 Config 已经实例化
from pdf2train.core.config import core_config 



from fastapi import APIRouter, Depends, BackgroundTasks
from pdf2train.api.schema.pdf2md_schema import Pdf2MdConvertReq
from pdf2train.core.manager.pdf2md_manager import Pdf2MdManager
from pdf2train.api.dependencies import get_pdf2md_manager 

from pdf2train.utils.response import make_response

router = APIRouter(prefix="/api/pdf2md", tags=["PDF2MD"])

@router.post("/convert")
async def convert_pdf(
    req: Pdf2MdConvertReq,
    background_tasks: BackgroundTasks,
    manager: Pdf2MdManager = Depends(get_pdf2md_manager) 
):
    """
    提交PDF转Markdown任务
    """
    try:
        await manager.submit_convert_task(
            doc_id=req.doc_id,
            is_ocr=req.is_ocr,
            split_pages=req.split_pages,
            background_tasks=background_tasks
        )
        return make_response(True, "任务已提交", {"doc_id": req.doc_id})
    except Exception as e:
        import traceback
        return make_response(False, f"{str(e)} \n {traceback.format_exc()}", code=500)