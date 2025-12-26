#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/23 11:48
@Author  : weiyutao
@File    : pipeline_task_service.py
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

# 导入模型和枚举
from api.table.base.pipeline_task import PipelineTask, TaskType, TaskLifecycle
from agent.provider.sql_provider import SqlProvider
from api.table.base.pdf_document import PdfDocument, DocStatus
from api.service.minio_service import MinioService

class PipelineTaskService:
    """任务流水线业务服务"""
    
    def __init__(self, sql_config_path: str):
        self.sql_config_path = sql_config_path
        self.logger = logging.getLogger(self.__class__.__name__)
        
    async def init_tasks_for_document(self, doc_id: int) -> bool:
        """
        [核心] 为新上传的文档初始化默认流水线任务 (4个步骤)
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PipelineTask, sql_config_path=self.sql_config_path)
            
            # 定义默认流程 (Single Source of Truth)
            default_flow = [
                {"type": TaskType.MINERU_EXTRACT, "name": "PDF解析"},
                {"type": TaskType.MARKDOWN_CHUNK, "name": "智能切片"},
                {"type": TaskType.INSTRUCTION_GEN, "name": "指令生成"},
                {"type": TaskType.QDRANT_INDEX, "name": "知识库索引"}
            ]
            
            tasks = []
            for index, step in enumerate(default_flow):
                status_ = TaskLifecycle.PENDING.value if index == 0 else TaskLifecycle.WAITING_PARENT.value
                task = {
                    "doc_id": doc_id,
                    "task_type": step["type"].value,
                    "step_order": index + 1,
                    "task_name": step["name"],
                    "status": status_,
                    "detailed_status": 0
                }
                tasks.append(task)
            
            # 批量插入
            # 注意：bulk_insert_with_update 是你 provider 里的方法
            # 如果没有 bulk 方法，可以用循环 add_record
            await sql_provider.bulk_insert_with_update(tasks)
            return True
            
        except Exception as e:
            self.logger.error(f"初始化流水线失败 DocID: {doc_id}, Error: {e}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def get_tasks_by_doc_id(self, doc_id: int) -> List[Dict[str, Any]]:
        """获取某文档的所有任务"""
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PipelineTask, sql_config_path=self.sql_config_path)
            # 按步骤顺序返回
            tasks = await sql_provider.get_record_by_condition({"doc_id": doc_id})
            # 手动排序（如果数据库没排好）
            tasks.sort(key=lambda x: x.get('step_order', 0))
            return tasks
        except Exception as e:
            self.logger.error(f"查询任务失败: {e}")
            raise e
        finally:
             if sql_provider: await sql_provider.close()

    async def update_task_status(
        self, 
        task_id: int, 
        status: int,  # TaskLifecycle (Level 1)
        detailed_status: Optional[int] = None, # (Level 2)
        progress: Optional[int] = None,
        result_data: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """
        [核心] 更新任务状态，并自动触发父文档的状态刷新
        """
        task_provider = None
        try:
            task_provider = SqlProvider(model=PipelineTask, sql_config_path=self.sql_config_path)
            
            # 1. 获取当前任务
            tasks = await task_provider.get_record_by_condition({"id": task_id})
            if not tasks:
                raise ValueError(f"Task ID {task_id} 不存在")
            task = tasks[0]
            doc_id = task.get("doc_id") # 记下来，一会要用

            # 2. 准备更新数据
            update_data = {
                "status": status,
                "update_time": datetime.now()
            }
            
            # 2.1 状态流转处理
            if detailed_status is not None:
                update_data["detailed_status"] = detailed_status

            if result_data is not None:
                # 注意：如果是增量更新，这里可能需要先读取旧 data 再 merge，这里简化为覆盖
                update_data["result_data"] = result_data
            
            if error_message is not None:
                update_data["error_message"] = error_message
            elif status in [TaskLifecycle.SUCCESS.value, TaskLifecycle.RUNNING.value]:
                update_data["error_message"] = None
            
            
            if progress is not None:
                # 限制范围 0-100
                update_data["progress"] = max(0, min(100, progress))

            # 2.2 时间与耗时计算
            # 如果是刚开始跑
            if status == TaskLifecycle.RUNNING.value and task.get("status") != TaskLifecycle.RUNNING.value:
                update_data["start_time"] = datetime.now()
            
            # 如果是刚结束 (成功或失败)
            if status in [TaskLifecycle.SUCCESS.value, TaskLifecycle.FAILED.value]:
                end_time = datetime.now()
                update_data["end_time"] = end_time
                # 计算耗时
                start_time = task.get("start_time")
                # 如果数据库里读出来是 str，可能需要转 datetime，视 driver 而定
                # 这里假设 SqlProvider 已经处理好类型，或者 start_time 就在内存里
                if start_time:
                    # 确保 start_time 是 datetime 对象
                    if isinstance(start_time, str):
                        start_time = datetime.fromisoformat(str(start_time)) 
                    cost = int((end_time - start_time).total_seconds() * 1000)
                    update_data["cost_ms"] = cost

            # 3. 执行任务表更新
            await task_provider.update_record(task_id, update_data)
            
            # 4. [关键] 触发父_refresh_parent_doc_status文档状态刷新 (Event Driven)
            await self._refresh_parent_doc_status(doc_id)
            
            return True

        except Exception as e:
            self.logger.error(f"更新任务状态失败: {e}")
            raise e
        finally:
            if task_provider: await task_provider.close()

    async def activate_next_step(self, doc_id: int, current_step_order: int) -> bool:
        """
        链式激活：当前步骤完成后，将下一个步骤从 WAITING_PARENT (-2) 变更为 PENDING (0)
        """
        task_provider = None
        try:
            task_provider = SqlProvider(model=PipelineTask, sql_config_path=self.sql_config_path)
            
            # 1. 查找下一个任务
            next_order = current_step_order + 1
            tasks = await task_provider.get_record_by_condition({
                "doc_id": doc_id,
                "step_order": next_order
            })
            
            if not tasks:
                self.logger.info(f"没有找到步骤 {next_order}，流程结束。")
                return False
                
            next_task = tasks[0]
            
            # 2. 只有当下一个任务处于"等待前置"状态时，才激活它
            # 防止重复激活，或者误操作把已经 Failed 的任务重置了
            if next_task.get("status") == TaskLifecycle.WAITING_PARENT.value:
                await task_provider.update_record(next_task.get("id"), {
                    "status": TaskLifecycle.PENDING.value,
                    "update_time": datetime.now()
                })
                self.logger.info(f"已自动激活步骤 {next_order} (Task ID: {next_task.get('id')})")
                return True
                
            return False
        except Exception as e:
            self.logger.error(f"激活下一步失败: {e}")
            return False
        finally:
            if task_provider: await task_provider.close()

    async def _refresh_parent_doc_status(self, doc_id: int):
        """
        [私有 helper] 重新计算父文档的全局状态和进度
        父文档进度 = 所有子任务进度之和 / 任务数量
        """
        doc_provider = None
        try:
            doc_provider = SqlProvider(model=PdfDocument, sql_config_path=self.sql_config_path)

            # 1. 查出该文档所有任务
            tasks = await self.get_tasks_by_doc_id(doc_id)
            if not tasks: return

            total_tasks = len(tasks)
            status_list = [t.get("status") for t in tasks]

            # 直接累加所有任务的 progress 字段
            total_progress_sum = 0
            for t in tasks:
                p = t.get("progress", 0) or 0
                # 双重保险：如果任务由其他途径标记为完成，视为100
                if t.get("status") == TaskLifecycle.SUCCESS.value:
                    p = 100
                total_progress_sum += p
            calc_progress = int(total_progress_sum / total_tasks)
            is_all_success = all(s == TaskLifecycle.SUCCESS.value for s in status_list)
            is_failed = TaskLifecycle.FAILED.value in status_list
            
            # 2. 计算全局状态 (State Machine Logic)
            new_global_status = DocStatus.RUNNING.value
            if is_all_success:
                new_global_status = DocStatus.SUCCESS.value
                calc_progress = 100
            elif is_failed:
                # 有一个步骤失败全局状态即为失败
                new_global_status = DocStatus.FAILED.value
            elif all(s == TaskLifecycle.PENDING.value for s in status_list):
                new_global_status = DocStatus.PENDING.value
            else:
                # 运行中，封顶 99
                if calc_progress >= 100: calc_progress = 99
                
            # 4. 更新父文档
            doc_update = {
                "status": new_global_status,
                "progress": calc_progress,
            }
            # 如果有报错，把错误信息也提上来
            if is_failed:
                failed_task = next((t for t in tasks if t.get("status") == TaskLifecycle.FAILED.value), None)
                if failed_task:
                    doc_update["process_error"] = f"Step {failed_task.get('step_order')}: {failed_task.get('error_message')}"
            else:
                doc_update["process_error"] = None
            await doc_provider.update_record(doc_id, doc_update)

        except Exception as e:
            self.logger.error(f"刷新父文档状态失败 DocID {doc_id}: {e}")
            # 这里一般只打日志，不抛异常，避免阻塞主流程
        finally:
            if doc_provider: await doc_provider.close()

    async def cleanup_tasks_files(self, doc_id: int, minio_service: MinioService):
        """
        [核心] 删除文档前，清理所有任务产生的中间文件
        这个逻辑从 DocumentService 移到了这里，更符合职责划分
        """
        # 获取所有任务
        tasks = await self.get_tasks_by_doc_id(doc_id)
        
        for task in tasks:
            result_data = task.get('result_data')
            if not result_data or not isinstance(result_data, dict):
                continue

            # 统一清理逻辑：遍历 result_data 里的特定 key
            # 你可以根据 task_type 做 switch case，也可以做通用匹配
            
            # 1. 清理 bucket/path 组合，注意仅仅是清理了markdown这个路径，剩余的步骤的产出物也需要清理
            bucket = result_data.get('bucket')
            path = result_data.get('path') or result_data.get('markdown_path') or result_data.get('chunks_json_path')
            
            if bucket and path:
                try:
                    await minio_service.remove_object(bucket, path)
                    self.logger.info(f"已清理中间文件: {bucket}/{path}")
                except Exception as ex:
                    self.logger.warning(f"清理文件失败: {ex}")