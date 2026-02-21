#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/02/04 21:03
@Author  : weiyutao
@File    : retrieval_router.py
"""


from fastapi import APIRouter

from pdf2train.utils.response import make_response


router = APIRouter(prefix="/api/retrieval", tags=["Retrieval"])

import os
import httpx
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

# 引入之前写好的 KB Manager (用于获取配置)
from pdf2train.core.manager.knowledge_base_manager import KnowledgeBaseManager
# 引入 DTO (用于类型提示)
from pdf2train.core.schema.knowledge_base_dto import KnowledgeBaseCoreRichDTO
from pdf2train.api.schema.retrieval_schema import SearchQueryRequest
from pdf2train.api.dependencies import get_knowledge_base_manager


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/retrieval", tags=["Retrieval Proxy"])


@router.post("/search")
async def search(
    req: SearchQueryRequest,
    kb_manager: KnowledgeBaseManager = Depends(get_knowledge_base_manager)
):
    """
    [代理接口] 获取本地配置并调用 Wangeng 检索服务
    """
    try:
        kb_rich_config: KnowledgeBaseCoreRichDTO = await kb_manager.get_kb_detail(req.kb_id)
        if not kb_rich_config: return make_response(False, "知识库不存在", code=404)

        # 2. [构造参数] 准备发送给 Wangeng 的数据
        wangeng_payload = {
            "query": req.query,
            # 🔥 重点: 将 DTO 转为 JSON 兼容的字典 (处理 datetime 等字段)
            "knowledge_base_config": kb_rich_config.model_dump(mode='json'),
            "highlight": req.highlight
        }

        # 3. [远程调用] 发送 HTTP 请求给 Wangeng
        # 建议从环境变量取地址，默认为 docker 内部网络地址
        wangeng_host = os.getenv("WANGENG_API_BASE", "http://wangeng:9040")
        target_url = f"{wangeng_host}/api/vector/search"

        logger.info(f"转发检索请求 -> {target_url} (KB_ID: {req.kb_id})")

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(target_url, json=wangeng_payload)
            
            # 检查 HTTP 状态码
            if resp.status_code != 200:
                logger.error(f"Wangeng服务报错: {resp.text}")
                return make_response(False, f"检索服务异常: {resp.status_code}", code=500)
            
            # 4. [透传响应] 直接返回 Wangeng 的结果
            result_json = resp.json()
            
            # 如果 Wangeng 返回的是标准结构 {"success": true, ...}，直接透传
            if isinstance(result_json, dict) and "success" in result_json:
                return JSONResponse(content=result_json)
            
            # 否则自己包一层
            return make_response(True, "检索成功", result_json)

    except httpx.RequestError as e:
        logger.error(f"连接 Wangeng 失败: {str(e)}")
        return make_response(False, "无法连接检索服务，请检查网络", code=503)
        
    except Exception as e:
        logger.exception("检索接口内部错误")
        return make_response(False, f"系统内部错误: {str(e)}", code=500)