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
from pdf2train.core.table.pdf_document import PdfDocument

from pdf2train.core.service.instruction_datum_service import InstructionDatumService
from pdf2train.core.service.document_chunk_service import DocumentChunkService
from pdf2train.core.service.pdf_document_service import PdfDocumentService

from pdf2train.core.schema.base_schema import PageResult
from pdf2train.core.schema.instruction_datum_dto import (
    InstructionDatumCoreDTO,
    InstructionDatumUpdateDTO, 
    InstructionDatumFilterDTO
)


class InstructionDatumManager:
    """
    指令数据集业务管理实例
    """
    def __init__(
        self,
        instruction_datum_service: InstructionDatumService,
        document_chunk_service: DocumentChunkService,
        pdf_document_service: PdfDocumentService
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.service = instruction_datum_service
        self.document_chunk_service = document_chunk_service
        self.pdf_document_service = pdf_document_service
        
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
        
        # 1. 查看现有的is_indexed等逻辑判断字段
        db_result: InstructionDatum = await self.service.get_by_id(instruction_id)
        if not db_result:
            self.logger.warning(f"不存在的instruction_id: {instruction_id}")
            return False
        old_is_indexed_status = db_result.is_indexed
        old_question = db_result.question
        
        # 2. Logic: If content changes, token count changes and index becomes invalid
        if "question" in update_dto.model_fields_set:
            new_question = update_dto.question
            if new_question:
                if new_question != old_question:
                    update_dto.is_indexed = False
                else:
                    update_dto.is_indexed = old_is_indexed_status
        
        # 3. Call Service
        success = await self.service.update(instruction_id, update_dto)
        
        if success:
            # TODO: Async trigger vector deletion or re-embedding here if needed
            # For now, we just marked is_indexed=False in SQL
            if old_is_indexed_status and not update_dto.is_indexed:
                # 重新嵌入向量 
                self.logger.info("重新嵌入向量！")
                pass
            
        return success
    
    async def delete_instruction(self, instruction_id: str) -> bool:
        """
        1. 直接删除数据库数据
        2. 删除向量数据库
        """
        # 1. 直接删除数据库数据
        success = await self.service.delete(instruction_id)

        if success:
            # 2. 删除向量数据库
            pass
            return success
        return False
    
    async def clear_by_doc(self, doc_id: int) -> int:
        """
        1. 直接删除数据库数据
        2. 删除向量数据库
        """
        # 1. 直接删除数据库数据
        success = await self.service.delete_by_doc_id(doc_id)
        if success:
            # 2. 删除向量数据库
            pass
            return success
        return False
    
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

    
            

        