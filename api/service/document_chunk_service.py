#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/24 13:26
@Author  : weiyutao
@File    : document_chunk_service.py
"""

import logging
from typing import Dict, Any, List, Optional
from sqlalchemy import text, and_

# 导入数据库模型
from api.table.base.document_chunk import DocumentChunk
from agent.provider.sql_provider import SqlProvider
from api.service.pipeline_task_service import PipelineTaskService
from api.table.base.pipeline_task import TaskType, TaskLifecycle, ChunkStatus, ChunkTaskResult


class DocumentChunkService:
    """
    文档切片 (Chunk) 业务服务
    负责 Chunk 的批量存储、列表查询、编辑以及状态管理
    """

    def __init__(
        self, 
        sql_config_path: str,
        pipeline_task_service: PipelineTaskService
    ):
        self.sql_config_path = sql_config_path
        self.pipeline_task_service = pipeline_task_service
        self.logger = logging.getLogger(self.__class__.__name__)

    async def batch_save_chunks(self, doc_id: int, chunks_data: List[Dict[str, Any]]) -> int:
        """
        [核心] 批量保存切片数据
        通常在 ETL 解析完成后调用
        
        Args:
            document_id: 关联的文档 ID
            chunks_data: 也就是你 pipeline 里的 formatted_chunks 列表
        """
        if not chunks_data:
            return 0
        sql_provider = None
        try:
            clean_data = []
            for item in chunks_data:
                clean_data.append({
                    "id": item["id"],
                    "document_id": doc_id,
                    "chunk_index": item["chunk_index"],
                    "content": item["content"],
                    "meta_info": item["meta_info"], # 对应 SQL 中的 :metadata -> 存入 meta_info
                    "image_info": item["image_info"],     # 对应 SQL 中的 :images -> 存入 image_info
                    "token_count": item.get("token_count", 0),
                    "is_indexed": False
                })
            sql_provider = SqlProvider(model=DocumentChunk, sql_config_path=self.sql_config_path)
            count = await sql_provider.batch_create(clean_data)
            self.logger.info(f"Doc {doc_id}: 成功保存 {count} 个 Chunks")
            return count
        except Exception as e:
            self.logger.error(f"批量保存 Chunks 异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def export_chunks_as_json(self, doc_id: int) -> List[Dict[str, Any]]:
        """
        [导出功能] 从数据库读取最新数据，还原为 JSON 格式
        用于前端"下载最新结果"或"重新归档"
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=DocumentChunk, sql_config_path=self.sql_config_path)
            
            # 1. 查询该文档所有切片，按 index 排序
            # 这里不用分页，因为是导出全量
            stmt = text("SELECT * FROM document_chunks WHERE document_id = :doc_id ORDER BY chunk_index ASC")
            
            # 注意：这里需要获取 engine 或使用 execute_sql
            # 假设 sql_provider 提供了获取 session 或执行 raw sql 的能力
            async with sql_provider.get_db_session() as session:
                result = await session.execute(stmt, {"doc_id": doc_id})
                rows = result.fetchall()
            
            # 2. 转换为 List[Dict]
            export_data = []
            for row in rows:
                # row 是 SQLAlchemy Row 对象，转换为 dict
                # 注意：根据你的表结构，image_info 和 meta_info 存的是 JSON，DB读取出来通常自动转为 dict (取决于驱动)
                # 如果是字符串需 json.loads，如果是 dict 则直接用
                item = {
                    "id": row.id,
                    "document_id": row.document_id,
                    "chunk_index": row.chunk_index,
                    "content": row.content,
                    "images": row.image_info,  # 数据库字段名
                    "metadata": row.meta_info, # 数据库字段名
                    "token_count": row.token_count,
                    "is_indexed": row.is_indexed
                }
                export_data.append(item)
                
            return export_data
            
        except Exception as e:
            self.logger.error(f"导出 JSON 失败: {e}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def get_chunk_list(
        self, 
        doc_id: int, 
        chunk_id: str,
        page: int = 1, 
        page_size: int = 20,
        keyword: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        获取某文档的切片列表 (用于前端 Knowledge Base 详情页)
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=DocumentChunk, sql_config_path=self.sql_config_path)
            
            # 构造查询条件
            condition = {"document_id": doc_id}
            if chunk_id:
                condition["id"] = chunk_id
            filters = []
            if keyword:
                filters.append(DocumentChunk.content.like(f"%{keyword}%"))
            
            # 定义返回字段
            fields = [
                "id", "chunk_index", "content", "token_count", 
                "image_info", "is_indexed", "page_numbers", "meta_info"
            ]
            
            # 执行分页查询
            # 注意：通常希望按 chunk_index 顺序展示
            # 如果 SqlProvider 支持 order_by 最好，如果不支持，可能需要 modify provider
            # 这里假设 get_records_paginated 支持默认排序或可以在 model 里定义
            
            result = await sql_provider.get_records_paginated(
                page=page,
                page_size=page_size,
                condition=condition,
                filters=filters,
                fields=fields,
                order_by=DocumentChunk.chunk_index.asc()
            )
            
            # 如果 result['items'] 是字典列表，这里可以直接返回
            # 如果需要对 image_info 做处理，可以在这里循环处理
            
            return result

        except Exception as e:
            self.logger.error(f"查询 Chunk 列表异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def update_chunk_info(
        self, 
        chunk_id: str, 
        content: Optional[str] = None, 
        meta_info: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        [编辑功能] 通用更新切片信息 (内容 或 元数据)
        注意：无论是修改内容还是元数据，都需要将 is_indexed 设为 False，触发重新向量化
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=DocumentChunk, sql_config_path=self.sql_config_path)
            
            data = {}
            
            # 1. 处理内容更新
            if content is not None:
                data["content"] = content
                data["token_count"] = len(content)
                data["is_indexed"] = False
            
            # 2. 处理元数据更新
            if meta_info is not None:
                data["meta_info"] = meta_info
                data["is_indexed"] = False

            # 如果没有任何要更新的字段，直接返回
            if not data:
                return False

            # 执行更新
            result = await sql_provider.update_record(record_id=chunk_id, data=data)
            
            update_fields = list(data.keys())
            self.logger.info(f"Chunk {chunk_id} 更新字段 {update_fields}, 状态重置为 未索引")
            return result
            
        except Exception as e:
            self.logger.error(f"更新 Chunk 异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def delete_chunk(self, chunk_id: str) -> bool:
        """
        删除单个切片
        通常用于人工清洗数据时，手动删除质量差的切片
        """
        sql_provider = None
        try:
            # step1 获取document_id以更新对应任务生成数据
            doc_id = await self.get_document_id_by_chunk_id(chunk_id)
                
            if not doc_id:
                self.logger.warning(f"准备删除的 Chunk {chunk_id} 不存在")
                return False

            # step2 删除chunk数据
            sql_provider = SqlProvider(model=DocumentChunk, sql_config_path=self.sql_config_path)
            result = await sql_provider.delete_record(record_id=chunk_id, hard_delete=True)
            
            if result:
                self.logger.info(f"Chunk {chunk_id} 已删除")
            else:
                self.logger.warning(f"Chunk {chunk_id} 删除失败或不存在")
            
            # step3 更新对应任务生成的数据
            try:
                await self._decrease_task_chunk_count(doc_id)
            except Exception as update_err:
                self.logger.error(f"Doc {doc_id} 统计数据更新失败: {update_err}")
                # 注意：这里不 throw 异常，因为删除已经成功了
            return result
        except Exception as e:
            self.logger.error(f"删除单个 Chunk 异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def _decrease_task_chunk_count(self, doc_id: int):
        """
        [独立抽取的私有方法] 更新任务统计数据 -1
        """
        tasks = await self.pipeline_task_service.get_tasks_by_doc_id(doc_id)
        extract_task = next((t for t in tasks if t['task_type'] == TaskType.MARKDOWN_CHUNK.value), None)
        
        if not extract_task: 
            return

        current_result_data = extract_task.get('result_data') or {}
        task_id = extract_task['id']
        final_data = current_result_data

        try:
            # 1. 尝试使用 Pydantic 规范化处理
            result_obj = ChunkTaskResult.model_validate(current_result_data)
            if result_obj.chunk_count > 0:
                result_obj.chunk_count -= 1
                if result_obj.chunk_count == 0:
                    await self.pipeline_task_service.update_task_status(
                        task_id=task_id,
                        status=TaskLifecycle.PENDING.value, # 保持原状态
                        detailed_status=ChunkStatus.PENDING.value,
                        progress=ChunkStatus.PENDING.value,
                        result_data=result_obj.model_dump()
                    )
                    return
            final_data = result_obj.model_dump()
            
        except Exception as e:
            # 2. 降级处理：直接操作字典
            self.logger.warning(f"Result data 校验失败，尝试直接更新字典: {e}")
            old_count = current_result_data.get('chunk_count', 0)
            if old_count > 0:
                # 浅拷贝避免直接修改原引用（视情况而定，安全起见）
                final_data = current_result_data.copy()
                final_data['chunk_count'] = old_count - 1

        # 3. [关键修正] 必须加 await
        await self.pipeline_task_service.update_task_status(
            task_id=task_id,
            status=extract_task['status'], # 保持原状态
            detailed_status=extract_task['detailed_status'],
            progress=extract_task['progress'],
            result_data=final_data
        )

    async def get_document_id_by_chunk_id(self, chunk_id: str) -> Optional[int]:
        """
        [辅助方法] 根据 Chunk ID 快速反查 Document ID
        用于删除、校验或联动更新
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=DocumentChunk, sql_config_path=self.sql_config_path)
            result = await sql_provider.get_record_by_condition(
                condition={"id": chunk_id},
                fields=["id", "document_id"]
            )
            if result:
                return result[0].get("document_id")
            else:
                return None
        except Exception as e:
            self.logger.error(f"查询 Document ID 失败 (Chunk {chunk_id}): {str(e)}")
            return None
        finally:
            if sql_provider: await sql_provider.close()

    async def delete_chunks_by_doc_id(self, doc_id: int) -> int:
        """
        根据文档ID 删除所有切片 (用于级联删除)
        [修改后] 使用 SqlProvider 的标准接口
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=DocumentChunk, sql_config_path=self.sql_config_path)
            # 构造删除条件
            condition = {"document_id": doc_id}
            deleted_count = await sql_provider.delete_records_by_condition(condition)
            self.logger.info(f"Doc {doc_id}: 已清理 {deleted_count} 个 Chunks")
            
            
            tasks = await self.pipeline_task_service.get_tasks_by_doc_id(doc_id)
            extract_task = next((t for t in tasks if t['task_type'] == TaskType.MARKDOWN_CHUNK.value), None)
            if not extract_task: return deleted_count
            result_data = extract_task.get('result_data') or {}
            result_obj = ChunkTaskResult.model_construct(**result_data)
            result_obj.chunk_count = 0
            task_id = extract_task['id']
            await self.pipeline_task_service.update_task_status(
                task_id=task_id,
                status=TaskLifecycle.PENDING.value,
                detailed_status=ChunkStatus.PENDING.value,
                progress=ChunkStatus.PENDING.value,
                result_data=result_obj.model_dump()
            )
            return deleted_count
        except Exception as e:
            import traceback
            self.logger.error(f"删除 Chunks 异常: {str(e)} \n {traceback.format_exc()}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()