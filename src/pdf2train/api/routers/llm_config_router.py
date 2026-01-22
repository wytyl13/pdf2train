#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/19 17:29
@Author  : weiyutao
@File    : llm_config_router.py
"""

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from datetime import datetime
from typing import Any

# 1. 引入 Request Schema (前端契约)
from pdf2train.api.schema.llm_config_schema import (
    LLMConfigCreateReq, 
    LLMConfigUpdateReq, 
    LLMConfigDeleteReq, 
    LLMConfigListReq,
    GetLLMConfigByDocIdReq,
    LLMConfigDefaultReq
)

# 2. 引入 Core DTO (业务契约)
from pdf2train.core.schema.llm_config_dto import LLMConfigCoreDTO, LLMConfigUpdateDTO

# 3. 引入 Manager
from pdf2train.core.manager.llm_config_manager import LLMConfigManager
from pdf2train.api.dependencies import get_llm_config_manager

router = APIRouter(prefix="/api/llm_config", tags=["LLM Config"])

def make_response(success: bool, message: str = "", data: Any = None, code: int = 200):
    """统一响应封装"""
    return {
        "success": success,
        "message": message,
        "data": jsonable_encoder(data) if data is not None else None,
        "timestamp": datetime.now().isoformat()
    }


# 4. 接口实现
@router.post("/type_list")
async def get_model_type_list(
    manager: LLMConfigManager = Depends(get_llm_config_manager)
):
    """获取模型类型列表 (POST 无参)"""
    try:
        data = await manager.get_model_type_list()
        return make_response(True, "查询成功", data)
    except Exception as e:
        return make_response(False, f"查询失败: {str(e)}", code=500)

@router.post("/provider_list")
async def get_provider_list(
    manager: LLMConfigManager = Depends(get_llm_config_manager)
):
    """获取提供商列表 (POST 无参)"""
    try:
        data = await manager.get_provider_list()
        return make_response(True, "查询成功", data)
    except Exception as e:
        return make_response(False, f"查询失败: {str(e)}", code=500)

@router.post("/create")
async def create_config(
    req: LLMConfigCreateReq, 
    manager: LLMConfigManager = Depends(get_llm_config_manager)
):
    try:
        # 🔥 Router 负责转换: Request(Enum) -> DTO(String)
        dto = LLMConfigCoreDTO(
            name=req.name,
            model_type=req.model_type.value, # Enum -> Str
            provider=req.provider.value,     # Enum -> Str
            model_name=req.model_name,
            api_key=req.api_key,
            base_url=req.base_url,
            is_default=req.is_default
        )
        # 调用 Manager
        new_id = await manager.create_config(dto)
        return make_response(True, "创建成功", {"id": new_id})
    except Exception as e:
        return make_response(False, f"创建失败: {str(e)}", code=500)

@router.post("/update")
async def update_config(
    req: LLMConfigUpdateReq, 
    manager: LLMConfigManager = Depends(get_llm_config_manager)
):
    try:
        # 转换 Update 参数
        update_data = req.model_dump(exclude_unset=True, exclude={'id'})
        
        # 手动处理 Enum -> Str
        if req.model_type: update_data['model_type'] = req.model_type.value
        if req.provider: update_data['provider'] = req.provider.value
        # 构造 DTO
        dto = LLMConfigUpdateDTO(**update_data)
        
        # Manager 只需要 ID 和 DTO
        success = await manager.update_config(req.id, dto)
        if success:
            return make_response(True, "更新成功")
        return make_response(False, "配置不存在或更新失败", code=404)
    except Exception as e:
        return make_response(False, f"更新失败: {str(e)}", code=500)

@router.post("/delete")
async def delete_config(
    req: LLMConfigDeleteReq, 
    manager: LLMConfigManager = Depends(get_llm_config_manager)
):
    """删除配置 (使用 Request 对象接收 ID)"""
    try:
        # Manager 不需要 DeleteDTO，只需要 int ID
        success = await manager.delete_config(req.id)
        if success:
            return make_response(True, "删除成功")
        return make_response(False, "配置不存在", code=404)
    except Exception as e:
        return make_response(False, f"删除失败: {str(e)}", code=500)

@router.post("/list")
async def get_list(
    req: LLMConfigListReq, 
    manager: LLMConfigManager = Depends(get_llm_config_manager)
):
    try:
        # 传递 Enum 给 Manager (Manager 内部处理查询逻辑)
        result = await manager.get_config_list(
            page=req.page, 
            page_size=req.page_size, 
            model_type=req.model_type # 传 Enum
        )
        return make_response(True, "查询成功", result)
    except Exception as e:
        return make_response(False, f"查询失败: {str(e)}", code=500)
    
@router.post("/default_config")
async def get_default_config(
    req: LLMConfigDefaultReq, 
    manager: LLMConfigManager = Depends(get_llm_config_manager)
):
    """获取当前激活的默认配置详情"""
    try:
        m_type = req.model_type.value if req.model_type else "llm"
        result = await manager.get_active_config(model_type=m_type)
        return make_response(True, "查询成功", result)
    except Exception as e:
        return make_response(False, f"查询失败: {str(e)}", code=500)
    
@router.post("/default_config_name")
async def get_default_config_name(
    req: LLMConfigDefaultReq, 
    manager: LLMConfigManager = Depends(get_llm_config_manager)
):
    """获取当前激活的默认配置名称"""
    try:
        m_type = req.model_type.value if req.model_type else "llm"
        # 复用 get_active_config 拿全部信息，然后只取 name
        config = await manager.get_active_config(model_type=m_type)
        result = config.get("name") if config else None
        return make_response(True, "查询成功", result)
    except Exception as e:
        return make_response(False, f"查询失败: {str(e)}", code=500)
    
@router.post("/get_config_by_doc_id")
async def get_config_by_doc_id(
    req: GetLLMConfigByDocIdReq, 
    manager: LLMConfigManager = Depends(get_llm_config_manager)
):
    try:
        result = await manager.get_config_by_doc_id(
            doc_id=req.doc_id, 
            field_llm_name=req.llm_name
        )
        return make_response(True, "查询成功", result)
    except Exception as e:
        # 保持你原来的逻辑：报错时不返回 data，message 为错误信息
        return make_response(False, str(e), None)