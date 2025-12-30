#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/30 11:36
@Author  : weiyutao
@File    : llm_config_server.py
"""


from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from datetime import datetime
import logging
from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, Any, List
import traceback

from api.service.llm_config_service import LLMConfigService
from api.table.base.llm_enum import LLMProvider

# === Pydantic 请求模型 ===

class LLMConfigCreateRequest(BaseModel):
    """创建配置请求"""
    name: str = Field(..., description="配置显示名称", example="DeepSeek-V3")
    provider: LLMProvider = Field(..., description="提供商", example="DeepSeek")
    model_name: str = Field(..., description="模型名称", example="deepseek-chat")
    api_key: str = Field(..., description="API Key")
    base_url: Optional[str] = Field(None, description="Base URL (兼容 OpenAI 格式)")
    is_default: bool = Field(False, description="是否设为默认")

class LLMConfigUpdateRequest(BaseModel):
    """更新配置请求"""
    id: int = Field(..., description="配置ID")
    name: Optional[str] = None
    provider: Optional[LLMProvider] = None
    model_name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    is_default: Optional[bool] = None

class LLMConfigDeleteRequest(BaseModel):
    """删除配置请求"""
    id: int = Field(..., description="配置ID")

class LLMConfigListRequest(BaseModel):
    """列表查询请求"""
    page: int = 1
    page_size: int = 20
    
class GetLLMConfigByDocIdRequest(BaseModel):
    """列表查询请求"""
    doc_id: int = 1
    llm_name: str = "instruction_gen_llm_config"


class LLMConfigServer:
    """
    LLM 配置管理接口
    """

    def __init__(self, llm_config_service: LLMConfigService):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.service = llm_config_service

    def register_routes(self, app: FastAPI):
        """注册路由"""
        
        # 增
        app.post("/api/llm_config/create")(self.create_config)
        
        # 删
        app.post("/api/llm_config/delete")(self.delete_config)
        
        # 改
        app.post("/api/llm_config/update")(self.update_config)
        
        # 查 (列表)
        app.post("/api/llm_config/list")(self.get_list)
        
        app.post("/api/llm_config/get_config_by_doc_id")(self.get_config_by_doc_id)

    def _response(self, success: bool, message: str = "", data: Any = None, code: int = 200):
        return JSONResponse(
            status_code=code,
            content={
                "success": success,
                "message": message,
                "data": jsonable_encoder(data) if data is not None else None,
                "timestamp": datetime.now().isoformat()
            }
        )

    # === 接口实现 ===

    async def create_config(self, request: LLMConfigCreateRequest):
        """创建新配置"""
        try:
            # Pydantic 已经校验了 Enum
            data = request.dict()
            # 枚举转字符串存储
            if isinstance(data.get("provider"), LLMProvider):
                data["provider"] = data["provider"].value

            new_id = await self.service.create_llm_config(data)
            return self._response(True, "创建成功", {"id": new_id})
        except Exception as e:
            self.logger.error(f"创建配置失败: {traceback.format_exc()}")
            return self._response(False, f"创建失败: {str(e)}", code=500)

    async def update_config(self, request: LLMConfigUpdateRequest):
        """更新配置"""
        try:
            data = request.dict(exclude_unset=True) # 仅包含已传字段
            config_id = data.pop("id")
            
            # 枚举处理
            if "provider" in data and isinstance(data["provider"], LLMProvider):
                data["provider"] = data["provider"].value

            success = await self.service.update_llm_config(config_id, data)
            if success:
                return self._response(True, "更新成功")
            else:
                return self._response(False, "配置不存在或更新失败", code=404)
        except Exception as e:
            self.logger.error(f"更新配置失败: {traceback.format_exc()}")
            return self._response(False, f"更新失败: {str(e)}", code=500)

    async def delete_config(self, request: LLMConfigDeleteRequest):
        """删除配置"""
        try:
            success = await self.service.delete_llm_config(request.id)
            if success:
                return self._response(True, "删除成功")
            else:
                return self._response(False, "配置不存在", code=404)
        except Exception as e:
            self.logger.error(f"删除配置失败: {traceback.format_exc()}")
            return self._response(False, f"删除失败: {str(e)}", code=500)

    async def get_list(self, request: LLMConfigListRequest):
        """获取配置列表 (自动脱敏)"""
        try:
            result = await self.service.get_config_list(
                page=request.page, 
                page_size=request.page_size
            )
            return self._response(True, "查询成功", result)
        except Exception as e:
            self.logger.error(f"查询列表失败: {traceback.format_exc()}")
            return self._response(False, f"查询失败: {str(e)}", code=500)
        
    async def get_config_by_doc_id(self, request: GetLLMConfigByDocIdRequest):
        try:
            result = await self.service.get_config_by_doc_id(doc_id=request.doc_id, field_llm_name=request.llm_name)
        except Exception as e:
            return self._response(False, message=f"{str(e)}",data=None)
        return self._response(True, "查询成功", result)
        