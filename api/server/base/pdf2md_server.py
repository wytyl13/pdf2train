#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/15 18:40
@Author  : weiyutao
@File    : pdf2md_server.py
"""

import uuid
from fastapi import FastAPI, APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import (
    Optional
)
from api.service.pdf2md_service import Pdf2MdService # 导入业务层
from api.service.minio_service import MinioService


# 定义请求模型
class PdfConvertRequest(BaseModel):
    id: int
    is_ocr: Optional[bool] = None
    split_pages: Optional[int] = None


class Pdf2MdServer:
    """
    PDF 转 Markdown 的 HTTP 接口服务
    """
    def __init__(
        self, 
        pdf2md_service: Pdf2MdService,
    ):
        self.pdf2md_service = pdf2md_service # 注入业务实例

    def register_routes(self, app: FastAPI):
        # 1. 提交任务接口
        app.add_api_route("/api/pdf2md/convert", self.convert_pdf, methods=["POST"])

    async def convert_pdf(self, request: PdfConvertRequest, background_tasks: BackgroundTasks):
        try:
            # 1. 校验 ID
            if not request.id:
                return JSONResponse(status_code=400, content={"success": False, "message": "缺少文档ID"})
            
            is_ocr = request.is_ocr or True
            split_pages = request.split_pages or 20
            
            # 2. 将耗时任务放入后台队列
            background_tasks.add_task(
                self.pdf2md_service.process_pdf_pipeline,
                doc_id=request.id,
                is_ocr=is_ocr,
                split_pages=split_pages
            )
            
            # 3. 立即返回 Task ID
            return JSONResponse(content={
                "success": True, 
                "message": "Task submitted successfully", 
                "doc_id": request.id
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500, 
                content={"success": False, "error": str(e)}
            )
    
    