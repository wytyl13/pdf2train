#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/24 12:27
@Author  : weiyutao
@File    : instruction_datum_manager.py
"""

import json
import logging
from typing import Dict, Any, List, Optional, Union
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pdf2train.core.table.instruction_datum import InstructionDatum
from pdf2train.core.table.document_chunk import DocumentChunk
from pdf2train.core.table.pdf_document import PdfDocument, TaskType
from pdf2train.core.table.pipeline_task import PipelineTask, TaskLifecycle, InstructionStatus, IndexStatus

from pdf2train.core.schema.pipeline_task_dto import PipelineTaskUpdateDTO

from pdf2train.core.service.instruction_datum_service import InstructionDatumService
from pdf2train.core.service.document_chunk_service import DocumentChunkService
from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.core.service.pipeline_task_service import PipelineTaskService
from pdf2train.core.service.llm_config_service import LLMConfigService
from pdf2train.core.service.qdrant_service import QdrantService

from pdf2train.core.schema.base_schema import PageResult
from pdf2train.core.schema.instruction_datum_dto import (
    InstructionDatumCoreDTO,
    InstructionDatumUpdateDTO, 
    InstructionDatumFilterDTO
)
from pdf2train.core.schema.qdrant_dto import VectorDeleteRequest, ChunkPayload, IngestRequest, EmbeddingConfigOverride



class InstructionDatumManager:
    """
    指令数据集业务管理实例
    """
    def __init__(
        self,
        instruction_datum_service: InstructionDatumService,
        document_chunk_service: DocumentChunkService,
        pdf_document_service: PdfDocumentService,
        pipeline_task_service: PipelineTaskService,
        llm_config_service: LLMConfigService,
        qdrant_service: QdrantService
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.service = instruction_datum_service
        self.document_chunk_service = document_chunk_service
        self.pdf_document_service = pdf_document_service
        self.pipeline_task_service = pipeline_task_service
        self.llm_config_service = llm_config_service
        self.qdrant_service = qdrant_service
        
    async def list_instructions(
        self, 
        filter_dto: InstructionDatumFilterDTO, 
        page: int, 
        page_size: int, 
    ) -> PageResult[InstructionDatumUpdateDTO]:
        db_result: Dict[str, List[InstructionDatum] | int] = await self.service.search_paginated(filter_dto, page, page_size)
        return PageResult[InstructionDatumCoreDTO](**db_result)
    
    async def update_instruction(
        self,
        instruction_id: str,
        update_dto: InstructionDatumUpdateDTO
    ) -> bool:
        """
        更新指令
        1. 首先获取数据库旧数据
        2. 重置updata_dto的is_indexed
        3. 更新 instruction datum 数据库
        4. 判断是否同步向量数据库
        """
        try:
            # 1. 查看现有的is_indexed等逻辑判断字段
            db_result: InstructionDatum = await self.service.get_by_id(instruction_id)
            if not db_result: raise ValueError(f"指令数据{instruction_id}不存在")
            old_is_indexed_status = db_result.is_indexed
            old_question = db_result.question
            old_is_valid = db_result.is_valid
            should_vector = False
            
            # 2. 处理 is_valid 状态变更逻辑
            if "is_valid" in update_dto.model_fields_set:
                new_is_valid = update_dto.is_valid
                
                # 场景 A: 从 有效(0/1) 变为 无效(-1) 且是已嵌入状态 -> 删除向量
                if old_is_valid != -1 and new_is_valid == -1 and old_is_indexed_status:
                    # 直接删除向量
                    collection_name = await self.llm_config_service.get_collection_name_by_doc_id(db_result.doc_id)
                    vector_delete_request = VectorDeleteRequest(
                        collection_name=collection_name,
                        filters={"chunk_id": instruction_id}
                    )
                    del_count = await self.qdrant_service.delete_vector(vector_delete_request)
                    # 标记数据库为未索引
                    update_dto.is_indexed = False
                    # 后续不再重新嵌入
                    old_is_indexed_status = False 
                    
                # 场景 B: 从 无效(-1) 变为 有效(0/1) 不论旧的嵌入状态何如 -> 需要重新嵌入
                elif old_is_valid == -1 and new_is_valid in [0, 1]:
                    should_vector = True
                    old_is_indexed_status = True
                    update_dto.is_indexed = False
            
            # 2. 初始化is_indexed
            if "question" in update_dto.model_fields_set:
                new_question = update_dto.question
                if new_question:
                    if new_question != old_question:
                        update_dto.is_indexed = False
                    else:
                        if should_vector:
                            update_dto.is_indexed = False
                        else:
                            update_dto.is_indexed = old_is_indexed_status
            
            # 3. 更新数据表
            success = await self.service.update(instruction_id, update_dto)
            if not success: raise ValueError(f"更新数据表操作失败！{instruction_id}")
            
            # 4. 判断是否需要重新嵌入向量 
            if old_is_indexed_status and not update_dto.is_indexed:
                # 4.1 获取需要更新的chunk
                instruction_data: List[Dict[str, Any]] = await self.service.export_instructions_as_ingest_chunks(instruction_id=instruction_id)
                question: str = update_dto.question or instruction_data[0]["text"]
                metadata = instruction_data[0]["metadata"].copy()
                target_metadata = update_dto.model_dump(exclude_unset=True)
                new_metadata = metadata | target_metadata
                item = {"text": question, "metadata": new_metadata}
                chunks = [ChunkPayload(**item)]
                # 4.2 获取嵌入模型配置
                embed_config: EmbeddingConfigOverride = await self.llm_config_service.get_embedding_config_override(db_result.doc_id)
                # 4.2 重新嵌入向量 
                ingest_request = IngestRequest(
                    chunks=chunks,
                    embed_config=embed_config
                )
                count: int = await self.qdrant_service.ingest_api(ingest_request)
                update_dto.is_indexed = True
                success: bool = await self.service.update(instruction_id, update_dto)
            return success
        except Exception as e:
            raise ValueError(f"更新指令数据{instruction_id}失败！{str(e)}") from e
    
    async def delete_instructions_batch(self, instruction_ids: List[str]) -> int:
        """
        [核心逻辑] 批量删除指令数据
        包含：DB删除、向量删除、Task状态检查
        """
        try:
            if not instruction_ids:
                return 0

            # 1. 预查询：获取涉及的 doc_ids
            affected_doc_ids = set()
            
            doc_id_map = await self.service.get_doc_ids_by_ids(instruction_ids) 
            affected_doc_ids = set(doc_id_map.values())

            # 2. 批量物理删除 (DB)
            deleted_count = await self.service.delete_by_ids(instruction_ids)
            if deleted_count == 0:
                return 0

            # 3. 批量删除向量 (Vector)
            # 3.1 获取collection_name 根据 doc_id
            for doc_id in affected_doc_ids:
                collection_name = await self.llm_config_service.get_collection_name_by_doc_id(doc_id)
                # 3.2 构建删除参数
                vector_delete_req = VectorDeleteRequest(
                    collection_name=collection_name,
                    filters={
                        "chunk_id": instruction_ids,
                    }
                )
                vec_del_count = await self.qdrant_service.delete_vector(vector_delete_req)

            # -------------------------------------------------------------------------------------------
            # 4. 如果指令数据为空了，需要重置嵌入步骤状态为PENDING，因为指令数据为待处理状态，后续的步骤必须等待-开始
            count_map = await self.service.get_counts_by_doc_ids(affected_doc_ids)
            for doc_id in affected_doc_ids:
                if count_map.get(doc_id) == 0:
                    # 4.1 更新状态
                    task = await self.pipeline_task_service.get_specific_task_by_doc_id(
                        doc_id, 
                        TaskType.QDRANT_INDEX.value
                    )
                    if task and task.status != TaskLifecycle.PENDING.value:
                        await self.pipeline_task_service.update(
                            task.id,
                            PipelineTaskUpdateDTO(
                                status=TaskLifecycle.PENDING.value,
                                detailed_status=IndexStatus.PENDING.value
                            )
                        )
            # -------------------------------------------------------------------------------------------
            # 4. 如果指令数据为空了，需要重置嵌入步骤状态为PENDING，因为指令数据为待处理状态，后续的步骤必须等待-结束
            
            # 5. 批量检查 Task 状态回滚
            if affected_doc_ids:
                counts_map = await self.service.get_counts_by_doc_ids(list(affected_doc_ids))
                
                for doc_id in affected_doc_ids:
                    current_count = counts_map.get(doc_id, 0)
                    if current_count == 0:
                        task = await self.pipeline_task_service.get_specific_task_by_doc_id(
                            doc_id, TaskType.INSTRUCTION_GEN.value
                        )
                        if task and task.status == TaskLifecycle.SUCCESS.value:
                            await self.pipeline_task_service.update_and_refresh_parent_doc_status(
                                task.id,
                                PipelineTaskUpdateDTO(
                                    status=TaskLifecycle.PENDING.value,
                                    detailed_status=InstructionStatus.PENDING.value,
                                    result_data=None
                                )
                            )
                            self.logger.info(f"文档 {doc_id} 的指令已清空，Task 回滚为 PENDING")
            return deleted_count
        except Exception as e:
            raise ValueError(f"[InstructionDatum]批量删除失败！{str(e)}") from e  
    
    async def delete_instruction(self, instruction_id: str) -> int:
        """
        [单个删除] 只是批量删除的特例
        """
        try:
            count = await self.delete_instructions_batch([instruction_id])
            return count > 0
        except Exception as e:
            raise ValueError(str(e)) from e
    
    async def delete_instruction_single(self, instruction_id: str) -> bool:
        """
        删除指定id指令数据
        1. 直接删除数据库数据
        2. 删除向量数据库
        """
        try:
            # 1. 首先获取db_data
            db_data: InstructionDatum = await self.service.get_by_id(instruction_id)
            if not db_data:
                raise ValueError(f"[InstructionDatum]目标-{instruction_id}-不存在")
            
            # 2. 直接删除数据库数据
            success = await self.service.delete(instruction_id)
            if not success:
                raise ValueError(f"[InstructionDatum]目标删除失败！-{instruction_id}-")
            
            # 3. 删除向量数据库
            
            # 4. 判断是否是最后一个删除，如果是更新对应的task状态为pending
            counts_map: Dict[int, int] = await self.service.get_counts_by_doc_ids([db_data.doc_id])
            if not counts_map.get(db_data.doc_id):
                task_db_data: PipelineTask = await self.pipeline_task_service.get_specific_task_by_doc_id(db_data.doc_id, TaskType.INSTRUCTION_GEN.value)
                task_update_status: bool = await self.pipeline_task_service.update_and_refresh_parent_doc_status(
                    task_db_data.id,
                    PipelineTaskUpdateDTO(
                        status=TaskLifecycle.PENDING.value,
                        detailed_status=InstructionStatus.PENDING.value,
                        result_data=None
                    )
                )
                if not task_update_status:
                    raise ValueError(f"[PipelineTask]更新状态失败-{task_db_data.id}-")
            return success
        except Exception as e:
            raise ValueError(f"删除指令数据{instruction_id}失败！ {str(e)}") from e
        
    async def clear_by_doc(self, doc_id: int) -> int:
        """
        清空doc_id指令数据
        1. 直接删除数据库数据
        2. 删除向量数据库
        """
        try:
            # 1. 直接删除数据库数据
            success = await self.service.delete_by_doc_id(doc_id)
            if not success: raise ValueError(f"[InstructionDatum]清除指令数据失败！-{doc_id}-")
            
            # 2. 直接重置task为pending
            task_db_data: PipelineTask = await self.pipeline_task_service.get_specific_task_by_doc_id(doc_id, TaskType.INSTRUCTION_GEN.value)
            task_update_status: bool = await self.pipeline_task_service.update_and_refresh_parent_doc_status(
                task_db_data.id,
                PipelineTaskUpdateDTO(
                    status=TaskLifecycle.PENDING.value,
                    detailed_status=InstructionStatus.PENDING.value,
                    result_data=None
                )
            )
            if not task_update_status:
                raise ValueError(f"[PipelineTask]重置任务状态失败！-{task_db_data.id}-")
            
            # 3. 删除向量数据库
            # 3.1 获取collection_name 根据 doc_id
            collection_name = await self.llm_config_service.get_collection_name_by_doc_id(doc_id)
            # 3.2 构建删除参数
            vector_delete_req = VectorDeleteRequest(
                collection_name=collection_name,
                filters={
                    "doc_kb_id": doc_id,
                    "type": "instruction"
                }
            )
            vec_del_count = await self.qdrant_service.delete_vector(vector_delete_req)
            
            # -----------------------------------------------------------------------
            # 4. 重置后续嵌入步骤状态开始
            task = await self.pipeline_task_service.get_specific_task_by_doc_id(
                doc_id, 
                TaskType.QDRANT_INDEX.value
            )
            await self.pipeline_task_service.update(
                task.id,
                PipelineTaskUpdateDTO(
                    status=TaskLifecycle.PENDING.value,
                    detailed_status=IndexStatus.PENDING.value
                )
            )
            # 4. 重置后续嵌入步骤状态结束
            # -----------------------------------------------------------------------
            return task_update_status
        except Exception as e:
            raise ValueError(f"清空doc_id指令数据！{doc_id} \n{str(e)}") from e
    
    async def check_cascade_impact(self, chunk_ids: List[str]) -> List[str]:
        """
        接口 1: 判断/预览
        返回受影响的 ID 列表。如果是空列表，说明可以直接删 chunk，不需要确认。
        """
        return await self.service.get_ids_by_ref_chunk_ids(chunk_ids)
    
    async def check_cascade_impact_by_doc_id(self, doc_id: int) -> List[str]:
        """
        [Query] 根据文档 ID 查找所有【且 ref_chunk_ids 不为空】的指令数据 ID。
        
        筛选条件:
        1. doc_id 匹配
        2. ref_chunk_ids 数组长度大于 0 (即不仅仅是文档级指令，而是绑定了具体 Chunk 的指令)
        """
        return await self.service.get_ids_by_ref_ids_doc_id(doc_id)
    
    async def _export_single_doc(self, doc_id: int) -> List[Dict[str, Any]]:
        """
        [内部方法] 导出单个文档的数据
        (这是你原本 export_for_finetuning 的核心逻辑，几乎未改动，只是封装了一下)
        """
        sql_provider_inst = None
        sql_provider_chunk = None
        try:
            # === Step 1: 获取指令数据 ===
            all_instructions: List[InstructionDatum] = await self.service.get_by_doc_id(doc_id)
            
            # === Step 2: 获取切片原文 (构建缓存) ===
            all_chunks: List[DocumentChunk] = await self.document_chunk_service.get_all_by_doc_id(doc_id)
            
            def get_val(obj, key):
                return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)

            chunk_map = {str(get_val(c, "id")): get_val(c, "content") for c in all_chunks}

            # === Step 3: 组装数据 ===
            export_data = []
            
            valid_instructions = [r for r in all_instructions if get_val(r, "is_valid") != -1]
            valid_instructions.sort(key=lambda x: str(get_val(x, "id")))

            for row in valid_instructions:
                instruction = get_val(row, "system_prompt")
                question = get_val(row, "question")
                answer = get_val(row, "answer")
                cot = get_val(row, "chain_of_thought")
                ref_ids = get_val(row, "ref_chunk_ids") or []
                
                if not question or not answer: continue
                
                if not ref_ids:
                    # 场景 A: 知识内化 / 通用问答
                    user_content = question
                else:
                    context_texts = []
                    for rid in ref_ids:
                        content = chunk_map.get(str(rid))
                        if content:
                            context_texts.append(content)
                    
                    context_block = "\n\n".join(context_texts)
                    
                    if context_block:
                        user_content = f"【参考资料】\n{context_block}\n\n【问题】\n{question}"
                    else:
                        user_content = question
                if cot:
                    # 格式：<思考过程> \n\n <最终答案>
                    final_assistant_content = f"<thought>{cot}</thought>\n\n{answer}"
                else:
                    final_assistant_content = answer
                record = {
                    "messages": [
                        {"role": "system", "content": instruction},
                        {"role": "user", "content": user_content},
                        {"role": "assistant", "content": final_assistant_content}
                    ]
                }
                export_data.append(record)
                
            return export_data
            
        except Exception as e:
            self.logger.error(f"Doc {doc_id} 导出失败: {e}")
            # 单个文档失败不应阻断整体流程，返回空列表或抛出取决于需求
            return [] 

    async def export_for_finetuning(
        self, 
        doc_id: Optional[int] = None,
        kb_id: Optional[Union[int, List[int]]] = None
    ) -> List[Dict[str, Any]]:
        """
        [导出入口] 导出微调数据
        :param doc_id: 如果提供，导出指定文档；如果不提供 (None)，导出所有文档
        """
        try:
            all_results = []
            
            if doc_id is not None:
                # === 模式 A: 导出单个 ===
                self.logger.info(f"开始导出单个文档: {doc_id}")
                return await self._export_single_doc(doc_id)
            else:
                target_ids = []
                # === 模式 B: 导出所有 ===
                self.logger.info("开始导出所有文档数据...")
                if kb_id is not None:
                    # 归一化为 List
                    kb_ids_list = kb_id if isinstance(kb_id, list) else [kb_id]
                    self.logger.info(f"开始导出知识库 {kb_ids_list} 下的数据...")
                    # 1. 查出该 KB 下所有的 doc_id
                    target_ids = await self.pdf_document_service.get_doc_ids_by_kb_ids(kb_ids_list)
                else:    
                    # 1. 获取所有有数据的 doc_id
                    target_ids: List[int] = await self.service.get_all_instruction_doc_ids()
                    
                self.logger.info(f"发现 {len(target_ids)} 个包含指令数据的文档")
                if not target_ids:
                    self.logger.warning(f"知识库 {kb_ids_list} 下没有文档")
                    return []
                
                # 2. 循环处理
                for idx, did in enumerate(target_ids):
                    # 打印进度日志
                    if idx % 10 == 0:
                        self.logger.info(f"导出进度: {idx}/{len(target_ids)}")
                    
                    doc_data = await self._export_single_doc(did)
                    all_results.extend(doc_data)
                    
                self.logger.info(f"全量导出完成，共生成 {len(all_results)} 条微调数据")
                return all_results
        except Exception as e:
            raise ValueError(f"导出微调数据失败：{doc_id} - {kb_id}")
    
    async def export_instructions_as_ingest_chunks(self, doc_id: int) -> List[Dict[str, Any]]:
        """
        [向量化专用] 将指令数据导出为待入库的 Chunk 格式
        
        策略：
        1. 向量化目标 (Embedding Target): "question" 字段
        2. 元数据 (Metadata): 包含 answer, 原始引用文本(context), doc_id 等
        """
        try:
            # 0 获取文件名
            db_data: PdfDocument = await self.pdf_document_service.get_by_id(doc_id)
            file_name = "Generated_Instruction" if not db_data else db_data.file_name


            # 1: 获取该文档下所有【有效】的指令数据
            all_instructions: List[InstructionDatum] = await self.service.get_valid_by_doc_id(doc_id)
            if not all_instructions:
                return []

            # 2: 批量获取关联的 DocumentChunk 原始文本
            all_ref_ids = set()
            for inst in all_instructions:
                # 兼容对象/字典读取
                refs = inst.ref_chunk_ids
                if refs:
                    all_ref_ids.update(refs)
            
            chunk_map = {}
            if all_ref_ids:
                all_doc_chunks: List[DocumentChunk] = await self.document_chunk_service.get_all_by_doc_id(doc_id)
                for c in all_doc_chunks:
                    chunk_map[str(c.id)] = c.content

            # 3. 转换为 Ingestion格式
            ingest_chunks = []
            
            for row in all_instructions:
                # 3.1. 提取基础字段
                datum_id = row.id
                question = row.question
                answer = row.answer
                ref_ids = row.ref_chunk_ids
                q_type = row.type

                # 3.2. 构建上下文 (Context)
                context_text = ""
                if ref_ids:
                    # RAG 模式：拼接原始切片内容
                    texts = [chunk_map.get(str(rid), "") for rid in ref_ids if str(rid) in chunk_map]
                    context_text = "\n\n".join(texts)
                else:
                    # 非 RAG 模式：上下文就是答案本身 (或者留空)
                    context_text = answer

                # 3.3 构建 Metadata
                metadata = {
                    "chunk_id": str(datum_id),       # 使用 InstructionDatum 的 UUID 作为 Qdrant Point ID
                    "doc_id": doc_id,                # 关联文档 ID
                    "doc_kb_id": doc_id,             # 兼容之前的 KB 逻辑
                    "filename": file_name, # 虚拟文件名
                    "type": "instruction",           # 标记数据类型，方便过滤
                    "q_type": q_type,                # 指令类型 (原理/操作...)
                    "answer": answer,                # 【核心】检索后直接给 LLM 的答案
                    "context": context_text,         # 【核心】原始参考资料
                    "ref_chunk_ids": ref_ids,
                    "is_instruction": True           # 显式标记
                }

                # 3.4. 构造标准切片对象
                item = {
                    "text": question, 
                    "metadata": metadata
                }
                ingest_chunks.append(item)

            return ingest_chunks

        except Exception as e:
            import traceback
            erro = f"导出指令Chunks失败: {e} \n {traceback.format_exc()}"
            self.logger.error(erro)
            raise erro

    
            

        