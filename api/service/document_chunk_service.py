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
from api.service.pdf_document_service import PdfDocumentService
from api.service.pipeline_task_service import PipelineTaskService
from api.table.base.pipeline_task import TaskType, TaskLifecycle, ChunkStatus


class DocumentChunkService:
    """
    文档切片 (Chunk) 业务服务
    负责 Chunk 的批量存储、列表查询、编辑以及状态管理
    """

    def __init__(
        self, 
        sql_config_path: str,
        pdf_document_service: PdfDocumentService,
        pipeline_task_service: PipelineTaskService
    ):
        self.sql_config_path = sql_config_path
        self.pdf_document_service = pdf_document_service
        self.pipeline_task_service = pipeline_task_service
        self.logger = logging.getLogger(self.__class__.__name__)

    async def batch_save_chunks(self, document_id: int, chunks_data: List[Dict[str, Any]]) -> int:
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
                    "document_id": document_id,
                    "chunk_index": item["chunk_index"],
                    "content": item["content"],
                    "meta_info": item["meta_info"], # 对应 SQL 中的 :metadata -> 存入 meta_info
                    "image_info": item["image_info"],     # 对应 SQL 中的 :images -> 存入 image_info
                    "token_count": item.get("token_count", 0),
                    "is_indexed": False
                })
            sql_provider = SqlProvider(model=DocumentChunk, sql_config_path=self.sql_config_path)
            count = await sql_provider.batch_create(clean_data)
            self.logger.info(f"Doc {document_id}: 成功保存 {count} 个 Chunks")
            return count
        except Exception as e:
            self.logger.error(f"批量保存 Chunks 异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def export_chunks_as_json(self, document_id: int) -> List[Dict[str, Any]]:
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
                result = await session.execute(stmt, {"doc_id": document_id})
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
        document_id: int, 
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
            condition = {"document_id": document_id}
            
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
                fields=fields
            )
            
            # 如果 result['items'] 是字典列表，这里可以直接返回
            # 如果需要对 image_info 做处理，可以在这里循环处理
            
            return result

        except Exception as e:
            self.logger.error(f"查询 Chunk 列表异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def update_chunk_content(self, chunk_id: str, new_content: str) -> bool:
        """
        [编辑功能] 更新切片内容
        重要：更新内容后，必须将 is_indexed 设为 False，触发重新向量化
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=DocumentChunk, sql_config_path=self.sql_config_path)
            
            data = {
                "content": new_content,
                "is_indexed": False,  # <--- 关键点：标记为脏数据，等待 Worker 更新向量库
                "token_count": len(new_content) # 简单更新 token 数
            }
            
            result = await sql_provider.update_record(record_id=chunk_id, data=data)
            self.logger.info(f"Chunk {chunk_id} 内容已更新，状态重置为 未索引")
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
            sql_provider = SqlProvider(model=DocumentChunk, sql_config_path=self.sql_config_path)
            result = await sql_provider.delete_record(record_id=chunk_id, hard_delete=True)
            if result:
                self.logger.info(f"Chunk {chunk_id} 已删除")
            else:
                self.logger.warning(f"Chunk {chunk_id} 删除失败或不存在")
            return result
        except Exception as e:
            self.logger.error(f"删除单个 Chunk 异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def delete_chunks_by_doc_id(self, document_id: int) -> int:
        """
        根据文档ID 删除所有切片 (用于级联删除)
        [修改后] 使用 SqlProvider 的标准接口
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=DocumentChunk, sql_config_path=self.sql_config_path)
            # 构造删除条件
            condition = {"document_id": document_id}
            deleted_count = await sql_provider.delete_records_by_condition(condition)
            self.logger.info(f"Doc {document_id}: 已清理 {deleted_count} 个 Chunks")
            
            
            tasks = await self.pipeline_task_service.get_tasks_by_doc_id(document_id)
            extract_task = next((t for t in tasks if t['task_type'] == TaskType.MARKDOWN_CHUNK.value), None)
            if not extract_task: return deleted_count
            task_id = extract_task['id']
            self.pipeline_task_service.update_task_status(
                task_id=task_id,
                status=TaskLifecycle.PENDING.value,
                detailed_status=ChunkStatus.PENDING.value,
                progress=ChunkStatus.PENDING.value
            )
            return deleted_count
        except Exception as e:
            self.logger.error(f"删除 Chunks 异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()