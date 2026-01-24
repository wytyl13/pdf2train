#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/23 11:48
@Author  : weiyutao
@File    : pipeline_task_service.py
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import select, func, desc, case, update

# 导入模型和枚举
from pdf2train.core.provider.sql_provider import SqlProvider

from pdf2train.core.table.pipeline_task import PipelineTask, TaskType, TaskLifecycle
from pdf2train.core.table.pdf_document import PdfDocument, DocStatus
from pdf2train.core.configs.sql_config import SqlConfig
from pdf2train.core.schema.pipeline_task_dto import PipelineTaskCoreDTO, PipelineTaskUpdateDTO

class PipelineTaskService:
    """任务流水线业务服务"""
    
    def __init__(
        self, 
        sql_config: Optional[SqlConfig] = None
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.model = PipelineTask
        self.sql_provider = SqlProvider(
            model=PipelineTask, 
            sql_config=sql_config 
        )
    
    async def init_tasks_for_document(self, doc_id: int) -> bool:
        """
        [业务编排] 为新文档初始化默认流水线
        定义了步骤的顺序、名称和初始状态
        """
        # 1. 定义标准流程 (Recipe)
        # 这里是修改流程步骤的唯一入口
        default_flow = [
            {"type": TaskType.MINERU_EXTRACT, "name": "PDF文档解析"},
            {"type": TaskType.MARKDOWN_CHUNK, "name": "智能切片处理"},
            {"type": TaskType.INSTRUCTION_GEN, "name": "QA指令生成"},
            {"type": TaskType.QDRANT_INDEX, "name": "向量知识库索引"}
        ]

        # 2. 构建 DTO 列表
        dtos = []
        for index, step in enumerate(default_flow):
            # 业务规则：第一个任务 Pending (准备执行)，后续任务 Waiting (等待前置)
            status = TaskLifecycle.PENDING.value if index == 0 else TaskLifecycle.WAITING_PARENT.value
            
            dto = PipelineTaskCoreDTO(
                doc_id=doc_id,
                task_type=step["type"].value,
                step_order=index + 1,
                task_name=step["name"],
                status=status,
                detailed_status=0,
                progress=0
            )
            dtos.append(dto)

        # 3. 调用 Service 执行批量插入
        return await self.create_batch(dtos)
    
    async def activate_next_step(self, doc_id: int, current_step_order: int) -> bool:
        """
        链式激活：当前步骤完成后，将下一个步骤从 WAITING_PARENT (-2) 变更为 PENDING (0)
        """
        try:
            # 1. 查找下一个任务
            next_order = current_step_order + 1
            tasks: List[PipelineTask] = await self.sql_provider.get_record_by_condition({
                "doc_id": doc_id,
                "step_order": next_order
            })
            if not tasks:
                self.logger.info(f"没有找到步骤 {next_order}，流程结束。")
                return False
            next_task = tasks[0]
            # 2. 只有当下一个任务处于"等待前置"状态时，才激活它
            # 防止重复激活，或者误操作把已经 Failed 的任务重置了
            dto = PipelineTaskUpdateDTO(
                status=TaskLifecycle.PENDING.value,
                end_time=datetime.now()
            )
            
            if next_task.status == TaskLifecycle.WAITING_PARENT.value:
                result = await self.update(task_id=next_task.id, dto=dto)
                if not result:
                    self.logger.error(f"激活下一步{next_order} (Task ID: {next_task.get('id')}) 失败: {e}")
                    return False
                self.logger.info(f"已自动激活步骤 {next_order} (Task ID: {next_task.get('id')})")
                return True
            return False
        except Exception as e:
            self.logger.error(f"激活下一步失败: {e}")
            return False
    
    async def reset_processing_tasks_to_failed(self) -> List[int]:
        """
        [系统级兜底] 重置所有异常中断的任务
        逻辑：
        1. 找出所有状态为 RUNNING (10) 的任务涉及的 doc_id
        2. 批量将这些任务更新为 FAILED (-1)，并备注"因程序中断导致运行中断"
        """
        try:
            affected_doc_ids = []
            async with self.sql_provider.get_db_session() as session:
                # 1. 找出受影响的文档 ID
                stmt_select = (
                    select(PipelineTask.doc_id)
                    .where(PipelineTask.status == TaskLifecycle.RUNNING.value)
                    .distinct()
                )
                result = await session.execute(stmt_select)
                affected_doc_ids = result.scalars().all()
                
                if not affected_doc_ids:
                    self.logger.info("✅ 没有发现异常挂起(Running)的任务，无需重置。")
                    return affected_doc_ids

                # 2. 批量更新任务状态
                stmt_update = (
                    update(PipelineTask)
                    .where(PipelineTask.status == TaskLifecycle.RUNNING.value)
                    .values(
                        status=TaskLifecycle.FAILED.value,
                        error_message="因程序中断导致运行中断",
                        end_time=datetime.now(),
                        detailed_status=-1 
                    )
                )
                await session.execute(stmt_update)
                self.logger.warning(f"⚠️ 检测到非正常中断，已重置 {len(affected_doc_ids)} 个文档关联的任务状态。")
                return affected_doc_ids
        except Exception as e:
            import traceback
            self.logger.error(f"重置挂起任务失败: {e} \n {traceback.format_exc()}")
            raise e
        
    async def create(self, dto: PipelineTaskCoreDTO) -> int:
        """创建单个任务"""
        try:
            data = dto.model_dump(exclude_unset=True)
            res = await self.sql_provider.add_record(data)
            return res
        except Exception as e:
            self.logger.error(f"创建任务失败: {e}")
            raise e
        
    async def create_batch(self, dtos: List[PipelineTaskCoreDTO]) -> bool:
        """批量创建任务 (用于初始化文档流程)"""
        try:
            data_list = [dto.model_dump(exclude_unset=True) for dto in dtos]
            await self.sql_provider.bulk_insert_with_update(data_list)
            return True
        except Exception as e:
            self.logger.error(f"批量创建任务失败: {e}")
            raise e
        
    async def update(self, task_id: int, dto: PipelineTaskUpdateDTO) -> bool:
        """更新任务状态"""
        try:
            update_data = dto.model_dump(exclude_unset=True)
            await self.sql_provider.update_record(task_id, update_data)
            return True
        except Exception as e:
            self.logger.error(f"更新任务失败 TaskID {task_id}: {e}")
            raise e
        
    async def get_by_doc_id(self, doc_id: int) -> List[PipelineTask]:
        """获取文档的所有任务"""
        try:
            tasks = await self.sql_provider.get_record_by_condition({"doc_id": doc_id})
            # 按 step_order 排序
            tasks.sort(key=lambda x: x.step_order)
            return tasks
        except Exception as e:
            self.logger.error(f"查询文档任务失败 DocID {doc_id}: {e}")
            return []
        
        """根据ID获取任务"""
    
    async def get_by_id(self, task_id: int) -> Optional[PipelineTask]:
        try:
            res = await self.sql_provider.get_record_by_condition({"id": task_id})
            return res[0] if res else None
        except Exception as e:
            self.logger.error(f"查询任务失败 TaskID {task_id}: {e}")
            return None
        
    async def get_stats_group_by_type_and_status(self) -> List[tuple]:
        """
        [原子能力] 获取任务状态统计
        返回: [(task_type, status, count), ...]
        """
        async with self.sql_provider.get_db_session() as session:
            stmt = select(
                PipelineTask.task_type, 
                PipelineTask.status, 
                func.count(PipelineTask.id)
            ).group_by(PipelineTask.task_type, PipelineTask.status)
            res = await session.execute(stmt)
            return res.all()
        
    async def get_status_by_doc_ids(self, doc_ids: List[int]) -> List[Dict]:
        """
        [原子能力] 批量获取指定文档的任务状态 (避免 N+1 查询)
        """
        if not doc_ids: return []
        
        async with self.sql_provider.get_db_session() as session:
            # 只查我们需要的最轻量字段
            stmt = select(PipelineTask.doc_id, PipelineTask.task_type, PipelineTask.status)\
                .where(PipelineTask.doc_id.in_(doc_ids))
            res = await session.execute(stmt)
            # 返回 raw data 或者 dict 均可
            return [{"doc_id": r[0], "task_type": r[1], "status": r[2]} for r in res.all()]
        
    