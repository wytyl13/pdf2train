#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/29 09:47
@Author  : weiyutao
@File    : instruction_datum_service.py
"""

import logging
import json
from typing import Dict, Any, List, Optional
from sqlalchemy import text, or_

from api.table.base.instruction_datum import InstructionDatum
from api.table.base.document_chunk import DocumentChunk
from api.service.pipeline_task_service import PipelineTaskService
from api.table.base.pipeline_task import TaskType, TaskLifecycle, InstructionStatus

from agent.provider.sql_provider import SqlProvider

class InstructionDatumService:
    def __init__(self, sql_config_path: str, pipeline_task_service: PipelineTaskService):
        self.sql_config_path = sql_config_path
        self.pipeline_task_service = pipeline_task_service
        self.logger = logging.getLogger(self.__class__.__name__)

    async def get_processed_h1_titles(self, task_id: int) -> set:
        """
        [断点续传核心] 获取某任务已完成的章节标题列表
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=InstructionDatum, sql_config_path=self.sql_config_path)
            
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
            
            sql_provider = SqlProvider(model=InstructionDatum, sql_config_path=self.sql_config_path)
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
            sql_provider = SqlProvider(model=InstructionDatum, sql_config_path=self.sql_config_path)
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
            sql_provider_inst = SqlProvider(model=InstructionDatum, sql_config_path=self.sql_config_path)
            all_instructions = await sql_provider_inst.get_record_by_condition(
                condition={"doc_id": doc_id}
            )
            
            # === Step 2: 获取切片原文 (构建缓存) ===
            sql_provider_chunk = SqlProvider(model=DocumentChunk, sql_config_path=self.sql_config_path)
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

    async def export_for_finetuning(self, doc_id: Optional[int] = None) -> List[Dict[str, Any]]:
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
            # === 模式 B: 导出所有 ===
            self.logger.info("开始导出所有文档数据...")
            
            # 1. 获取所有有数据的 doc_id
            target_ids = await self._get_all_instruction_doc_ids()
            self.logger.info(f"发现 {len(target_ids)} 个包含指令数据的文档")
            
            # 2. 循环处理
            # [Diagram of processing flow]
            # Start -> Get IDs -> Loop [Fetch Doc -> Fetch Chunk -> Assemble] -> Aggregate -> End
            for idx, did in enumerate(target_ids):
                # 打印进度日志
                if idx % 10 == 0:
                    self.logger.info(f"导出进度: {idx}/{len(target_ids)}")
                
                doc_data = await self._export_single_doc(did)
                all_results.extend(doc_data)
                
            self.logger.info(f"全量导出完成，共生成 {len(all_results)} 条微调数据")
            return all_results

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
        [人工审核接口]
        更新内容或状态
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=InstructionDatum, sql_config_path=self.sql_config_path)
            
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

    async def get_instruction_list(self, doc_id: int, type: int, is_valid, page: int = 1, page_size: int = 20, keyword: Optional[str] = None):
        """查询列表 (包含 is_valid 状态，方便前端展示状态颜色)"""
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=InstructionDatum, sql_config_path=self.sql_config_path)
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
            sql_provider = SqlProvider(model=InstructionDatum, sql_config_path=self.sql_config_path)
            
            # hard_delete=True 表示执行 DELETE FROM ...
            result = await sql_provider.delete_record(record_id=datum_id, hard_delete=True)
            
            if result:
                self.logger.info(f"Instruction {datum_id} 已物理删除")
            return result
        except Exception as e:
            self.logger.error(f"删除指令异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def delete_by_doc_id(self, doc_id: int) -> int:
        """
        [级联删除] 删除该文档下的所有指令数据
        通常用于用户删除文档时，顺便清理关联生成的微调数据
        """
        sql_provider = None
        try:
            tasks = await self.pipeline_task_service.get_tasks_by_doc_id(doc_id)
            extract_task = next((t for t in tasks if t['task_type'] == TaskType.INSTRUCTION_GEN.value), None)
            if not extract_task: 
                return 0
            task_id = extract_task['id']
            sql_provider = SqlProvider(model=InstructionDatum, sql_config_path=self.sql_config_path)
            
            condition = {"doc_id": doc_id}
            count = await sql_provider.delete_records_by_condition(condition)
            await self.pipeline_task_service.update_task_status(
                task_id=task_id,
                status=TaskLifecycle.PENDING.value,
                detailed_status=InstructionStatus.PENDING.value,
                progress=InstructionStatus.PENDING.value
            )
            self.logger.info(f"已级联清理 Doc {doc_id} 的 {count} 条指令数据")
            return count
        except Exception as e:
            self.logger.error(f"级联删除指令异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()
    