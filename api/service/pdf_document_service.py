#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/17 12:46
@Author  : weiyutao
@File    : pdf_document_service.py
"""

import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy import and_, or_

# 导入数据库模型和工具
from api.table.base.pdf_document import PdfDocument, DocStatus, CoverInfo
from api.table.base.pipeline_task import ExtractTaskResult, PipelineTask, TaskLifecycle, ChunkTaskResult, TaskType
from agent.provider.sql_provider import SqlProvider
from api.service.minio_service import MinioService
from api.service.pipeline_task_service import PipelineTaskService
from api.service.document_chunk_service import DocumentChunkService

class PdfDocumentService:
    """
    PDF 文档业务服务
    """
    def __init__(
        self, 
        sql_config_path: str,
        minio_service: MinioService,
        document_chunk_service: Optional[DocumentChunkService] = None,
        task_service: Optional[PipelineTaskService] = None,
    ):
        self.sql_config_path = sql_config_path
        self.minio_service = minio_service
        self.document_chunk_service = document_chunk_service or DocumentChunkService(self.sql_config_path)
        self.task_service = task_service or PipelineTaskService(self.sql_config_path)
        self.protocol = "http://" if "localhost" in self.minio_service.endpoint else "https://"
        self.logger = logging.getLogger(self.__class__.__name__)


    def _get_base_url(self) -> str:
        """
        [私有辅助] 获取 MinIO 的 Base URL
        注意：生产环境可能需要区分内网 IP 和外网域名，这里暂时取 MinIO Service 配置
        """
        endpoint = self.minio_service.endpoint
        return f"{self.protocol}{endpoint}"


    async def get_document_list(
        self, 
        condition: Dict[str, Any], 
        page: Optional[int] = None, 
        page_size: Optional[int] = None,
        filter_step_type: Optional[int] = None,
        filter_step_status: Optional[List[int]] = None,
        keyword: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        根据条件查询文档列表
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PdfDocument, sql_config_path=self.sql_config_path)
            
            # 定义需要返回的字段
            fields = [
                "id", "file_name", "bucket_name", "object_name", "file_size", 
                "page_count", "author", "status", "user_name", "create_time", 
                "original_title", "summary", "process_error", "content_type",
                "cover_info", "progress"
            ]
            
            complex_filters = []
            
            # 联表过滤任务状态
            if filter_step_type is not None:
                task_conditions = [PipelineTask.task_type == filter_step_type]
                
                if filter_step_status is not None:
                    # 如果传的是列表，用 in_；如果是单个，转成列表处理
                    if isinstance(filter_step_status, list):
                        task_conditions.append(PipelineTask.status.in_(filter_step_status))
                    else:
                        task_conditions.append(PipelineTask.status == filter_step_status)
                
                # 构造 EXISTS 查询
                complex_filters.append(
                    PdfDocument.tasks.any(and_(*task_conditions))
                )
            
            # 关键词检索
            if keyword:
                # 逻辑：文件名 包含 keyword  OR  作者 包含 keyword
                # SQL: WHERE (file_name LIKE '%keyword%' OR author LIKE '%keyword%')
                search_rule = or_(
                    PdfDocument.file_name.like(f"%{keyword}%"),
                    PdfDocument.author.like(f"%{keyword}%"),
                    # PdfDocument.summary.like(f"%{keyword}%") # 如果你也想搜摘要，把这行解注
                )
                complex_filters.append(search_rule)
            
            if page is not None and page_size is not None:
                # 模式 A: 分页查询 (给前端 API 用)
                result = await sql_provider.get_records_paginated(
                    page=page,
                    page_size=page_size,
                    condition=condition,
                    filters=complex_filters,
                    fields=fields
                )
            else:
                # 模式 B: 全量/普通查询 (给 delete_file 等内部逻辑用)
                items = await sql_provider.get_record_by_condition(condition, fields)
                result = {"items": items, "total": len(items)}
                
            base_url = self._get_base_url()
            for doc in result['items']:
                # A. 处理下载链接
                if doc.get('bucket_name') and doc.get('object_name'):
                    doc['download_url'] = f"{base_url}/{doc['bucket_name']}/{doc['object_name']}"
                
                # B. 处理封面链接 (利用 Pydantic 逻辑)
                raw_cover = doc.get('cover_info')
                doc['cover_url'] = None # 默认值
                
                if raw_cover:
                    try:
                        # 使用类方法验证和处理，复用逻辑
                        cover_obj = CoverInfo.model_validate(raw_cover)
                        doc['cover_url'] = f"{base_url}/{cover_obj.bucket}/{cover_obj.path}"
                    except Exception:
                        pass # 忽略解析错误
            
            return result

        except Exception as e:
            self.logger.error(f"Service查询异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()


    async def get_dashboard_statistics(self) -> Dict[str, int]:
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PdfDocument, sql_config_path=self.sql_config_path)
            
            # 1. 获取原始计数
            # 例如: {0: 3, 10: 2, 20: 1, 30: 1, 100: 3, -1: 2}
            raw_counts = await sql_provider.get_group_counts("status")
            
            # 2. 计算各状态数量
            
            # Total: 所有状态的总和
            total_count = sum(raw_counts.values())
            
            # Pending (0): 未处理
            # 虽然仪表盘没单独卡片，但为了数据完整建议返回，或者用于校验 Total
            pending_count = raw_counts.get(0, 0)
            
            # Processing (10, 20, 30): 严格的处理中
            processing_count = (
                raw_counts.get(10, 0) +  # 拆分/OCR
                raw_counts.get(20, 0) +  # 合并中
                raw_counts.get(30, 0)    # 上传中
            )
            
            # Completed (100): 处理完成
            completed_count = raw_counts.get(100, 0)
            
            # Failed (-1): 处理失败
            failed_count = raw_counts.get(-1, 0)

            return {
                "total": total_count,
                "pending": pending_count,       # [新增] 单独返回未处理数量
                "processing": processing_count, # [修改] 仅包含 10, 20, 30
                "completed": completed_count,
                "failed": failed_count
            }
            
        except Exception as e:
            self.logger.error(f"统计服务异常: {str(e)}")
            raise e
        finally:
            if sql_provider:
                await sql_provider.close()


    async def create_document(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        创建新的文档记录
        """
        sql_provider = None
        try:
            # 补充默认值
            if "create_time" not in data:
                data["create_time"] = datetime.now()
            if "status" not in data:
                data["status"] = DocStatus.PENDING.value  # 默认待处理
            if "user_name" not in data:
                data["user_name"] = "system"
            if "progress" not in data:
                data["progress"] = 0

            sql_provider = SqlProvider(model=PdfDocument, sql_config_path=self.sql_config_path)
            
            # 1. 执行插入 Document
            doc_id = await sql_provider.add_record(data)

            # 2. [新增] 委托 TaskService 初始化流水线
            await self.task_service.init_tasks_for_document(doc_id)
            data['id'] = doc_id
            return data
        except Exception as e:
            self.logger.error(f"Service创建异常: {str(e)}")
            raise e
        finally:
            if sql_provider:
                await sql_provider.close()


    async def update_document(self, doc_id: int, data: Dict[str, Any]) -> bool:
        """
        更新文档信息
        """
        sql_provider = None
        try:
            # 补充更新时间（如果表里有 update_time 字段的话）
            data["update_time"] = datetime.now()
            sql_provider = SqlProvider(model=PdfDocument, sql_config_path=self.sql_config_path)
            
            # 执行更新
            result = await sql_provider.update_record(record_id=doc_id, data=data)
            return result
        except Exception as e:
            self.logger.error(f"Service更新异常: {str(e)}")
            raise e
        finally:
            if sql_provider:
                await sql_provider.close()


    async def delete_document(self, doc_id: int) -> bool:
        """
        物理删除文档记录
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PdfDocument, sql_config_path=self.sql_config_path)
            # 1. 查询文档 (为了拿到文件路径)
            doc: PdfDocument = await sql_provider.get_with_relations(doc_id, relations=["tasks"])
            if not doc:
                return False
            
            # 2 委托 TaskService 清理中间产物 (Markdown/Index/Chunks)
            if self.task_service:
                await self.task_service.cleanup_tasks_files(doc_id, self.minio_service)
            
            
            # 3. 清理提取产生的 Markdown 和 图片
            extract_res = doc.latest_extract_result
            if extract_res:
                # A. 删除 Markdown 文件
                if extract_res.md_bucket and extract_res.markdown_path:
                    try:
                        await self.minio_service.remove_object(extract_res.md_bucket, extract_res.markdown_path)
                        self.logger.info(f"关联Markdown已删除: {extract_res.markdown_path}")
                    except Exception as e:
                        self.logger.warning(f"Markdown删除失败: {e}")

                # B. 删除图片目录
                # 注意：MinIO 没有直接的 "删除文件夹" API，我们需要先列出该前缀下的所有文件，再循环删除
                if extract_res.images_bucket and extract_res.images_path:
                    try:
                        # 1. 列出所有图片 (使用 existing MinioService method)
                        # images_path 类似于 "images/doc_101/"
                        objects = await self.minio_service.list_bucket_objects(
                            extract_res.images_bucket, 
                            prefix=extract_res.images_path
                        )
                        
                        # 2. 循环删除
                        count = 0
                        for obj in objects:
                            await self.minio_service.remove_object(extract_res.images_bucket, obj['object_name'])
                            count += 1
                        
                        self.logger.info(f"关联图片已删除: {count} 张 (Prefix: {extract_res.images_path})")
                    except Exception as e:
                        self.logger.warning(f"图片目录删除失败: {e}")
            
            
            # 清理数据库中的chunks（待优化）
            await self.document_chunk_service.delete_chunks_by_doc_id(doc_id)
            
            # 4. 删除源文件
            bucket = doc.bucket_name
            obj_name = doc.object_name
            if bucket and obj_name:
                try:
                    await self.minio_service.remove_object(bucket, obj_name)
                    self.logger.info(f"源文件已删除: {obj_name}")
                except Exception as e:
                    self.logger.warning(f"源文件删除失败或文件不存在: {str(e)}")
            
            # 5. 删除封面图
            if doc.cover:
                try:
                    await self.minio_service.remove_object(doc.cover.bucket, doc.cover.path)
                    self.logger.info(f"封面图已删除: {doc.cover.full_path}")
                except Exception as e:
                    self.logger.warning(f"封面删除失败: {e}")
            
            result = await sql_provider.delete_record(record_id=doc_id, hard_delete=True)
            return result
        except Exception as e:
            self.logger.error(f"Service删除异常: {str(e)}")
            raise e
        finally:
            if sql_provider:
                await sql_provider.close()
                
                
    async def get_markdown_content(self, doc_id: int) -> str:
        """获取文档关联的 Markdown 内容"""
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PdfDocument, sql_config_path=self.sql_config_path)
            
            # 1. 一次查询，带出 tasks 关系
            # 返回的是 PdfDocument 对象，不是字典
            doc: PdfDocument = await sql_provider.get_with_relations(doc_id, relations=["tasks"])
            
            if not doc:
                raise ValueError(f"文档 ID {doc_id} 不存在")
            
            # 2. 直接调用 Model 层的智能属性
            # 因为 tasks 已经预加载了，这里是在内存中计算，无需再次查库
            result: ExtractTaskResult = doc.latest_extract_result
            
            if not result:
                raise ValueError("未找到已完成的解析结果 (Markdown Path)")
            
            # 3. 读 MinIO (bucket_name 也是直接从对象取)
            return await self.minio_service.read_object_text(
                result.md_bucket,      # 自动提示
                result.markdown_path   # 自动提示
            )
            
        finally:
            if sql_provider: await sql_provider.close()
    
    
    async def get_chunk_content(self, doc_id: int) -> str:
        """获取文档关联的 Markdown 内容"""
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PdfDocument, sql_config_path=self.sql_config_path)
            
            # 1. 一次查询，带出 tasks 关系
            # 返回的是 PdfDocument 对象，不是字典
            doc: PdfDocument = await sql_provider.get_with_relations(doc_id, relations=["tasks"])
            
            if not doc:
                raise ValueError(f"文档 ID {doc_id} 不存在")
            
            # 2. 直接调用 Model 层的智能属性
            # 因为 tasks 已经预加载了，这里是在内存中计算，无需再次查库
            result: ChunkTaskResult = doc.latest_chunk_result
            if not result:
                raise ValueError("未找到已完成的解析结果 (Json Path)")
            
            # 3. 读 MinIO (bucket_name 也是直接从对象取)
            return await self.minio_service.read_object_text(
                result.json_bucket,      # 自动提示
                result.json_path   # 自动提示
            )
            
        finally:
            if sql_provider: await sql_provider.close()
            
            
    async def get_chunk_count(self, doc_id: int) -> str:
        """获取文档关联的 Markdown 内容"""
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PdfDocument, sql_config_path=self.sql_config_path)
            
            # 1. 一次查询，带出 tasks 关系
            # 返回的是 PdfDocument 对象，不是字典
            doc: PdfDocument = await sql_provider.get_with_relations(doc_id, relations=["tasks"])
            
            if not doc:
                raise ValueError(f"文档 ID {doc_id} 不存在")
            
            # 2. 直接调用 Model 层的智能属性
            # 因为 tasks 已经预加载了，这里是在内存中计算，无需再次查库
            result: ChunkTaskResult = doc.latest_chunk_result
            if not result:
                raise ValueError("未找到已完成的解析结果 (Json Path)")
            
            # 3. 读 MinIO (bucket_name 也是直接从对象取)
            return result.chunk_count
        finally:
            if sql_provider: await sql_provider.close()
    
    
    async def save_markdown_content(self, doc_id: int, new_content: str) -> bool:
        """
        [优化后] 保存/更新 Markdown 内容
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PdfDocument, sql_config_path=self.sql_config_path)
            
            # 1. 同样查出带关系的对象
            doc: PdfDocument = await sql_provider.get_with_relations(doc_id, relations=["tasks"])
            
            if not doc:
                raise ValueError(f"文档 ID {doc_id} 不存在")
                
            # 2. 获取路径
            result: ExtractTaskResult = doc.latest_extract_result
            
            if not result:
                 raise ValueError("无法保存：该文档尚未生成初始 Markdown 文件")
            
            # 3. 写 MinIO
            await self.minio_service.put_object_text(result.md_bucket, result.markdown_path, new_content)
            return True
        finally:
            if sql_provider: await sql_provider.close()