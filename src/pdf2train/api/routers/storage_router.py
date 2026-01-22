#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/19 15:41
@Author  : weiyutao
@File    : storage_router.py
"""

from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from typing import Optional
from pydantic import BaseModel

from pdf2train.core.manager.storage_manager import StorageManager
from pdf2train.api.schema.storage_schema import GetUrlReq, DeleteFileReq
from pdf2train.utils.response import make_response
from pdf2train.api.dependencies import get_storage_manager

router = APIRouter(prefix="/api/storage", tags=["Storage"])


@router.post("/url")
async def get_presigned_url(
    get_url_req: GetUrlReq,
    storage_manager: StorageManager = Depends(get_storage_manager) 
):
    """
    获取minio文件临时url
    """
    if not get_url_req.object_name:
        return make_response(False, "object_name must not be null!", code=400)
    url = await storage_manager.get_presigned_url(get_url_req.bucket_name, get_url_req.object_name, get_url_req.expires)
    return make_response(success=True, data=url)


# 虽然前端没用，但建议保留作为管理接口
@router.post("/delete")
async def delete_file(
    delete_file_req: DeleteFileReq,
    storage_manager: StorageManager = Depends(get_storage_manager)
):
    await storage_manager.physical_delete(delete_file_req.bucket_name, delete_file_req.object_name)
    return make_response(True, "已删除！")