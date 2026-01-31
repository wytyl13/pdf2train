#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/25 12:12
@Author  : weiyutao
@File    : instruction_gen_manager.py
"""

import logging
import traceback
from typing import List, Dict, Any, Optional
from fastapi import BackgroundTasks
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text
import uuid

from pdf2train.core.table.pipeline_task import TaskType, TaskLifecycle, InstructionStatus, InstructionTaskResult
from pdf2train.core.table.instruction_datum import InstructionDatum
from pdf2train.core.table.llm_config import LLMConfig
from pdf2train.core.table.pdf_document import PdfDocument
from pdf2train.core.table.pipeline_task import PipelineTask
from pdf2train.core.schema.pipeline_task_dto import PipelineTaskUpdateDTO
from pdf2train.core.schema.instruction_datum_dto import InstructionDatumCoreDTO

# Services
from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.core.service.document_chunk_service import DocumentChunkService
from pdf2train.core.service.instruction_datum_service import InstructionDatumService
from pdf2train.core.service.pipeline_task_service import PipelineTaskService
from pdf2train.core.service.llm_config_service import LLMConfigService
# 假设 UpdateDocToKBService 用于处理向量入库逻辑
from pdf2train.core.service.qdrant_service import QdrantService

# Tools
from pdf2train.tool.h1_context_assembler import H1ContextAssembler
from pdf2train.tool.instruction_llm_generator import InstructionLLMGenerator


MIN_CONTENT_LENGTH = 500


class InstructionGenManager:
    def __init__(
        self,
        pdf_document_service: PdfDocumentService,
        document_chunk_service: DocumentChunkService,
        instruction_datum_service: InstructionDatumService,
        pipeline_task_service: PipelineTaskService,
        llm_config_service: LLMConfigService,
        qdrant_service: QdrantService,
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.pdf_document_service = pdf_document_service
        self.document_chunk_service = document_chunk_service
        self.instruction_datum_service = instruction_datum_service
        self.pipeline_task_service = pipeline_task_service
        self.llm_config_service = llm_config_service
        self.qdrant_service = qdrant_service
        self.assembler = H1ContextAssembler()
        self.instruction_llm_generator = InstructionLLMGenerator()

    async def validate_and_init_task(
        self, 
        doc_id: int,
    ) -> int:
        """
        [Sync Phase] 仅负责校验和初始化数据库状态
        返回 task_id 供 Router 使用
        """
        try:
            # 1. 校验文档
            doc: PdfDocument = await self.pdf_document_service.get_by_id(doc_id)
            if not doc: raise ValueError(f"文档 {doc_id} 不存在")

            # 2. 校验切片
            counts: Dict[int, int] = await self.document_chunk_service.get_counts_by_doc_ids([doc_id])
            if counts.get(doc_id, 0) == 0: raise ValueError("未切片")

            # 3. 获取并更新任务状态
            task: PipelineTask = await self.pipeline_task_service.get_specific_task_by_doc_id(
                doc_id=doc_id, 
                task_type_val=TaskType.INSTRUCTION_GEN.value
            )
            if not task: raise ValueError("Task未初始化")
            
            # 更新状态为“提交中/等待执行”
            # 在执行任务前更新task和pdf_document表格的状态
            await self.pipeline_task_service.update_and_refresh_parent_doc_status(
                task.id,
                PipelineTaskUpdateDTO(
                    status=TaskLifecycle.RUNNING.value,
                    detailed_status=InstructionStatus.LLM_GENERATING.value,
                    error_message="",
                    progress=InstructionStatus.LLM_GENERATING.value
                )
            )
        except Exception as e:
            raise ValueError("FAIL TO EXEC validate_and_init_task FUNCTION! {str(e)}") from e
        return task.id

    async def submit_instruction_task(
        self, 
        doc_id: int, 
        task_id: int
    ) -> None:
        """
        [Sync Phase] 校验并提交后台任务
        """
        # 4. 提交后台任务
        self.run_instruction_generation(
            doc_id=doc_id,
            task_id=task_id
        )
        self.logger.info(f"🚀 [Doc {doc_id}] 提交指令生成任务")

    async def run_instruction_generation(
        self, 
        doc_id: int, 
        task_id: int,
    ) -> None:
        """
        [Async Phase] 执行指令生成全流程
        执行开始前更新pdf_document表格的状态
        执行结束更新pdf_document表格的最终状态
        执行过程中仅更细task表格的状态
        """
        try:
            # 1. 确保task_id
            assert task_id is not None
            
            # 2. 异步执行，虽然之前做了数据库校验但是因为该函数式异步执行，必做数据库校验
            task: PipelineTask = await self.pipeline_task_service.get_by_id(task_id)
            if not task: raise ValueError("没有初始化嵌入任务！")

            # 3. 清理旧数据 (指令生成是破坏性重做，需要清理旧指令和对应的向量)
            await self.instruction_datum_service.delete_by_doc_id(doc_id=doc_id)

            # 4. 获取原始切片数据
            raw_chunks: List[Dict[str, Any]] = await self.document_chunk_service.export_chunks_json(doc_id=doc_id)
            chunk_index_map = {c['id']: c['chunk_index'] for c in raw_chunks}

            # 5. 组装 H1 上下文 (Assembler Tool)
            h1_content_assembler = await run_in_threadpool(self.assembler.process, raw_chunks)
            # h1_content_assembler = self.assembler.process(raw_chunks)
            
            # 计算总工作量
            total_chars = sum(len(h1.get('prompt_text', '')) for h1 in h1_content_assembler)
            if total_chars == 0:
                await self._finish_task(task_id, [], 0, {}, "Default")
                return

            self.logger.info(f"📊 [Doc {doc_id}] Workload: {len(h1_content_assembler)} chapters, {total_chars} chars.")

            # 6. 准备执行循环
            # (可选) 获取断点续传记录，这里简化为重新开始，如果需要断点续传可在此调用 Service
            processed_titles = [] 
            all_generated_results = []
            total_tokens = 0
            type_distribution = {}
            current_processed_chars = 0
            has_error = False
            last_error_msg = ""

            # 获取使用的模型名称用于记录
            config: LLMConfig  = await self.llm_config_service.get_config_by_doc_id(
                doc_id=doc_id, 
                field_llm_id_name='instruction_gen_llm_config_id'
            )
            
            # 7. 执行 LLM 生成循环
            for h1_data in h1_content_assembler:
                if has_error: break # 遇到严重错误中断

                h1_title = h1_data.get('h1_title', 'Unknown')
                prompt_text = h1_data.get('prompt_text', '')
                
                # Progress Update Logic
                current_processed_chars += len(prompt_text)
                progress = self._calculate_progress(current_processed_chars, total_chars)
                
                # 更新LLM_GENERATING详细进度
                await self.pipeline_task_service.update(
                    task_id,
                    PipelineTaskUpdateDTO(
                        status=TaskLifecycle.RUNNING.value, 
                        detailed_status=InstructionStatus.LLM_GENERATING.value, 
                        progress=progress
                    )
                )
                
                # A. 长度过滤
                if len(prompt_text) < MIN_CONTENT_LENGTH:
                    self.logger.info(f"⏭️ [Skipped] {h1_title}: Too short.")
                    continue

                try:
                    # B. 调用生成器 Tool，因为处理时间较长，开启线程在后台处理，不影响主线程
                    generated_data = await run_in_threadpool(
                        self.instruction_llm_generator.process_single_h1, # 刚才改好的同步方法
                        h1_data=h1_data,
                        llm_config=config
                    )
                    clean_data: List[InstructionDatumCoreDTO] = []
                    if generated_data:
                        # C. 数据补全与清洗
                        for item in generated_data:
                            item['doc_id'] = doc_id
                            item['task_id'] = task_id
                            item['h1_title'] = h1_title
                            
                            # 补全 chunk index 描述 (方便前端展示)
                            ref_chunk_ids = item.get('ref_chunk_ids', [])
                            chunk_desc = []
                            for ref_id in ref_chunk_ids:
                                idx = chunk_index_map.get(ref_id)
                                chunk_desc.append(f"chunk {idx + 1}" if idx is not None else "chunk ?")
                            item['chunk_index_description'] = chunk_desc

                            # 统计分布
                            q_type = item.get('metadata', {}).get('type', 'unknown')
                            type_distribution[q_type] = type_distribution.get(q_type, 0) + 1
                            total_tokens += item.get('metadata', {}).get('token_usage', 0)

                            clean_data.append(InstructionDatumCoreDTO(
                                id=str(uuid.uuid4()),
                                doc_id=item["doc_id"],
                                task_id=item["task_id"],
                                type=item.get("type", "原理机制"),
                                h1_title=item.get("h1_title"),
                                system_prompt=item.get("system_prompt", ""),
                                question=item["question"],
                                answer=item["answer"],
                                chain_of_thought=item.get("chain_of_thought"),
                                ref_chunk_ids=item.get("ref_chunk_ids", []),
                                chunk_index_description=item.get("chunk_index_description", []),
                                meta_info=item.get("meta_info", {}),
                                is_indexed=False,
                                is_valid=0,
                                qdrant_point_id=item.get("id")
                            ))
                            
                            
                        # D. 批量存入数据库 (调用 Service)
                        create_status = await self.instruction_datum_service.create_batch(clean_data)
                        if not create_status:
                            raise ValueError("批量存储指令数据失败！")
                        all_generated_results.extend(generated_data)
                        self.logger.info(f"✅ [Doc {doc_id}] Saved {len(generated_data)} instructions for {h1_title}")

                except Exception as e:
                    # 报错影响后续的操作，并且更新进度
                    import traceback
                    has_error = True
                    last_error_msg = f"Error processing {h1_title}: {str(e)}"
                    error_info = f"❌ {last_error_msg}\n{traceback.format_exc()}"
                    self.logger.error(error_info)
                    await self.pipeline_task_service.update(
                        task_id,
                        PipelineTaskUpdateDTO(
                            status=TaskLifecycle.FAILED.value,
                            detailed_status=InstructionStatus.FAILED.value,
                            error_message=last_error_msg
                        )
                    )
                    raise ValueError(error_info) from e


            # 8. 批量嵌入向量 (Sync Embedding)
            # if all_generated_results:
            #     await self._sync_embeddings(doc_id)

            # 9. 任务完成
            model_name = config.model_name or "unknown"
            
            await self._finish_task(
                task_id, 
                all_generated_results, 
                total_tokens, 
                type_distribution, 
                model_name, 
                doc_id
            )

        except Exception as e:
            import traceback
            error_msg = f"❌ [Doc {doc_id}] 指令生成全局失败: {str(e)} \n {traceback.format_exc()}"
            self.logger.error(f"{error_msg}")
            await self._finish_task(
                task_id=task_id, 
                doc_id=doc_id,
                error_message=error_msg
            )

    # ================= Private Helper Methods =================
    async def _sync_embeddings(self, doc_id: int):
        """
        [内部] 协调多个 Service 完成向量同步
        Manager 负责跨 Service 编排
        """
        try:
            self.logger.info(f"⚡ [Doc {doc_id}] 开始同步指令向量...")
            # 1. 获取 Embedding 配置
            embed_config = await self.llm_config_service.get_embedding_config_override(doc_id=doc_id)
            
            # 2. 从数据库导出待嵌入数据 (Service 层提供数据)
            # 假设 InstructionDatumService 有这个方法，返回适合 Vector Store 的格式
            ingest_data = await self.instruction_datum_service.export_valid_instructions_for_ingest(doc_id)
            
            if not ingest_data:
                return

            # 3. 调用向量入库 Service
            await self.update_doc_to_kb_service.ingest_data_list(
                doc_id=doc_id,
                data_list=ingest_data,
                embedding_config_override=embed_config
            )

            # 4. 更新数据库状态 is_indexed=True
            await self.instruction_datum_service.mark_as_indexed(doc_id)
            
            self.logger.info(f"✅ [Doc {doc_id}] 向量同步完成")
        except Exception as e:
            self.logger.error(f"❌ [Doc {doc_id}] 向量同步失败: {e}")
            raise e

    def _calculate_progress(self, current: int, total: int) -> int:
        """计算进度 (20% - 100%)"""
        if total <= 0: return 20
        raw = 20 + (80 * (current / total)) # 使用 80% 的区间 (20-100)
        return int(max(20, min(raw, 100)))

    async def _finish_task(
        self, 
        task_id, 
        results=None, 
        tokens=None, 
        distribution=None, 
        model_name=None, 
        doc_id=None,
        error_message=None
    ):
        """任务成功收尾"""
        try:
            # 1. 定义完成结果数据
            update_dto = PipelineTaskUpdateDTO(
                status=TaskLifecycle.SUCCESS.value if not error_message else TaskLifecycle.FAILED.value,
                detailed_status=InstructionStatus.SUCCESS.value if not error_message else InstructionStatus.FAILED.value,
                progress=100 if not error_message else -1,
            )
            if not error_message:
                task_result = InstructionTaskResult(
                    total_count=len(results),
                    total_tokens=tokens,
                    type_distribution=distribution,
                    model_name=model_name
                )
                update_dto.result_data=task_result.model_dump()
            else:
                update_dto.error_message = error_message
                
            # 2. 更新任务状态
            await self.pipeline_task_service.update_and_refresh_parent_doc_status(task_id, update_dto)
            
            # 3. 激活下一步
            if doc_id and not error_message:
                await self.pipeline_task_service.activate_next_step(doc_id=doc_id, current_step_order=3)
        except Exception as e:
            error_info = f"任务完成状态更新失败！{str(e)}"
            self.logger.error(error_info)
            raise ValueError(error_info) from e
