#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/29 09:47
@Author  : weiyutao
@File    : instruction_datum_service.py
"""

import logging
from typing import Dict, Any, List, Optional, Union
from sqlalchemy import or_, text, select

from pdf2train.core.table.instruction_datum import InstructionDatum
from pdf2train.core.table.document_chunk import DocumentChunk
from pdf2train.api.service.base.pipeline_task_service import PipelineTaskService
from pdf2train.core.table.pipeline_task import TaskType, TaskLifecycle, InstructionStatus
from pdf2train.api.service.base.update_doc_to_kb_service import UpdateDocToKbService
from pdf2train.api.schema.qdrant_schema import VectorDeleteRequest, IngestRequest
from pdf2train.core.table.pdf_document import PdfDocument
from pdf2train.api.service.base.llm_config_service import LLMConfigService


from pdf2train.core.provider.sql_provider import SqlProvider

class InstructionDatumService:
    """
    指令数据集postgresql服务，包含对qdrant的处理
    """
    def __init__(
        self, 
        pipeline_task_service: PipelineTaskService,
        update_doc_to_kb_service: UpdateDocToKbService,
        llm_config_service: LLMConfigService
    ):
        self.pipeline_task_service = pipeline_task_service
        self.update_doc_to_kb_service = update_doc_to_kb_service
        self.llm_config_service = llm_config_service
        self.logger = logging.getLogger(self.__class__.__name__)

    async def get_processed_h1_titles(self, task_id: int) -> set:
        """
        [断点续传核心] 获取某任务已完成的章节标题列表
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=InstructionDatum)
            
            # 使用 provider 接口查询，只提取 h1_title 字段
            records = await sql_provider.get_record_by_condition(
                condition={"task_id": task_id},
                fields=["h1_title"]
            )
            
            # 在内存中进行去重 (Set)
            titles = set()
            for row in records:
                # 兼容 row 是字典或对象的情况
                val = row.get("h1_title") if isinstance(row, dict) else getattr(row, "h1_title", None)
                if val:
                    titles.add(val)
            
            return titles
            
        except Exception as e:
            self.logger.error(f"查询断点进度失败: {e}")
            return set()
        finally:
            if sql_provider: await sql_provider.close()

    async def batch_save_instructions(self, data_list: List[Dict[str, Any]]) -> int:
        """批量保存 (生成器调用)"""
        if not data_list: return 0
        sql_provider = None
        try:
            clean_data = []
            for item in data_list:
                clean_data.append({
                    "id": item.get("id"),
                    "doc_id": item["doc_id"],
                    "task_id": item["task_id"],
                    "type": item.get("type", "原理机制"),
                    "h1_title": item.get("h1_title"),
                    "system_prompt": item.get("system_prompt"),
                    "question": item["question"],
                    "answer": item["answer"],
                    "chain_of_thought": item.get("chain_of_thought"),
                    "ref_chunk_ids": item.get("ref_chunk_ids", {}),
                    "chunk_index_description": item.get("chunk_index_description", []),
                    "meta_info": item.get("meta_info", {}),
                    "is_indexed": False,
                    # 默认设为 0 (待审核)
                    "is_valid": 0 
                })
            
            sql_provider = SqlProvider(model=InstructionDatum)
            count = await sql_provider.batch_create(clean_data)
            return count
        except Exception as e:
            self.logger.error(f"批量保存异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def _get_all_instruction_doc_ids(self) -> List[int]:
        """
        [辅助方法] 获取所有包含指令数据的文档 ID (去重)
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=InstructionDatum)
            # 只查询 doc_id 字段，减少传输量
            # 注意：SqlProvider 的具体去重写法可能不同，这里使用 Python 集合去重
            records = await sql_provider.get_record_by_condition(condition={}, fields=["doc_id"])
            
            # 提取并去重
            doc_ids = set()
            for r in records:
                # 兼容字典或对象访问
                did = r.get("doc_id") if isinstance(r, dict) else getattr(r, "doc_id", None)
                if did:
                    doc_ids.add(int(did))
            
            return list(doc_ids)
        except Exception as e:
            self.logger.error(f"获取文档ID列表失败: {e}")
            return []
        finally:
            if sql_provider: await sql_provider.close()

    async def _export_single_doc(self, doc_id: int) -> List[Dict[str, Any]]:
        """
        [内部方法] 导出单个文档的数据
        (这是你原本 export_for_finetuning 的核心逻辑，几乎未改动，只是封装了一下)
        """
        sql_provider_inst = None
        sql_provider_chunk = None
        try:
            # === Step 1: 获取指令数据 ===
            sql_provider_inst = SqlProvider(model=InstructionDatum)
            all_instructions = await sql_provider_inst.get_record_by_condition(
                condition={"doc_id": doc_id}
            )
            
            # === Step 2: 获取切片原文 (构建缓存) ===
            sql_provider_chunk = SqlProvider(model=DocumentChunk)
            all_chunks = await sql_provider_chunk.get_record_by_condition(
                condition={"document_id": doc_id}, 
                fields=["id", "content"] 
            )
            
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
        finally:
            # 确保每次循环都正确关闭连接
            if sql_provider_inst: await sql_provider_inst.close()
            if sql_provider_chunk: await sql_provider_chunk.close()

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
                target_ids = await self._get_doc_ids_by_kb_ids(kb_ids_list)
            else:    
                # 1. 获取所有有数据的 doc_id
                target_ids = await self._get_all_instruction_doc_ids()
                
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
        sql_provider_inst = None
        sql_provider_chunk = None
        try:
            # 0 获取文件名
            sql_provider_pdf_document = SqlProvider(model=PdfDocument)
            docs_data = await sql_provider_pdf_document.get_record_by_condition(condition={"id": doc_id})
            file_name = "Generated_Instruction" if not docs_data else docs_data[0].get("file_name")


            # 1: 获取该文档下所有【有效】的指令数据
            sql_provider_inst = SqlProvider(model=InstructionDatum)
            stmt = text("""
                SELECT * FROM instruction_datum 
                WHERE doc_id = :doc_id 
                  AND (is_valid != -1 OR is_valid IS NULL)
            """)
            async with sql_provider_inst.get_db_session() as session:
                result = await session.execute(stmt, {"doc_id": doc_id})
                all_instructions = result.fetchall()
                
            if not all_instructions:
                return []

            # 2: 批量获取关联的 DocumentChunk 原始文本
            all_ref_ids = set()
            for inst in all_instructions:
                # 兼容对象/字典读取
                refs = inst.get("ref_chunk_ids") if isinstance(inst, dict) else getattr(inst, "ref_chunk_ids", [])
                if refs:
                    all_ref_ids.update(refs)
            
            chunk_map = {}
            if all_ref_ids:
                sql_provider_chunk = SqlProvider(model=DocumentChunk)
                all_doc_chunks = await sql_provider_chunk.get_record_by_condition(
                    condition={"document_id": doc_id},
                    fields=["id", "content"]
                )
                for c in all_doc_chunks:
                    cid = c.get("id") if isinstance(c, dict) else getattr(c, "id")
                    ctxt = c.get("content") if isinstance(c, dict) else getattr(c, "content")
                    chunk_map[str(cid)] = ctxt

            # 3. 转换为 Ingestion格式
            ingest_chunks = []
            
            for row in all_instructions:
                # 3.1. 提取基础字段
                if isinstance(row, dict):
                    datum_id = row["id"]
                    question = row["question"]
                    answer = row["answer"]
                    ref_ids = row.get("ref_chunk_ids", [])
                    q_type = row.get("type", "general")
                else:
                    datum_id = getattr(row, "id")
                    question = getattr(row, "question")
                    answer = getattr(row, "answer")
                    ref_ids = getattr(row, "ref_chunk_ids", [])
                    q_type = getattr(row, "type", "general")

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
        finally:
            if sql_provider_inst: await sql_provider_inst.close()
            if sql_provider_chunk: await sql_provider_chunk.close()

    async def update_instruction(
        self, 
        datum_id: str, 
        system_prompt: Optional[str] = None,
        question: Optional[str] = None, 
        answer: Optional[str] = None,
        is_valid: Optional[int] = None,
        chain_of_thought: Optional[str] = None,
        ref_chunk_ids: Optional[List[str]] = None
    ) -> bool:
        """
        [主入口] 更新指令信息 (Sync Merge Pattern)
        逻辑：
        1. 查：获取当前数据库记录
        2. 改：更新数据库字段
        3. 合：合并旧数据与新数据，得到 Final State
        4. 同步：
           - 如果变为无效 -> 删向量
           - 如果有效 -> 实时生成新向量并 Upsert
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=InstructionDatum)
            
            # --- 1. 查询当前状态 ---
            current_record = await sql_provider.get_record_by_id(record_id=datum_id)
            if not current_record:
                self.logger.warning(f"更新失败: 找不到指令 {datum_id}")
                return False

            # --- 2. 准备更新数据 ---
            db_update_data = {}
            if question is not None: db_update_data["question"] = question
            if answer is not None: db_update_data["answer"] = answer
            if system_prompt is not None: db_update_data["system_prompt"] = system_prompt
            if chain_of_thought is not None: db_update_data["chain_of_thought"] = chain_of_thought
            if ref_chunk_ids is not None: db_update_data["ref_chunk_ids"] = ref_chunk_ids
            if is_valid is not None: db_update_data["is_valid"] = is_valid

            if is_valid is not None:
                db_update_data["is_valid"] = is_valid
            
            if not db_update_data:
                return False

            # --- 3. 执行数据库更新 ---
            await sql_provider.update_record(record_id=datum_id, data=db_update_data)
            
            # --- 4. 准备同步用的"最终态数据" ---
            final_data = {**current_record, **db_update_data}

            # --- 5. 检查并执行同步逻辑**********（一般不在这里同步，因为同步有可能执行效率较低，影响数据更新效率）************** ---
                # 一般会将更新数据的逻辑进行单独处理，更新向量数据库的服务单独处理，然后创建一个服务去调用这两个服务
                # 所以这里的代码结构不是特别规范，后续继续解耦优化
            final_indexed_status = final_data.get("is_indexed", False)
            final_valid_status = final_data.get("is_valid", 0)
            
            if final_valid_status == -1:
                # 情况 A: 数据被标记为无效 (-1:废弃)
                await self._delete_instruction_vector(final_data)
            else:
                if final_indexed_status is True:
                    # 情况 B: 有效且需要索引 (is_indexed=True)
                    await self._sync_single_instruction_to_kb(final_data)
                else:
                    # 如果开关是关的 (False)，确保向量库里没有这条数据
                    await self._delete_instruction_vector(final_data)
            return True

        except Exception as e:
            import traceback
            self.logger.error(f"更新指令流程异常: {str(e)} \n {traceback.format_exc()}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def _sync_single_instruction_to_kb(self, datum_data: Dict[str, Any]):
        """
        [私有辅助函数] 将单条指令数据同步到向量库 (Embedding + Upsert)
        """
        sql_provider_chunk = None
        try:
            doc_id = datum_data.get("doc_id")
            datum_id = str(datum_data.get("id"))
            
            # 1. 获取 Embedding 配置
            embedding_config_override = await self.llm_config_service.get_embedding_config_override(doc_id=doc_id)
            
            # 2. 构建 Context (引用文本)
            ref_ids = datum_data.get("ref_chunk_ids", [])
            context_text = ""
            
            if ref_ids:
                sql_provider_chunk = SqlProvider(model=DocumentChunk)
                stmt = (
                    select(DocumentChunk.id, DocumentChunk.content)
                    .where(DocumentChunk.id.in_(ref_ids))
                )
                if len(ref_ids) > 0:
                     async with sql_provider_chunk.get_db_session() as session:
                        result = await session.execute(stmt, {"ids": tuple(ref_ids)})
                        rows = result.fetchall()
                        # 拼接内容
                        texts = [row[1] for row in rows if row[1]]
                        context_text = "\n\n".join(texts)

            # 3. 构造 Vector Metadata (复用 export_instructions_as_ingest_chunks 的逻辑)
            base_meta = datum_data.get("meta_info", {})
            if not isinstance(base_meta, dict): base_meta = {}

            vector_metadata = {
                **base_meta,
                "chunk_id": datum_id,
                "doc_id": doc_id,
                "doc_kb_id": doc_id,
                "filename": base_meta.get("filename", "instruction_manual"),
                "type": "instruction",       # 核心标记
                "q_type": datum_data.get("type", "general"),
                "answer": datum_data.get("answer", ""),
                "context": context_text,
                "is_instruction": True
            }

            # 4. 构造 Payload (注意：Instruction 向量化的是 Question)
            payload = {
                "text": datum_data.get("question"), 
                "metadata": vector_metadata
            }
            
            # 5. 调用通用入库接口
            ingest_request = IngestRequest(
                chunks=[payload],
                embed_config=embedding_config_override
            )
            
            self.logger.info(f"🔄 同步指令到向量库: ID={datum_id}, Doc={doc_id}")
            await self.update_doc_to_kb_service.call_vector_api(ingest_request=ingest_request)

        except Exception as e:
            self.logger.error(f"❌ 指令向量同步失败 (ID {datum_data.get('id')}): {str(e)}")
        finally:
            if sql_provider_chunk: await sql_provider_chunk.close()

    async def _delete_instruction_vector(self, datum_data: Dict[str, Any]):
        """
        [私有辅助函数] 从向量库中物理删除指令
        """
        try:
            doc_id = datum_data.get("doc_id")
            datum_id = str(datum_data.get("id"))
            
            collection_name = await self.update_doc_to_kb_service.get_collection_name_by_doc_id(doc_id)
            if not collection_name: return

            self.logger.info(f"🗑️ 指令 {datum_id} 无效，正在清理向量...")
            
            await self.update_doc_to_kb_service.delete_vector(
                vector_delete_request=VectorDeleteRequest(
                    collection_name=collection_name,
                    filters={
                        "chunk_id": datum_id,
                        "type": "instruction" # 双重保险
                    }
                )
            )
        except Exception as e:
            self.logger.error(f"向量删除失败: {e}")

    async def update_instruction_bake(
        self, 
        datum_id: str, 
        system_prompt: Optional[str] = None,
        question: Optional[str] = None, 
        answer: Optional[str] = None,
        is_valid: Optional[int] = None,
        chain_of_thought: Optional[str] = None,
        ref_chunk_ids: Optional[List[str]] = None
    ) -> bool:
        """
        [人工审核接口]
        更新内容或状态
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=InstructionDatum)
            
            data = {}
            if question is not None: 
                data["question"] = question
                data["is_indexed"] = False # 内容变了，需要重新向量化
            if answer is not None: 
                data["answer"] = answer
                data["is_indexed"] = False
            if system_prompt is not None: 
                data["system_prompt"] = system_prompt
                data["is_indexed"] = False
            
            if chain_of_thought is not None:
                data["chain_of_thought"] = chain_of_thought
                data["is_indexed"] = False 

            if ref_chunk_ids is not None:
                data["ref_chunk_ids"] = ref_chunk_ids
                data["is_indexed"] = False
            
            
            if is_valid is not None:
                data["is_valid"] = is_valid
                # 如果用户把一个废弃数据(-1)改回了有效(1)，可以不重置 is_indexed，除非内容也变了
                if is_valid == 1:
                    # 简单策略：只要人工审核过（改为有效），就强制同步一次，确保向量库是最新的
                    data["is_indexed"] = False

            if not data: return False

            result = await sql_provider.update_record(record_id=datum_id, data=data)
            return result
        except Exception as e:
            self.logger.error(f"更新异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def get_doc_id_by_datum_id(self, datum_id: str) -> Optional[int]:
        """
        [辅助方法] 根据指令 ID 快速反查 Document ID
        用于获取 Collection Name 进行向量操作
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=InstructionDatum)
            # fields 只需要查 doc_id
            result = await sql_provider.get_record_by_condition(
                condition={"id": datum_id},
                fields=["doc_id"]
            )
            if result:
                # 兼容字典或对象返回
                item = result[0]
                return item.get("doc_id") if isinstance(item, dict) else item.doc_id
            return None
        except Exception as e:
            self.logger.error(f"查询 Instruction Doc ID 失败: {str(e)}")
            return None
        finally:
            if sql_provider: await sql_provider.close()

    async def get_instruction_list(self, doc_id: int, type: int, is_valid, page: int = 1, page_size: int = 20, keyword: Optional[str] = None):
        """查询列表 (包含 is_valid 状态，方便前端展示状态颜色)"""
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=InstructionDatum)
            condition = {"doc_id": doc_id}
            if type:
                condition["type"] = type
            if is_valid is not None:
                condition["is_valid"] = is_valid
            filters = []
            if keyword:
                filters.append(or_(
                    InstructionDatum.question.like(f"%{keyword}%"),
                    InstructionDatum.answer.like(f"%{keyword}%")
                ))
            fields = [
                "id", 
                "h1_title", 
                "type",
                "system_prompt",
                "question", 
                "answer", 
                "chain_of_thought", 
                "meta_info",
                "ref_chunk_ids",
                "chunk_index_description",
                "is_valid", 
                "is_indexed",
                "create_time"
            ]
            
            result = await sql_provider.get_records_paginated(
                page=page, page_size=page_size, condition=condition, filters=filters, fields=fields, order_by=InstructionDatum.id.asc()
            )
            return result
        except Exception as e:
            self.logger.error(f"查询列表异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()
    
    async def delete_instruction(self, datum_id: str) -> bool:
        """
        [物理删除] 删除单条指令
        慎用：删除后无法找回
        """
        sql_provider = None
        try:
            
            sql_provider = SqlProvider(model=InstructionDatum)
            
            datum = await sql_provider.get_record_by_condition({"id": datum_id})
            if not datum:
                self.logger.warning(f"指令 {datum_id} 不存在，跳过删除")
                return False
            
            # 兼容字典和对象访问
            doc_id = datum[0].get("doc_id")
            collection_name = await self.update_doc_to_kb_service.get_collection_name_by_doc_id(doc_id=doc_id)

            # hard_delete=True 表示执行 DELETE FROM ...
            result = await sql_provider.delete_record(record_id=datum_id, hard_delete=True)
            
            if result:
                self.logger.info(f"Instruction {datum_id} 已物理删除")
            
            if not collection_name:
                self.logger.error(f"无法获取 doc_id={doc_id} 的 Collection Name，跳过向量删除")
            else:
                try:
                    # 物理删除指令数据嵌入qdrant数据    
                    await self.update_doc_to_kb_service.delete_vector(
                        vector_delete_request=VectorDeleteRequest(
                            collection_name=collection_name,
                            filter_key="chunk_id",
                            filter_value=datum_id
                        )
                    )
                except Exception as ve:
                    self.logger.error(f"SQL已删，但向量删除失败: {ve}")
            return result
        except Exception as e:
            self.logger.error(f"删除指令异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def _get_doc_ids_by_kb_ids(self, kb_ids: List[int]) -> List[int]:
        """查询指定知识库下的所有文档 ID"""
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PdfDocument)
            
            # 使用 IN 查询
            stmt = select(PdfDocument.id).where(PdfDocument.kb_id.in_(kb_ids))
            
            async with sql_provider.get_db_session() as session:
                result = await session.execute(stmt)
                rows = result.fetchall()
            
            return [row[0] for row in rows]
        except Exception as e:
            self.logger.error(f"根据KB查询文档失败: {e}")
            return []
        finally:
            if sql_provider: await sql_provider.close()

    async def delete_by_doc_id(self, doc_id: int) -> int:
        """
        [级联删除] 删除该文档下的所有指令数据
        通常用于用户删除文档时，顺便清理关联生成的微调数据
        """
        sql_provider = None
        try:
            # 在删除前获取collection_name
            collection_name = await self.update_doc_to_kb_service.get_collection_name_by_doc_id(doc_id=doc_id)
                
                
            tasks = await self.pipeline_task_service.get_tasks_by_doc_id(doc_id)
            extract_task = next((t for t in tasks if t['task_type'] == TaskType.INSTRUCTION_GEN.value), None)
            if not extract_task: 
                return 0
            task_id = extract_task['id']
            sql_provider = SqlProvider(model=InstructionDatum)
            
            condition = {"doc_id": doc_id}
            count = await sql_provider.delete_records_by_condition(condition)
            await self.pipeline_task_service.update_task_status(
                task_id=task_id,
                status=TaskLifecycle.PENDING.value,
                detailed_status=InstructionStatus.PENDING.value,
                progress=InstructionStatus.PENDING.value
            )
            self.logger.info(f"已级联清理 Doc {doc_id} 的 {count} 条指令数据")
            
            if not collection_name:
                self.logger.error(f"无法获取 doc_id={doc_id} 的 Collection Name，跳过向量删除")
            else:
                try:
                    # 物理删除指令数据嵌入qdrant数据    
                    await self.update_doc_to_kb_service.delete_vector(
                        vector_delete_request=VectorDeleteRequest(
                            collection_name=collection_name,
                            filters={
                                "doc_kb_id": doc_id,
                                "type": "instruction"
                            }
                        )
                    )
                except Exception as ve:
                    self.logger.error(f"SQL已删，但向量删除失败: {ve}")
            return count
        except Exception as e:
            self.logger.error(f"级联删除指令异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()
    