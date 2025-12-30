#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/27 16:21
@Author  : weiyutao
@File    : instruction_gen_service.py
"""


import json
import uuid
import logging
from io import BytesIO
from typing import List
from llama_index.core.schema import Document, TextNode
import os
from typing import (
    Optional,
    Dict,
    Any
)


from tool.h1_context_assembler import H1ContextAssembler
from api.service.document_chunk_service import DocumentChunkService
from api.service.instruction_datum_service import InstructionDatumService
from api.service.pipeline_task_service import PipelineTaskService
from api.table.base.pipeline_task import InstructionStatus, TaskLifecycle, InstructionTaskResult, TaskType
from api.table.base.pdf_document import PdfDocument

from tool.instruction_llm_generator import InstructionLLMGenerator



MIN_CONTENT_LENGTH = 500

class InstructionGenService:
    def __init__(
        self, 
        assembler: H1ContextAssembler,
        document_chunk_service: DocumentChunkService,
        instruction_llm_generator: InstructionLLMGenerator,
        instruction_datum_service: InstructionDatumService,
        pipeline_task_service: PipelineTaskService
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.assembler = assembler
        self.document_chunk_service = document_chunk_service
        self.instruction_llm_generator = instruction_llm_generator
        self.instruction_datum_service = instruction_datum_service
        self.pipeline_task_service = pipeline_task_service
        

    def calculate_progress(self, current_processed_chars, total_chars):
        # 1. 安全检查：防止除以零
        if total_chars <= 0:
            return 20 # 如果没有总字数，默认返回起始值
        try:
            # 2. 计算进度
            raw_progress = 20 + (70 * (current_processed_chars / total_chars))

            # 3. 强制限制范围 (Clamping)
            final_progress = max(20, min(raw_progress, 100))
        except Exception as e:
            return 20
        return int(final_progress)


    async def run(self, doc_id: int) -> List[Dict[str, Any]]:
        # 预处理 -- 清空doc_id对应的instruction datum
        await self.instruction_datum_service.delete_by_doc_id(doc_id=doc_id)
    
        # 0 获取task_id
        tasks = await self.pipeline_task_service.get_tasks_by_doc_id(doc_id)
        extract_task = next((t for t in tasks if t['task_type'] == TaskType.INSTRUCTION_GEN.value), None)
        if not extract_task: return []
        task_id = extract_task['id']
        
        # 1 get raw_data
        raw_chunks = await self.document_chunk_service.export_chunks_as_json(doc_id=doc_id)
        # 2 h1 context assembler
        h1_content_assembler = self.assembler.process(raw_chunks)
        
        all_generated_results = []
        total_tokens = 0
        type_distribution = {}
        # 3 预计算总字数
        total_chars = sum(len(h1.get('prompt_text', '')) for h1 in h1_content_assembler)
        if total_chars == 0:
            # 3.1 字符串为0更新进度
            # 更新进度为100
            await self.pipeline_task_service.update_task_status(
                task_id=task_id, 
                status=TaskLifecycle.SUCCESS.value,
                detailed_status=InstructionStatus.SUCCESS.value,
                progress=100
            )
            return all_generated_results
        self.logger.info(f"📊 Total workload: {len(h1_content_assembler)} chapters, {total_chars} chars.")
        
        # 4 获取断点 _get_processed_h1_titles 定义该方法
        processed_titles = await self.instruction_datum_service.get_processed_h1_titles(task_id)
        processed_titles = []
        current_processed_chars = 0

        # 5 instruction llm generator
        for idx, h1_data in enumerate(h1_content_assembler):
            h1_title = h1_data.get('h1_title', 'Unknown')
            prompt_text = h1_data.get('prompt_text', '')
            
            # --- 情况 A: 断点续传 (跳过) ---
            if h1_title in processed_titles:
                self.logger.info(f"⏭️ [Skipped] {h1_title}")
                # 累加进度
                current_processed_chars += len(prompt_text)
                progress = self.calculate_progress(current_processed_chars, total_chars)
                # 更新进度
                await self.pipeline_task_service.update_task_status(
                    task_id=task_id, 
                    status=InstructionStatus.LLM_GENERATING.value, # 本应该属于detailed_status，特殊情况可以赋值给status，status本应该是全局状态
                    detailed_status=InstructionStatus.LLM_GENERATING.value,
                    progress=progress
                )
                continue
            
            # --- B. 长度过滤逻辑 (Length Filter) ---
            if len(prompt_text) < MIN_CONTENT_LENGTH:
                self.logger.info(f"⏭️ [Skipped] {h1_title}: Content too short ({len(prompt_text)} chars).")
                current_processed_chars += len(prompt_text)
                progress = self.calculate_progress(current_processed_chars, total_chars)
                # update progress
                await self.pipeline_task_service.update_task_status(
                    task_id=task_id, 
                    status=TaskLifecycle.RUNNING.value,
                    detailed_status=InstructionStatus.LLM_GENERATING.value,
                    progress=progress
                )
                continue
            try:
                # --- C. 调用生成器 (Generation) ---
                print(h1_data)
                generated_data = await self.instruction_llm_generator.process_single_h1(doc_id, h1_data=h1_data)
                if generated_data:
                    print(generated_data)
                    for item in generated_data:
                        item['doc_id'] = doc_id        # 补全 doc_id
                        item['task_id'] = task_id        # 补全 task_id
                        item['h1_title'] = h1_title      # 补全 h1_title
                    
                    # --- C.1 数据库存储伪代码 (DB Storage Pseudo-code) 包含instruction data数据表和进度数据表---
                    await self.instruction_datum_service.batch_save_instructions(generated_data)
                    
                    # C.2 积累返回结果
                    all_generated_results.extend(generated_data)
                    
                    # C.3 统计生成结果
                    for item in generated_data:
                        # 统计类型
                        q_type = item.get('metadata', {}).get('type', 'unknown')
                        type_distribution[q_type] = type_distribution.get(q_type, 0) + 1
                        # 统计 Token (如果有)
                        total_tokens += item.get('metadata', {}).get('token_usage', 0)
                    
                    # C.4 计算进度并更新任务状态
                    current_processed_chars += len(prompt_text)
                    progress = self.calculate_progress(current_processed_chars, total_chars)
                    await self.pipeline_task_service.update_task_status(
                        task_id=task_id, 
                        status=TaskLifecycle.RUNNING.value,
                        detailed_status=InstructionStatus.LLM_GENERATING.value,
                        progress=progress
                    )
            except Exception as e:
                error_msg = f"❌ Error processing chapter {h1_title}: {e}"
                self.logger.error(error_msg)
                await self.pipeline_task_service.update_task_status(
                    task_id=task_id, 
                    status=TaskLifecycle.FAILED.value,
                    detailed_status=InstructionStatus.FAILED.value,
                    error_message=error_msg
                )
                continue
        
        # 6. 构造并更新成功状态
        task_result = InstructionTaskResult(
            total_count=len(all_generated_results),
            total_tokens=total_tokens,
            type_distribution=type_distribution,
            model_name=self.instruction_llm_generator.model_name
        )
        await self.pipeline_task_service.update_task_status(
            task_id=task_id, 
            status=TaskLifecycle.SUCCESS.value,
            detailed_status=InstructionStatus.SUCCESS.value,
            progress=100,
            result_data=task_result.model_dump()
        )
        self.logger.info(f"🎉 Finished processing doc {doc_id}. Total generated: {len(all_generated_results)}")
        return all_generated_results