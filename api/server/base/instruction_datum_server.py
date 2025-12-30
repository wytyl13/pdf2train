#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/29 09:50
@Author  : weiyutao
@File    : instruction_datum_server.py
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.encoders import jsonable_encoder
from datetime import datetime
import logging
from pydantic import BaseModel, Field
from typing import Optional, Any, Dict, List
import traceback
import json
import io
import urllib.parse

from api.service.instruction_datum_service import InstructionDatumService


# === Pydantic 请求模型 ===

class InstructionListRequest(BaseModel):
    """查询指令列表请求"""
    doc_id: int = Field(..., description="文档ID")
    page: int = 1
    page_size: int = 20
    keyword: Optional[str] = None

class InstructionUpdateRequest(BaseModel):
    """更新指令请求"""
    id: str = Field(..., description="指令数据的UUID")
    question: Optional[str] = None
    answer: Optional[str] = None
    
    system_prompt: Optional[str] = None
    chain_of_thought: Optional[str] = None
    ref_chunk_ids: Optional[List[str]] = Field(None, description="引用的Chunk ID列表")
    
    is_valid: Optional[int] = Field(None, description="审核状态: 1有效, -1无效/废弃")

class InstructionDeleteRequest(BaseModel):
    """删除指令请求"""
    id: str = Field(..., description="指令数据的UUID")

class InstructionBatchDeleteRequest(BaseModel):
    """按文档清空指令请求"""
    doc_id: int = Field(..., description="文档ID")

class InstructionDatumServer:
    """
    指令数据接口服务
    提供给前端进行 微调数据的人工审核、编辑、导出
    """

    def __init__(self, instruction_datum_service: InstructionDatumService):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.service = instruction_datum_service

    def register_routes(self, app: FastAPI):
        """注册路由"""
        # 列表查询
        app.post("/api/instruction/list")(self.get_list)
        
        # 更新/审核
        app.post("/api/instruction/update")(self.update_datum)
        
        # 删除单条
        app.post("/api/instruction/delete")(self.delete_datum)
        
        # 下载 .jsonl 文件 (核心: 用于微调训练)
        app.get("/api/instruction/download_jsonl/{doc_id}")(self.download_jsonl)
        
        # 预览 (返回 JSON 格式)
        app.get("/api/instruction/preview/{doc_id}")(self.preview_data)
        
        # 物理删除单条
        app.post("/api/instruction/delete")(self.delete_datum)
        
        # 按文档清空
        app.post("/api/instruction/clear_by_doc")(self.clear_by_doc)
        
        # 导出所有数据jsonl
        app.get("/api/instruction/download_jsonl_all")(self.download_jsonl_all)

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

    async def download_jsonl_all(self):
        """下载所有文档的微调数据"""
        try:
            # 调用 Service 不传 ID
            data_list = await self.service.export_for_finetuning(doc_id=None)
            
            if not data_list:
                return self._response(False, "暂无有效数据", code=404)

            lines = [json.dumps(item, ensure_ascii=False) for item in data_list]
            jsonl_str = "\n".join(lines)
            
            stream = io.BytesIO(jsonl_str.encode("utf-8"))
            filename = f"finetune_all_{datetime.now().strftime('%Y%m%d')}.jsonl"
            encoded_filename = urllib.parse.quote(filename)
            
            return StreamingResponse(
                stream, 
                media_type="application/jsonl",
                headers={
                    "Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}"
                }
            )
        except Exception as e:
            self.logger.error(f"下载所有 JSONL 失败: {traceback.format_exc()}")
            return self._response(False, f"下载失败: {str(e)}", code=500)

    async def get_list(self, request: InstructionListRequest):
        """POST 获取指令列表"""
        try:
            result = await self.service.get_instruction_list(
                doc_id=request.doc_id,
                page=request.page,
                page_size=request.page_size,
                keyword=request.keyword
            )
            return self._response(True, "查询成功", result)
        except Exception as e:
            self.logger.error(f"查询指令列表失败: {traceback.format_exc()}")
            return self._response(False, f"查询失败: {str(e)}", code=500)

    async def update_datum(self, request: InstructionUpdateRequest):
        """POST 更新或审核指令"""
        try:
            success = await self.service.update_instruction(
                datum_id=request.id,
                question=request.question,
                answer=request.answer,
                system_prompt=request.system_prompt,
                chain_of_thought=request.chain_of_thought,
                ref_chunk_ids=request.ref_chunk_ids,
                is_valid=request.is_valid
            )
            if success:
                return self._response(True, "更新成功")
            else:
                return self._response(False, "数据不存在或更新失败", code=404)
        except Exception as e:
            self.logger.error(f"更新指令失败: {traceback.format_exc()}")
            return self._response(False, f"更新失败: {str(e)}", code=500)

    async def delete_datum(self, request: InstructionDeleteRequest):
        """POST 删除单条指令"""
        try:
            success = await self.service.delete_instruction(request.id)
            if success:
                return self._response(True, "删除成功")
            else:
                return self._response(False, "数据不存在或删除失败", code=404)
        except Exception as e:
            self.logger.error(f"删除指令失败: {traceback.format_exc()}")
            return self._response(False, f"删除失败: {str(e)}", code=500)

    async def download_jsonl(self, doc_id: int):
        """
        GET 下载微调用的 JSONL 文件
        内容直接从数据库实时生成
        """
        try:
            # 1. 获取标准化数据 (已转为 OpenAI message 格式)
            data_list = await self.service.export_for_finetuning(doc_id)
            
            if not data_list:
                return self._response(False, "暂无有效数据", code=404)

            # 2. 拼接 JSONL 字符串 (每行一个 JSON)
            lines = [json.dumps(item, ensure_ascii=False) for item in data_list]
            jsonl_str = "\n".join(lines)
            
            # 3. 流式返回
            stream = io.BytesIO(jsonl_str.encode("utf-8"))
            filename = f"finetune_doc_{doc_id}.jsonl"
            encoded_filename = urllib.parse.quote(filename)
            
            return StreamingResponse(
                stream, 
                media_type="application/jsonl",  # 或 application/x-jsonlines
                headers={
                    "Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}"
                }
            )
        except Exception as e:
            self.logger.error(f"下载 JSONL 失败: {traceback.format_exc()}")
            return self._response(False, f"下载失败: {str(e)}", code=500)

    async def preview_data(self, doc_id: int):
        """GET 预览数据 (返回标准 JSON 数组)"""
        try:
            data_list = await self.service.export_for_finetuning(doc_id)
            return self._response(True, "获取成功", data_list)
        except Exception as e:
            self.logger.error(f"预览数据失败: {traceback.format_exc()}")
            return self._response(False, f"预览失败: {str(e)}", code=500)
        
    async def delete_datum(self, request: InstructionDeleteRequest):
        """POST 物理删除单条指令"""
        try:
            success = await self.service.delete_instruction(request.id)
            if success:
                return self._response(True, "删除成功")
            else:
                return self._response(False, "数据不存在或删除失败", code=404)
        except Exception as e:
            self.logger.error(f"删除指令失败: {traceback.format_exc()}")
            return self._response(False, f"删除失败: {str(e)}", code=500)

    async def clear_by_doc(self, request: InstructionBatchDeleteRequest):
        """POST 清空某文档下的所有指令"""
        try:
            count = await self.service.delete_by_doc_id(request.doc_id)
            return self._response(True, f"已清空 {count} 条数据")
        except Exception as e:
            self.logger.error(f"清空指令失败: {traceback.format_exc()}")
            return self._response(False, f"清空失败: {str(e)}", code=500)