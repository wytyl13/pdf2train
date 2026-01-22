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

from pdf2train.api.service.base.minio_service import MinioService

class PipelineTaskService:
    """任务流水线业务服务"""
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        
    async def init_tasks_for_document(self, doc_id: int) -> bool:
        """
        [核心] 为新上传的文档初始化默认流水线任务 (4个步骤)
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PipelineTask)
            
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
            sql_provider = SqlProvider(model=PipelineTask)
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
             
    async def get_specific_task_by_doc_id(self, doc_id: int, task_type_val: int) -> List[Dict[str, Any]]:
        """直接通过 SQL 查询指定类型的任务"""
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PipelineTask)
            # 精确查询条件
            tasks = await sql_provider.get_record_by_condition({
                "doc_id": doc_id, 
                "task_type": task_type_val
            })
            return tasks[0] if tasks else None
        except Exception as e:
            self.logger.error(f"查询特定任务失败: {e}")
            return None
        finally:
            if sql_provider: await sql_provider.close()
        
    async def get_specific_task_id_by_doc_id(self, doc_id: int, task_type_val: int) -> List[Dict[str, Any]]:
        """直接通过 SQL 查询指定类型的任务"""
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PipelineTask)
            # 精确查询条件
            tasks = await sql_provider.get_record_by_condition({
                "doc_id": doc_id, 
                "task_type": task_type_val
            })
            return tasks[0].get("id") if tasks else None
        except Exception as e:
            self.logger.error(f"查询特定任务失败: {e}")
            return None
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
            task_provider = SqlProvider(model=PipelineTask)
            
            # 1. 获取当前任务
            tasks = await task_provider.get_record_by_condition({"id": task_id})
            if not tasks:
                raise ValueError(f"Task ID {task_id} 不存在")
            task = tasks[0]
            doc_id = task.get("doc_id") # 记下来，一会要用

            # 2. 准备更新数据
            update_data = {
                "status": status,
                "end_time": datetime.now()
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
            task_provider = SqlProvider(model=PipelineTask)
            
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
                    "end_time": datetime.now()
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
            doc_provider = SqlProvider(model=PdfDocument)

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
            bucket = result_data.get('bucket') or result_data.get('json_bucket')
            path = result_data.get('path') or result_data.get('markdown_path') or result_data.get('chunks_json_path') or result_data.get('json_path')
            
            if bucket and path:
                try:
                    await minio_service.remove_object(bucket, path)
                    self.logger.info(f"已清理中间文件: {bucket}/{path}")
                except Exception as ex:
                    self.logger.warning(f"清理文件失败: {ex}")
                    
    
    # === Dashboard 核心统计逻辑 ===
    async def get_recent_jobs(self, limit: int = 5) -> List[Dict[str, Any]]:
        """
        [Dashboard] 获取最近处理的任务列表 (按任务活跃时间倒序)
        逻辑：
        1. 查 PipelineTask 表最近的 N 条记录
        2. 提取不重复的 doc_id (保持时间顺序)
        3. 根据 doc_id 查 PdfDocument 信息
        4. 聚合数据返回
        """
        task_provider = None
        doc_provider = None
        
        try:
            # === Step 1: 找出最近活跃的文档 ID ===
            # 我们不知道最近的5个文档产生了多少条任务日志，所以这里扩大采样范围 (比如取 50 条)
            # 这样能保证即使一个文档有 10 个步骤，我们也能覆盖到足够多的不同文档
            sample_size = limit * 10 
            
            task_provider = SqlProvider(model=PipelineTask)
            
            # 使用 get_records_paginated 按 ID 倒序 (ID越大代表越新，比时间更准且有索引)
            # 或者用 order_by=PipelineTask.create_time.desc()
            recent_tasks_data = await task_provider.get_records_paginated(
                page=1,
                page_size=sample_size,
                fields=["doc_id"], # 我们只需要 doc_id，减少数据传输
                order_by=PipelineTask.id.desc() 
            )
            
            # 内存去重，保持顺序
            recent_doc_ids = []
            seen_ids = set()
            for item in recent_tasks_data.get("items", []):
                did = item.get("doc_id")
                if did and did not in seen_ids:
                    recent_doc_ids.append(did)
                    seen_ids.add(did)
                
                # 只要凑够了 limit 个就不找了
                if len(recent_doc_ids) >= limit:
                    break
            
            if not recent_doc_ids:
                return []

            # === Step 2: 查询这些文档的详情 (PdfDocument) ===
            doc_provider = SqlProvider(model=PdfDocument)
            
            # 这里不能简单的用 pagination，因为我们要查指定的 ID 列表
            # 使用 session 手动查询，或者如果你的 get_record_by_condition 支持 in_
            docs_list = []
            async with doc_provider.get_db_session() as session:
                # SELECT * FROM pdf_document WHERE id IN (...)
                stmt = select(PdfDocument).where(PdfDocument.id.in_(recent_doc_ids))
                res = await session.execute(stmt)
                docs_objects = res.scalars().all()
                # 转字典
                docs_list = [
                    {k: v for k, v in d.__dict__.items() if not k.startswith('_sa_')}
                    for d in docs_objects
                ]

            # ⚠️ 重要：数据库 IN 查询返回的顺序是不固定的，我们需要按 recent_doc_ids 的顺序重新排列
            # 构建一个 lookup map
            doc_map_by_id = {d["id"]: d for d in docs_list}
            ordered_docs = []
            for did in recent_doc_ids:
                if did in doc_map_by_id:
                    ordered_docs.append(doc_map_by_id[did])

            # === Step 3: 再次查询这些文档的所有任务状态 (用于画那4个圆点) ===
            # 这次我们已经确定了 doc_ids，可以直接查
            all_tasks_for_status = []
            async with task_provider.get_db_session() as session:
                stmt = select(PipelineTask).where(PipelineTask.doc_id.in_(recent_doc_ids))
                res = await session.execute(stmt)
                tasks_objs = res.scalars().all()
                all_tasks_for_status = [
                    {k: v for k, v in t.__dict__.items() if not k.startswith('_sa_')}
                    for t in tasks_objs
                ]

            # === Step 4: 组装最终数据 (这部分逻辑和之前一样) ===
            task_status_map = defaultdict(dict)
            for t in all_tasks_for_status:
                task_status_map[t.get("doc_id")][t.get("task_type")] = t.get("status")

            ordered_steps = [
                TaskType.MINERU_EXTRACT.value,
                TaskType.MARKDOWN_CHUNK.value,
                TaskType.INSTRUCTION_GEN.value,
                TaskType.QDRANT_INDEX.value
            ]

            result_list = []
            for doc in ordered_docs:
                doc_id = doc["id"]
                current_steps = []
                statuses = task_status_map.get(doc_id, {})
                
                for step_type in ordered_steps:
                    current_steps.append(statuses.get(step_type, TaskLifecycle.PENDING.value))

                item = {
                    "doc_id": doc_id,
                    "file_name": doc.get("file_name") or doc.get("object_name"),
                    "create_time": doc.get("create_time"),
                    "global_status": doc.get("status"),
                    "steps_status": current_steps
                }
                result_list.append(item)

            return result_list

        except Exception as e:
            self.logger.error(f"获取最近任务失败: {e}")
            # 打印堆栈以便调试
            import traceback
            self.logger.error(traceback.format_exc())
            return []
        finally:
            # 关闭 provider
            if task_provider: await task_provider.close()
            if doc_provider: await doc_provider.close()
    
    async def reset_processing_tasks_to_failed(self) -> int:
        """
        [系统级兜底] 重置所有异常中断的任务
        逻辑：
        1. 找出所有状态为 RUNNING (10) 的任务涉及的 doc_id
        2. 批量将这些任务更新为 FAILED (-1)，并备注"因程序中断导致运行中断"
        3. 触发这些 doc_id 的父文档状态刷新，确保文档状态也变更为 FAILED
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PipelineTask)
            
            affected_doc_ids = []
            
            async with sql_provider.get_db_session() as session:
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
                    return 0

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
                await session.commit()
                
                self.logger.warning(f"⚠️ 检测到非正常中断，已重置 {len(affected_doc_ids)} 个文档关联的任务状态。")

            # 3. 刷新父文档状态
            for doc_id in affected_doc_ids:
                await self._refresh_parent_doc_status(doc_id)
                self.logger.info(f"已同步刷新文档状态 DocID: {doc_id}")
            
            return len(affected_doc_ids)

        except Exception as e:
            self.logger.error(f"重置挂起任务失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return 0
        finally:
            if sql_provider: await sql_provider.close()
    
    async def get_dashboard_statistics(self) -> Dict[str, Any]:
        """
        [Dashboard] 获取仪表盘所需的所有统计数据
        聚合了：流水线监控、耗时分析、产出趋势
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PipelineTask)
            
            # 并行查询三个部分的数据 (为了代码简单，这里串行写，实际可用 asyncio.gather)
            monitor_data = await self._get_pipeline_monitor_stats(sql_provider)
            latency_data = await self._get_step_latency_stats(sql_provider)
            trend_data = await self._get_daily_production_trend(sql_provider)
            
            return {
                "pipeline_monitor": monitor_data,
                "step_latency": latency_data,
                "production_trend": trend_data
            }
        except Exception as e:
            self.logger.error(f"Dashboard统计失败: {e}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def _get_pipeline_monitor_stats(self, provider: SqlProvider) -> Dict[str, Dict[str, Any]]:
        """
        [Part 1] 流水线实时监控：运行中、等待中、平均耗时
        """
        # 初始化默认结构
        stats = {
            str(t.value): {"running": 0, "pending": 0, "avg_time_s": 0} 
            for t in TaskType
        }

        async with provider.get_db_session() as session:
            # 1.1 状态计数 (Running & Pending)
            stmt_count = (
                select(PipelineTask.task_type, PipelineTask.status, func.count(PipelineTask.id))
                .where(PipelineTask.status.in_([TaskLifecycle.PENDING.value, TaskLifecycle.RUNNING.value]))
                .group_by(PipelineTask.task_type, PipelineTask.status)
            )
            result_count = await session.execute(stmt_count)
            for task_type, status, count in result_count.all():
                key = str(task_type)
                if key in stats:
                    if status == TaskLifecycle.RUNNING.value:
                        stats[key]["running"] = count
                    elif status == TaskLifecycle.PENDING.value:
                        stats[key]["pending"] = count

            # 1.2 平均耗时 (基于 cost_ms 字段)
            # 取最近成功的 500 条任务计算平均值
            stmt_avg = (
                select(PipelineTask.task_type, func.avg(PipelineTask.cost_ms))
                .where(PipelineTask.status == TaskLifecycle.SUCCESS.value)
                .where(PipelineTask.cost_ms > 0) # 排除异常数据
                .group_by(PipelineTask.task_type)
            )
            result_avg = await session.execute(stmt_avg)
            for task_type, avg_ms in result_avg.all():
                key = str(task_type)
                if key in stats and avg_ms:
                    stats[key]["avg_time_s"] = int(avg_ms / 1000)

        return stats

    async def _get_step_latency_stats(self, provider: SqlProvider) -> Dict[str, Dict[str, int]]:
        """
        [Part 2] 步骤耗时分析：平均耗时 vs 最大耗时
        """
        stats = {}
        async with provider.get_db_session() as session:
            # 查询每种任务类型的 avg 和 max cost_ms
            stmt = (
                select(
                    PipelineTask.task_type, 
                    func.avg(PipelineTask.cost_ms).label("avg_ms"), 
                    func.max(PipelineTask.cost_ms).label("max_ms")
                )
                .where(PipelineTask.status == TaskLifecycle.SUCCESS.value)
                .where(PipelineTask.cost_ms > 0)
                .group_by(PipelineTask.task_type)
            )
            result = await session.execute(stmt)
            
            for task_type, avg_ms, max_ms in result.all():
                # 转换成秒
                stats[str(task_type)] = {
                    "avg_time_s": int(avg_ms / 1000) if avg_ms else 0,
                    "max_time_s": int(max_ms / 1000) if max_ms else 0
                }
        
        # 补全缺失的类型
        for t in TaskType:
            if str(t.value) not in stats:
                stats[str(t.value)] = {"avg_time_s": 0, "max_time_s": 0}
                
        return stats

    async def _get_daily_production_trend(self, provider: SqlProvider) -> Dict[str, List[int]]:
        """
        [Part 3] 每日产出趋势 (最近7天)
        统计维度：
        1. 上传文档数 (Step 1 成功数)
        2. QA 指令数 (Step 3 成功且有产出)
        3. 向量切片数 (Step 2/4 成功且有产出)
        """
        # 生成最近7天的日期列表 (MM-DD)
        today = datetime.now().date()
        date_list = [(today - timedelta(days=i)) for i in range(6, -1, -1)]
        date_str_list = [d.strftime("%m-%d") for d in date_list]
        
        # 初始化结果结构
        trend = {
            "dates": date_str_list,
            "docs": [0] * 7,
            "qa_pairs": [0] * 7,
            "vectors": [0] * 7
        }

        async with provider.get_db_session() as session:
            # 查最近7天的所有成功任务
            start_date = date_list[0]
            stmt = (
                select(
                    func.date(PipelineTask.end_time).label("date"),
                    PipelineTask.task_type,
                    PipelineTask.result_data # 需要解析 result_data 里的 count
                )
                .where(PipelineTask.status == TaskLifecycle.SUCCESS.value)
                .where(PipelineTask.end_time >= start_date)
            )
            
            result = await session.execute(stmt)
            
            # 内存聚合 (因为 result_data 是 JSON，SQL 直接 sum 比较麻烦)
            temp_agg = defaultdict(lambda: {"docs": 0, "qa": 0, "vec": 0})
            
            for row_date, task_type, result_json in result.all():
                if not row_date: continue
                # date对象转字符串 key
                d_key = row_date.strftime("%m-%d")
                
                # 1. 上传文档 (Step 1 MINERU_EXTRACT)
                if task_type == TaskType.MINERU_EXTRACT.value:
                    temp_agg[d_key]["docs"] += 1
                
                # 2. QA 对 (Step 3 INSTRUCTION_GEN)
                elif task_type == TaskType.INSTRUCTION_GEN.value:
                    # 从 JSON 中取 total_count
                    count = 0
                    if result_json and isinstance(result_json, dict):
                        count = result_json.get("total_count", 0)
                    temp_agg[d_key]["qa"] += count
                
                # 3. 向量/切片 (Step 2 MARKDOWN_CHUNK)
                elif task_type == TaskType.MARKDOWN_CHUNK.value:
                    # 从 JSON 中取 chunk_count
                    count = 0
                    if result_json and isinstance(result_json, dict):
                        count = result_json.get("chunk_count", 0)
                    temp_agg[d_key]["vec"] += count

            # 填回数组
            for i, d_str in enumerate(date_str_list):
                if d_str in temp_agg:
                    trend["docs"][i] = temp_agg[d_str]["docs"]
                    trend["qa_pairs"][i] = temp_agg[d_str]["qa"]
                    trend["vectors"][i] = temp_agg[d_str]["vec"]

        return trend              
    