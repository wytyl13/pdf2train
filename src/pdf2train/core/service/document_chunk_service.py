#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/24 13:26
@Author  : weiyutao
@File    : document_chunk_service.py
"""



import logging
import json
from typing import List, Optional, Tuple, AsyncGenerator, Dict, Any
from sqlalchemy import text, select, desc, asc, func, update
import re

from pdf2train.core.provider.sql_provider import SqlProvider
from pdf2train.core.configs.sql_config import SqlConfig
from pdf2train.core.table.document_chunk import DocumentChunk
from pdf2train.core.schema.document_chunk_dto import DocumentChunkCoreDTO, DocumentChunkUpdateDTO, DocumentChunkFilterDTO

class DocumentChunkService:
    def __init__(self, sql_config: Optional[SqlConfig] = None):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.model=DocumentChunk
        self.sql_config = sql_config
        # 内部持有 provider，避免每次调用都重新建立连接
        self.sql_provider = SqlProvider(
            model=self.model,
            sql_config=self.sql_config 
        )

    async def create_batch(self, dtos: List[DocumentChunkCoreDTO]) -> int:
        """Batch insert chunks"""
        data_list = [dto.model_dump() for dto in dtos]
        return await self.sql_provider.batch_create(data_list)

    async def update(self, chunk_id: str, dto: DocumentChunkUpdateDTO) -> bool:
        """Update a specific chunk"""
        data = dto.model_dump(exclude_unset=True)
        return await self.sql_provider.update_record(chunk_id, data)

    async def delete(self, chunk_id: str) -> bool:
        """Delete a single chunk"""
        return await self.sql_provider.delete_record(record_id=chunk_id, hard_delete=True)

    async def delete_by_doc_id(self, doc_id: int) -> int:
        """Delete all chunks for a document"""
        condition = {"document_id": doc_id}
        return await self.sql_provider.delete_records_by_condition(condition)

    async def get_by_id(self, chunk_id: str) -> Optional[DocumentChunk]:
        """Get single chunk instance"""
        results: List[DocumentChunk] = await self.sql_provider.get_record_by_condition({"id": chunk_id})
        return results[0] if results else None

    async def search_paginated(
        self, 
        filter_dto: DocumentChunkFilterDTO, 
        page: int, 
        page_size: int
    ) -> Dict[str, List[DocumentChunk] | int]:
        """
        Get paginated list of DocumentChunk instances
        Returns: (List[DocumentChunk], total_count)
        """
        condition = {"document_id": filter_dto.document_id}
        if filter_dto.id:
            condition["id"] = filter_dto.id
        
        filters = []
        if filter_dto.keyword:
            filters.append(DocumentChunk.content.like(f"%{filter_dto.keyword}%"))

        # Return (items, total)
        return await self.sql_provider.get_records_paginated(
            page=page,
            page_size=page_size,
            condition=condition,
            filters=filters,
            order_by=DocumentChunk.chunk_index.asc()
        )

    async def update_indexed_status_batch(self, doc_ids: List[int], is_indexed: bool) -> int:
        """
        批量更新切片的索引状态
        """
        if not doc_ids:
            return 0
            
        async with self.sql_provider.get_db_session() as session:
            stmt = (
                update(self.model)
                .where(self.model.document_id.in_(doc_ids))
                .values(is_indexed=is_indexed)
            )
            result = await session.execute(stmt)
            return result.rowcount

    async def get_all_by_doc_id(self, doc_id: int) -> List[DocumentChunk]:
        """Get all chunks for export (Non-paginated)"""
        condition = {"document_id": doc_id}
        return await self.sql_provider.get_record_by_condition(
            condition=condition
        )

    async def get_counts_by_doc_ids(self, doc_ids: List[int]) -> Dict[int, int]:
        """
        批量统计文档的 chunks 数量
        """
        if not doc_ids:
            return {}

        # 1. 使用 async with 获取上下文管理的 session
        async with self.sql_provider.get_db_session() as session:
            # 2. 构建查询语句
            stmt = (
                select(self.model.document_id, func.count(self.model.id))
                .where(self.model.document_id.in_(doc_ids))
                .group_by(self.model.document_id)
            )
            
            # 3. 执行查询
            result = await session.execute(stmt)
            
            # 4. 转换结果为字典 {doc_id: count}
            return dict(result.all())
               
    async def get_indexed_counts_by_doc_ids(self, doc_ids: List[int]) -> Dict[int, int]:
        """
        批量统计文档的 chunks 数量 (仅统计已索引/已嵌入 is_indexed=True 的数据)
        """
        if not doc_ids:
            return {}

        async with self.sql_provider.get_db_session() as session:
            stmt = (
                select(self.model.document_id, func.count(self.model.id))
                .where(self.model.document_id.in_(doc_ids))
                .where(self.model.is_indexed.is_(True)) 
                .group_by(self.model.document_id)
            )
            result = await session.execute(stmt)
            return dict(result.all())
                
    async def export_chunks_json(self, doc_id: int) -> List[Dict[str, Any]]:
    
        """Business Logic: Get DB Models -> Convert to plain Dict for JSON export"""
        chunks: List[DocumentChunk] = await self.get_all_by_doc_id(doc_id)
        # Using Pydantic Schema to dump to dict
        return [DocumentChunkCoreDTO.model_validate(c).model_dump() for c in chunks]
    
    async def export_chunks_as_ingest_chunks(
        self, 
        doc_id: Optional[int] = None,
        only_unindexed: bool = False,
        chunk_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        [向量化专用] 将文档原始切片导出为标准入库格式
        用于和 Instruction 数据合并后一同入库
        """
        if not doc_id and not chunk_id:
            raise ValueError("doc_id and chunk_id must not be null!")
        ingest_list = []
        filters = [] 
        # 1. 查询该文档所有切片，document_chunk表格没有is_valid这个字段
        async with self.sql_provider.get_db_session() as session:
            # 1.1 基础条件
            if doc_id:
                filters.append(DocumentChunk.document_id == doc_id)
            # 1.2 动态追加条件：是否只查未索引的
            if only_unindexed:
                filters.append(DocumentChunk.is_indexed == False)
            # 1.3 动态追加条件：指定 chunk_id
            if chunk_id:
                filters.append(DocumentChunk.id == chunk_id)
            # 1.4 组装查询语句 (*filters 解包)
            stmt = select(DocumentChunk).where(*filters).order_by(DocumentChunk.chunk_index.asc())
            result = await session.execute(stmt)
            rows = result.scalars().all()
        
        for row in rows:
            # 2. 准备 Metadata
            # 将数据库里的 meta_info (通常包含 h1, h2 等) 作为基础
            # 注意：根据你的数据库驱动，meta_info 可能是 dict 也可能是 str
            base_meta = row.meta_info if isinstance(row.meta_info, dict) else {}
            
            # 3. 注入关键系统字段
            # 显式标记类型，以便在 Qdrant 里区分这是“原文”还是“指令”
            metadata = {
                **base_meta,             # 展开原有的元数据
                "chunk_id": str(row.id), # 统一 ID 字段名
                "doc_id": row.document_id,
                "doc_kb_id": row.document_id, # 兼容之前的字段
                "filename": base_meta.get("filename", ""), 
                "chunk_index": row.chunk_index,
                "type": "document_chunk", # <--- 核心区分字段：这是原文
                "is_instruction": False
            }

            # 4. 构造标准格式
            # 原文切片的 text 就是 content
            item = {
                "text": row.content, 
                "metadata": metadata
            }
            ingest_list.append(item)
        return ingest_list
          
    def clean_agri_text(self, text: str) -> str:
        """
        针对 pdf2train 导出的 Markdown 进行深度清洗
        """
        if not text:
            return ""

        # 1. 剔除 (cid:123) 这种 PDF 字体映射失败的乱码
        text = re.sub(r'\(cid:\d+\)', '', text)

        # 2. 修复 LaTeX 里的异常空格 (小模型对 $ x _ { i } $ 这种极其细碎的 token 很难受)
        # 尽量将其合并为 $x_{i}$
        text = re.sub(r'(?<=\$)\s+|\s+(?=\$)', '', text) 
        
        # 3. 剔除连续的无意义符号，如 "......" 或 "————"（根据需要保留）
        text = re.sub(r'\.{4,}', '...', text)
        
        # 4. 剔除表格解析失败产生的空行或 nan 字符
        text = re.sub(r'\|\s*nan\s*\|', '| - |', text)
        text = re.sub(r'\bnan\b', '', text)

        # 5. 归一化空白：将多个换行符限制在最多两个，保持段落感但不过度空旷
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        return text.strip()
          
    async def generate_pretrain_stream(self, doc_ids: List[int]) -> AsyncGenerator[str, None]:
        """
        智能预训练数据导出：逻辑完整性优先
        """
        sql_provider = SqlProvider(model=DocumentChunk)
        
        # 设定阈值：比如 3000 字（留出余量给 System Prompt 和特殊符号）
        MAX_SAFE_LENGTH = 3000 
        
        # 按照 chunk_index 排序至关重要，保证文章顺序
        stmt = text("SELECT content FROM document_chunks WHERE document_id = :doc_id ORDER BY chunk_index ASC")
        
        for doc_id in doc_ids:
            async with sql_provider.get_db_session() as session:
                result = await session.execute(stmt, {"doc_id": doc_id})
                rows = result.fetchall()
                if not rows: continue
                
                # Buffer: 当前正在组装的训练样本
                current_buffer_text = ""
                
                for r in rows:
                    raw_chunk = r[0]
                    if not raw_chunk: continue
                    chunk_text = self.clean_agri_text(raw_chunk)
                    if not chunk_text: continue
                    # 1. 预判：如果把这一段加进去，会不会撑爆？
                    if len(current_buffer_text) + len(chunk_text) > MAX_SAFE_LENGTH:
                        # --- 撑爆了，先结算上一批 ---
                        
                        # 只有当 buffer 里有货时才 yield
                        if current_buffer_text.strip():
                            formatted_text = f"<|im_start|>text\n{current_buffer_text}\n<|im_end|>"
                            entry = {
                                "text": formatted_text,
                                "meta": {"doc_id": doc_id, "source": "pdf2train"}
                            }
                            yield json.dumps(entry, ensure_ascii=False) + "\n"
                        
                        # --- 开启新的轮回 ---
                        # 当前这个 chunk 成了下一批的开头
                        current_buffer_text = chunk_text
                        
                    else:
                        # --- 没撑爆，继续往里塞 ---
                        # 加个换行符，保持段落感
                        if current_buffer_text:
                            current_buffer_text += "\n\n" 
                        current_buffer_text += chunk_text
                
                # 2. 循环结束，把最后剩的一点点也发出去
                if current_buffer_text.strip():
                    formatted_text = f"<|im_start|>text\n{current_buffer_text}\n<|im_end|>"
                    entry = {
                        "text": formatted_text,
                        "meta": {"doc_id": doc_id, "source": "pdf2train"}
                    }
                    yield json.dumps(entry, ensure_ascii=False) + "\n"
            
    async def generate_pretrain_stream_bake(self, doc_ids: List[int]) -> AsyncGenerator[str, None]:
        """Stream generator for pretrain data"""
        sql_provider = SqlProvider(model=DocumentChunk)
        stmt = text("SELECT content, meta_info FROM document_chunks WHERE document_id = :doc_id ORDER BY chunk_index ASC")
        for doc_id in doc_ids:
            async with sql_provider.get_db_session() as session:
                result = await session.execute(stmt, {"doc_id": doc_id})
                # Yielding raw strings/jsonl lines
                rows = result.fetchall()
                if not rows: continue
                
                full_text = "\n\n".join([r[0] for r in rows if r[0]])
                # Use metadata from first chunk if available
                meta = rows[0][1] if rows[0][1] else {}
                
                entry = {"text": full_text, "meta": meta}
                yield json.dumps(entry, ensure_ascii=False) + "\n"