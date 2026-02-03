#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/29 09:47
@Author  : weiyutao
@File    : instruction_datum_service.py
"""


from typing import List, Dict, Any, Optional
from sqlalchemy import select, func, or_, delete, update, cast, and_
from sqlalchemy.ext.asyncio import AsyncSession
import logging


from pdf2train.core.configs.sql_config import SqlConfig
from pdf2train.core.provider.sql_provider import SqlProvider
from pdf2train.core.table.instruction_datum import InstructionDatum
from pdf2train.core.table.pdf_document import PdfDocument
from pdf2train.core.table.document_chunk import DocumentChunk

from pdf2train.core.schema.instruction_datum_dto import (
    InstructionDatumCoreDTO, 
    InstructionDatumUpdateDTO, 
    InstructionDatumFilterDTO
)
from sqlalchemy.dialects.postgresql import JSONB



class InstructionDatumService:
    """
    instruction datum service
    """
    def __init__(self, sql_config: Optional[SqlConfig] = None):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.model=InstructionDatum
        self.sql_config = sql_config
        self.sql_provider = SqlProvider(
            model=self.model,
            sql_config=self.sql_config 
        )
        
    async def create_batch(self, dtos: List[InstructionDatumCoreDTO]) -> int:
        """批量创建"""
        data_list = [dto.model_dump() for dto in dtos]
        return await self.sql_provider.batch_create(data_list)
    
    async def update(self, instruction_id: str, dto: InstructionDatumUpdateDTO) -> bool:
        """更新"""
        # exclude_unset=True 保证只更新 DTO 中显式赋值的字段
        data = dto.model_dump(exclude_unset=True)
        return await self.sql_provider.update_record(instruction_id, data)
    
    async def delete(self, instruction_id: str) -> bool:
        return await self.sql_provider.delete_record(record_id=instruction_id, hard_delete=True)
    
    async def delete_by_doc_id(self, doc_id: int) -> int:
        condition = {"doc_id": doc_id}
        return await self.sql_provider.delete_records_by_condition(condition)
    
    async def get_by_id(self, instruction_id: str) -> bool:
        results: List[InstructionDatum] = await self.sql_provider.get_record_by_condition({"id": instruction_id})
        return results[0] if results else None
    
    async def get_by_doc_id(self, doc_id: int) -> List[InstructionDatum]:
        return await self.sql_provider.get_record_by_condition({"doc_id": doc_id})
    
    async def get_ids_by_ref_chunk_ids(self, chunk_ids: List[str]) -> List[str]:
        """
        [PostgreSQL 专用优化版]
        找到所有参考了该 chunk_ids 列表及其子集的指令数据ID。
        """
        if not chunk_ids:
            return []

        async with self.sql_provider.get_db_session() as session:
            conditions = [
                cast(self.model.ref_chunk_ids, JSONB).contains([cid])
                for cid in chunk_ids
            ]

            # 修改点：select(self.model) -> select(self.model.id)
            # 仅查询 ID 列，减少数据传输
            stmt = select(self.model.id).where(or_(*conditions))
            
            result = await session.execute(stmt)
            return list(result.scalars().all())
    
    async def get_doc_ids_by_ids(self, ids: List[str]) -> Dict[str, int]:
        """[新增] 根据指令ID列表查询对应的文档ID"""
        if not ids: return {}
        async with self.sql_provider.get_db_session() as session:
            # 只查 id 和 doc_id 两个字段
            stmt = select(self.model.id, self.model.doc_id).where(self.model.id.in_(ids))
            result = await session.execute(stmt)
            # 返回 {instruction_id: doc_id}
            return {row.id: row.doc_id for row in result}
    
    async def get_ids_by_ref_ids_doc_id(self, doc_id: int) -> List[str]:
        """
        [Query] 根据文档 ID 查找所有【且 ref_chunk_ids 不为空】的指令数据 ID。
        
        筛选条件:
        1. doc_id 匹配
        2. ref_chunk_ids 数组长度大于 0 (即不仅仅是文档级指令，而是绑定了具体 Chunk 的指令)
        """
        async with self.sql_provider.get_db_session() as session:
            stmt = select(self.model.id).where(
                and_(
                    self.model.doc_id == doc_id,
                    func.jsonb_array_length(cast(self.model.ref_chunk_ids, JSONB)) > 0
                )
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
    
    async def delete_by_ids(self, ids: List[str]) -> int:
        """
        [Command] 仅删除：根据 ID 列表物理删除
        通用性极强，不依赖 chunk 逻辑，任何需要删 instruction 的地方都能用
        """
        if not ids:
            return 0

        async with self.sql_provider.get_db_session() as session:
            # 直接 where id in (...)
            stmt = delete(self.model).where(self.model.id.in_(ids))
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount
    
    async def get_counts_by_doc_ids(self, doc_ids: List[int]) -> Dict[int, int]:
        """
        批量统计文档的 instruction 数量
        """
        if not doc_ids:
            return {}

        # 1. 使用 async with 获取上下文管理的 session
        async with self.sql_provider.get_db_session() as session:
            # 2. 构建查询语句
            stmt = (
                select(self.model.doc_id, func.count(self.model.id))
                .where(self.model.doc_id.in_(doc_ids))
                .group_by(self.model.doc_id)
            )
            
            # 3. 执行查询
            result = await session.execute(stmt)
            
            # 4. 转换结果为字典 {doc_id: count}
            return dict(result.all())
              
    async def get_indexed_counts_by_doc_ids(self, doc_ids: List[int]) -> Dict[int, int]:
        """
        批量统计文档的 chunks 数量 (仅统计已索引/已嵌入 is_indexed=True 的数据)
        """
        if not doc_ids: return {}

        async with self.sql_provider.get_db_session() as session:
            stmt = (
                select(self.model.doc_id, func.count(self.model.id))
                .where(self.model.doc_id.in_(doc_ids))
                .where(self.model.is_indexed.is_(True)) 
                .group_by(self.model.doc_id)
            )
            
            result = await session.execute(stmt)
            return dict(result.all())
                
    async def get_all_instruction_doc_ids(self) -> List[int]:
        """
        [辅助方法] 获取所有包含指令数据的文档 ID (去重)
        """
        records: List[InstructionDatum] = await self.sql_provider.get_record_by_condition(condition={}, fields=["doc_id"])
        
        # 提取并去重
        doc_ids = set()
        for r in records:
            # 兼容字典或对象访问
            did = r.doc_id
            if did:
                doc_ids.add(int(did))
        
        return list(doc_ids)
    
    async def get_valid_by_doc_id(self, doc_id: int) -> List[InstructionDatum]:
        """
        获取某个doc_id下的所有有效的指令数据
        有效定义: is_valid 为 0 (待审核) 或 1 (有效)
        """
        # 利用 filters 参数传入 SQL 的 IN 查询条件
        return await self.sql_provider.get_record_by_condition(
            condition={"doc_id": doc_id},
            filters=[self.model.is_valid.in_([0, 1])]
        )
    
    async def export_instructions_as_ingest_chunks(
        self, 
        doc_id: Optional[int] = None,
        only_unindexed: bool = False,
        instruction_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        将指令数据导出为待入库的 Chunk 格式
        """
        ingest_chunks = []
        if not doc_id and not instruction_id: raise ValueError("doc_id and isntruction_id must not be null!")
        async with self.sql_provider.get_db_session() as session:
            # 1. 获取文件名
            stmt_doc = select(PdfDocument.file_name).where(PdfDocument.id == doc_id)
            result_doc = await session.execute(stmt_doc)
            file_name = result_doc.scalar() or "Generated_Instruction"

            # 2. 获取该文档下所有【有效】的指令数据
            # 2.1 基础条件：指定文档 + 数据有效
            filters = [or_(InstructionDatum.is_valid != -1, InstructionDatum.is_valid.is_(None))]
            if doc_id: filters.append(InstructionDatum.doc_id == doc_id)
            # 2.2 可选条件：如果 only_unindexed 为 True，则追加过滤条件
            if only_unindexed: filters.append(InstructionDatum.is_indexed == False)
            # 2.3 可选条件
            if instruction_id: filters.append(InstructionDatum.id == instruction_id)
            stmt_inst = select(InstructionDatum).where(*filters)
            result_inst = await session.execute(stmt_inst)
            all_instructions = result_inst.scalars().all()

            if not all_instructions:
                return []

            # 3. 批量获取关联的 DocumentChunk 原始文本
            all_ref_ids = set()
            for inst in all_instructions:
                refs = inst.ref_chunk_ids
                if refs:
                    all_ref_ids.update(refs)
            
            chunk_map = {}
            if all_ref_ids:
                stmt_chunk = select(DocumentChunk.id, DocumentChunk.content).where(
                    DocumentChunk.document_id == doc_id,
                    DocumentChunk.id.in_(all_ref_ids)
                )
                result_chunk = await session.execute(stmt_chunk)
                
                for row in result_chunk:
                    chunk_map[str(row.id)] = row.content

            # 4. 转换为 Ingestion 格式
            for inst in all_instructions:
                # 4.1. 提取基础字段 (直接属性访问)
                datum_id = inst.id
                question = inst.question
                answer = inst.answer
                ref_ids = inst.ref_chunk_ids or []
                q_type = inst.type or "general"

                # 4.2. 构建上下文 (Context)
                context_text = ""
                if ref_ids:
                    # RAG 模式：拼接原始切片内容
                    texts = [chunk_map.get(str(rid), "") for rid in ref_ids if str(rid) in chunk_map]
                    context_text = "\n\n".join(texts)
                else:
                    # 非 RAG 模式：上下文就是答案本身 (或者留空)
                    context_text = answer

                # 4.3. 构建 Metadata
                metadata = {
                    "chunk_id": str(datum_id),       # 使用 InstructionDatum 的 UUID
                    "doc_id": doc_id,                # 关联文档 ID
                    "doc_kb_id": doc_id,             # 兼容之前的 KB 逻辑
                    "filename": file_name,           # 文件名
                    "type": "instruction",           # 标记数据类型
                    "q_type": q_type,                # 指令类型
                    "answer": answer,                # 答案
                    "context": context_text,         # 原始参考资料
                    "ref_chunk_ids": ref_ids,        # 引用列表
                    "is_instruction": True           # 显式标记
                }

                # 3.4. 构造标准切片对象
                item = {
                    "text": question, # 向量化目标是 Question
                    "metadata": metadata
                }
                ingest_chunks.append(item)
        return ingest_chunks

    async def update_indexed_status_batch(self, doc_ids: List[int], is_indexed: bool) -> int:
        """
        批量更新切片的索引状态
        """
        if not doc_ids:
            return 0
            
        async with self.sql_provider.get_db_session() as session:
            stmt = (
                update(self.model)
                .where(self.model.doc_id.in_(doc_ids))
                .values(is_indexed=is_indexed)
            )
            result = await session.execute(stmt)
            return result.rowcount
    
    async def search_paginated(
        self, 
        filter_dto: InstructionDatumFilterDTO, 
        page: int, 
        page_size: int
    ) -> Dict[str, List[InstructionDatum] | int]:
        """分页查询 (支持通过 doc 关联查询 kb_id)"""
        condition = {"doc_id": filter_dto.doc_id}
        if filter_dto.is_valid:
            condition["is_valid"] = filter_dto.is_valid
        if filter_dto.type:
            condition["type"] = filter_dto.type
        
        filters = []
        if filter_dto.keyword:
            filters.append(or_(
                InstructionDatum.system_prompt.like(filter_dto.keyword),
                InstructionDatum.question.like(filter_dto.keyword),
                InstructionDatum.answer.like(filter_dto.keyword),
                InstructionDatum.chain_of_thought.like(filter_dto.keyword)
            ))

        # Return (items, total)
        return await self.sql_provider.get_records_paginated(
            page=page,
            page_size=page_size,
            condition=condition,
            filters=filters,
            order_by=InstructionDatum.create_time.asc()
        )
        

    
    
    