#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/25 12:11
@Author  : weiyutao
@File    : instruction_gen_router.py
"""

from fastapi import APIRouter, Depends, BackgroundTasks
from pdf2train.api.schema.instruction_gen_schema import InstructionGenRunReq
from pdf2train.core.manager.instruction_gen_manager import InstructionGenManager
from pdf2train.utils.response import make_response
from pdf2train.api.dependencies import get_instruction_gen_manager


router = APIRouter(prefix="/api/instruction", tags=["Instruction Gen"])


@router.post("/run")
async def run_instruction_task(
    req: InstructionGenRunReq,
    background_tasks: BackgroundTasks,
    manager: InstructionGenManager = Depends(get_instruction_gen_manager)
):
    """
    [POST] 提交指令生成任务 (异步)
    """
    # 1. 调用 Manager 进行校验和初始化 (await 等待结果)
    try:
        task_id = await manager.validate_and_init_task(req.doc_id)
        
        # 2. execute 方法放入后台
        background_tasks.add_task(
            manager.run_instruction_generation, # 传入函数引用
            doc_id=req.doc_id,
            task_id=task_id,
        )
        return make_response(success=True, message="指令生成任务已提交后台处理", data={"doc_id": req.doc_id})
    except Exception as e:
        return make_response(success=False, message=f"{str(e)}", code=500)
    