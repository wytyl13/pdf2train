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
from sqlalchemy import text



from pdf2train.core.provider.sql_provider import SqlProvider
from pdf2train.core.table.instruction_datum import InstructionDatum

from pdf2train.api.service.base.document_chunk_service import DocumentChunkService
from pdf2train.api.service.base.instruction_datum_service import InstructionDatumService
from pdf2train.api.service.base.pipeline_task_service import PipelineTaskService
from pdf2train.core.table.pipeline_task import InstructionStatus, TaskLifecycle, InstructionTaskResult, TaskType
from pdf2train.core.service.llm_config_service import LLMConfigService

from pdf2train.tool.h1_context_assembler import H1ContextAssembler
from pdf2train.tool.instruction_llm_generator import InstructionLLMGenerator



MIN_CONTENT_LENGTH = 500

class InstructionGenService:
    def __init__(
        self, 
        assembler: H1ContextAssembler,
        document_chunk_service: DocumentChunkService,
        instruction_llm_generator: InstructionLLMGenerator,
        instruction_datum_service: InstructionDatumService,
        llm_config_service: LLMConfigService,
        pipeline_task_service: PipelineTaskService,
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.assembler = assembler
        self.document_chunk_service = document_chunk_service
        self.instruction_llm_generator = instruction_llm_generator
        self.instruction_datum_service = instruction_datum_service
        self.llm_config_service = llm_config_service
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


    async def sync_all_embeddings_for_doc(self, doc_id: int):
        """
        [批量嵌入] 将该文档下所有有效指令同步到向量库，并更新 is_indexed=True
        """
        try:
            # 1. 获取 Embedding 配置
            embed_config = await self.llm_config_service.get_embedding_config_override(doc_id=doc_id)

            # 2. 导出为 Ingestion 格式 (复用你现有的 export 方法)
            ingest_chunks = await self.export_instructions_as_ingest_chunks(doc_id)
            
            if not ingest_chunks:
                self.logger.warning(f"Doc {doc_id} 没有可嵌入的数据")
                return

            # 3. 调用入库服务 (批量写入 Qdrant)
            await self.update_doc_to_kb_service.ingest_data_list(
                doc_id=doc_id,
                data_list=ingest_chunks,
                embedding_config_override=embed_config # 确保传入配置
            )

            # 4. 批量更新数据库状态 is_indexed = True
            sql_provider = SqlProvider(model=InstructionDatum)
            try:
                # 使用 SQL 批量更新效率最高
                stmt = text("""
                    UPDATE instruction_datum 
                    SET is_indexed = true 
                    WHERE doc_id = :doc_id AND is_valid != -1
                """)
                async with sql_provider.get_db_session() as session:
                    await session.execute(stmt, {"doc_id": doc_id})
            finally:
                await sql_provider.close()

        except Exception as e:
            self.logger.error(f"Doc {doc_id} 批量嵌入流程异常: {e}")
            raise e


    async def run(self, doc_id: int) -> List[Dict[str, Any]]:
        # -1. 强制重新执行指令生成，先要对原始指令进行删除（注意该删除会一并删除指定语义嵌入
        # 数据并且需要删除指定的嵌入向量）
        await self.instruction_datum_service.delete_by_doc_id(doc_id=doc_id)
    
        # 0 获取task_id
        task_id = await self.pipeline_task_service.get_specific_task_id_by_doc_id(doc_id=doc_id, task_type_val=TaskType.INSTRUCTION_GEN.value)
        
        # 1 get raw_data
        raw_chunks = await self.document_chunk_service.export_chunks_as_json(doc_id=doc_id)
        chunk_index_map = {c['id']: c['chunk_index'] for c in raw_chunks}
        
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
            has_error = False
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
                        chunk_index_description = []
                        # 添加前端显示chunk编号的字段，因为现在的item中的ref_chunk_ids字段是uuid，如果用户需要追踪不容易，所以以chunk{int}的形式展示最好
                        # 和对应的knowledge base菜单栏中的chunk编号一致
                        ref_chunk_ids = item.get('ref_chunk_ids', [])
                        for ref_chunk_id in ref_chunk_ids:
                            item_chunk_index = chunk_index_map.get(ref_chunk_id)
                            if item_chunk_index is not None:
                                chunk_index_description.append(f"chunk {item_chunk_index + 1}")
                            else:
                                chunk_index_description.append(f"chunk ?")
                        item['chunk_index_description'] = chunk_index_description   

                    # --- C.1 数据库存储伪代码 (DB Storage Pseudo-code) 包含instruction data数据表和进度数据表---
                    await self.instruction_datum_service.batch_save_instructions(generated_data)
                    
                    # C.2 积累返回结果
                    all_generated_results.extend(generated_data)
                    self.logger.info(f"✅ Saved {len(generated_data)} instructions for {h1_title}")
                    # C.3 统计生成结果
                    for item in generated_data:
                        # 统计类型
                        q_type = item.get('metadata', {}).get('type', 'unknown')
                        type_distribution[q_type] = type_distribution.get(q_type, 0) + 1
                        # 统计 Token (如果有)
                        total_tokens += item.get('metadata', {}).get('token_usage', 0)
                    
            except Exception as e:
                has_error = True
                import traceback
                traceback.print_exc()
                error_msg = f"❌ Error processing chapter {h1_title}: {(str(e))} \n"
                self.logger.error(error_msg)
                await self.pipeline_task_service.update_task_status(
                    task_id=task_id, 
                    status=TaskLifecycle.FAILED.value,
                    detailed_status=InstructionStatus.FAILED.value,
                    error_message=error_msg
                )
                # 报错一次就终止
                # continue
            finally:
                # C.4 计算进度并更新任务状态
                current_processed_chars += len(prompt_text)
                if not has_error:
                    # 计算进度
                    # 注意：这里可能因为取整导致看起来没变，所以我们打印日志观察
                    raw_progress = 20 + (70 * (current_processed_chars / total_chars))
                    progress = int(max(20, min(raw_progress, 100)))
                    
                    self.logger.info(f"📈 Progress update: Raw={raw_progress:.2f}%, Int={progress}% (Processed {current_processed_chars}/{total_chars})")
                    
                    # 更新数据库
                    await self.pipeline_task_service.update_task_status(
                        task_id=task_id, 
                        status=TaskLifecycle.RUNNING.value,
                        detailed_status=InstructionStatus.LLM_GENERATING.value,
                        progress=progress
                    )
        
        
        # 6. 循环结束后，统一执行批量嵌入
        if all_generated_results:
            try:
                self.logger.info(f"⚡ [批量嵌入] 文档 {doc_id} 生成完毕，开始同步向量...")
                await self.sync_all_embeddings_for_doc(doc_id)
                self.logger.info(f"✅ [批量嵌入] 文档 {doc_id} 向量同步成功")
            except Exception as e:
                self.logger.error(f"❌ [批量嵌入] 失败: {e}")
                # 如果希望嵌入失败导致整个任务失败，这里请加上: raise e
        
        
        # 7. 构造并更新成功状态
        config = await self.llm_config_service.get_config_by_doc_id(doc_id=doc_id, field_llm_id_name='instruction_gen_llm_config_id')
        task_result = InstructionTaskResult(
            total_count=len(all_generated_results),
            total_tokens=total_tokens,
            type_distribution=type_distribution,
            model_name=config.get('model_name', 'Unknown')
        )
        
        # 更新状态为成功并激活下一步
        await self.pipeline_task_service.update_task_status(
            task_id=task_id, 
            status=TaskLifecycle.SUCCESS.value,
            detailed_status=InstructionStatus.SUCCESS.value,
            progress=100,
            result_data=task_result.model_dump()
        )
        await self.pipeline_task_service.activate_next_step(doc_id=doc_id, current_step_order=3)
        self.logger.info(f"🎉 Finished processing doc {doc_id}. Total generated: {len(all_generated_results)}")
        return all_generated_results