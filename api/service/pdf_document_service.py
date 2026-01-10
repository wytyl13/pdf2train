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
from pathlib import Path
from dotenv import load_dotenv, dotenv_values
from sqlalchemy import select, func, and_, or_


# 导入数据库模型和工具
from api.table.base.pdf_document import PdfDocument, DocStatus, CoverInfo
from api.table.base.pipeline_task import ExtractTaskResult, PipelineTask, TaskLifecycle, ChunkTaskResult, TaskType
from agent.provider.sql_provider import SqlProvider
from api.service.minio_service import MinioService
from api.service.pipeline_task_service import PipelineTaskService
from api.service.document_chunk_service import DocumentChunkService
from api.service.instruction_datum_service import InstructionDatumService
from api.service.llm_config_service import LLMConfigService
from api.table.base.llm_enum import ModelType


ROOT_DIRECTORY = Path(__file__).parent.parent.parent
ENV_PATH = str(ROOT_DIRECTORY / ".env")
environment = dotenv_values(ENV_PATH)
MINIO_BASE_URL = environment.get("MINIO_BASE_URL", "http://localhost:9000")

class PdfDocumentService:
    """
    PDF 文档业务服务
    """
    def __init__(
        self, 
        sql_config_path: str,
        minio_service: MinioService,
        instruction_datum_service: InstructionDatumService,
        llm_config_service: LLMConfigService,
        document_chunk_service: Optional[DocumentChunkService] = None,
        task_service: Optional[PipelineTaskService] = None,
    ):
        self.sql_config_path = sql_config_path
        self.minio_service = minio_service
        self.document_chunk_service = document_chunk_service or DocumentChunkService(self.sql_config_path)
        self.task_service = task_service or PipelineTaskService(self.sql_config_path)
        self.instruction_datum_service = instruction_datum_service
        self.llm_config_service = llm_config_service
        self.minio_base_url = MINIO_BASE_URL
        self.logger = logging.getLogger(self.__class__.__name__)


    def _get_base_url(self) -> str:
        """
        [私有辅助] 获取 MinIO 的 Base URL
        注意：生产环境可能需要区分内网 IP 和外网域名，这里暂时取 MinIO Service 配置
        """
        return self.minio_base_url


    async def export_books_data(
        self, 
        filter_step_type: Optional[int] = None,
        filter_step_status: Optional[List[int]] = None,
        keyword: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        [新增] 导出书籍清单数据
        获取全量符合条件的数据，并进行格式化，便于生成 Excel/CSV
        """
        try:
            # 1. 复用 get_document_list 获取全量数据 (page=None, page_size=None)
            # 传入空字典作为基础 condition
            result = await self.get_document_list(
                condition={},
                page=None,
                page_size=None,
                filter_step_type=filter_step_type,
                filter_step_status=filter_step_status,
                keyword=keyword
            )
            
            raw_items = result.get("items", [])
            export_list = []

            # 2. 定义状态映射 (根据您的 DocStatus 定义调整)
            status_map = {
                0: "待处理",
                10: "解析中",
                20: "合并中",
                30: "上传中",
                100: "已完成",
                -1: "失败"
            }

            # 3. 数据格式化 (Flatten & Format)
            for item in raw_items:
                # 计算文件大小 (MB)
                size_mb = round(item.get("file_size", 0) / (1024 * 1024), 2)
                
                # 格式化时间
                create_time = item.get("create_time")
                create_time_str = create_time.strftime("%Y-%m-%d %H:%M:%S") if isinstance(create_time, datetime) else str(create_time)

                # 获取状态文本
                status_code = item.get("status")
                status_text = status_map.get(status_code, f"未知({status_code})")

                # 构造导出行的字典 (Keys 将作为 Excel/CSV 的表头)
                row = {
                    "文档ID": str(item.get("id")),
                    "文件名称": item.get("file_name"),
                    "原标题": item.get("original_title") or "",
                    "作者": item.get("author") or "未知",
                    "文件大小(MB)": size_mb,
                    "页数": item.get("page_count", 0),
                    "当前状态": status_text,
                    "上传用户": item.get("user_name"),
                    "上传时间": create_time_str,
                    "摘要": item.get("summary") or "",
                    "下载链接": item.get("download_url") or ""
                }
                export_list.append(row)

            return export_list

        except Exception as e:
            self.logger.error(f"导出数据准备失败: {str(e)}")
            raise e


    async def export_books_jsonl(
        self, 
        filter_step_type: Optional[int] = None,
        filter_step_status: Optional[List[int]] = None,
        keyword: Optional[str] = None
    ) -> str:
        """
        [新增] 导出书籍清单为 JSONL 格式字符串
        每行一个 JSON 对象，便于流式读取或作为日志/备份文件
        """
        import json
        
        # 1. 复用 export_books_data 获取清洗后的列表
        # (如果您希望导出原始字段名，可以改调 get_document_list)
        data_list = await self.export_books_data(
            filter_step_type=filter_step_type,
            filter_step_status=filter_step_status,
            keyword=keyword
        )
        
        # 2. 生成 JSONL 字符串
        # ensure_ascii=False 确保中文不被转义
        # default=str 处理 datetime 等无法直接序列化的对象 (虽然 export_books_data 已经处理了)
        jsonl_lines = [
            json.dumps(row, ensure_ascii=False, default=str) 
            for row in data_list
        ]
        
        # 3. 用换行符连接
        return "\n".join(jsonl_lines)


    async def get_document_list(
        self, 
        condition: Dict[str, Any], 
        page: Optional[int] = None, 
        page_size: Optional[int] = None,
        filter_step_type: Optional[int] = None,
        filter_step_status: Optional[List[int]] = None,
        keyword: Optional[str] = None,
        kb_id: Optional[int] = None
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
                "cover_info", "progress", "kb_id",
                "instruction_gen_llm_config",
                "h_title_llm_config",
                "embedding_llm_config"
            ]
            if kb_id is not None:
                condition["kb_id"] = kb_id
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
            
            # 在这里直接查询一次默认 Embedding 配置名称 (初始化)
            default_embedding_name = await self.llm_config_service.get_active_config_name(
                model_type=ModelType.EMBEDDING.value
            )
            
            
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
                    
                if not doc.get('embedding_llm_config'):
                    doc['embedding_llm_config'] = default_embedding_name
            
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

            if "instruction_gen_llm_config" not in data:
                data["instruction_gen_llm_config"] = await self.llm_config_service.get_active_config_name()

            if "h_title_llm_config" not in data:
                data["h_title_llm_config"] = await self.llm_config_service.get_active_config_name()

            if "embedding_llm_config" not in data:
                data["embedding_llm_config"] = await self.llm_config_service.get_active_config_name(model_type=ModelType.EMBEDDING.value)

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
            
            # [TODO 未来优化点]
            # 如果修改了 kb_id 且文档状态是 SUCCESS，可能需要异步触发 Qdrant Payload 的更新
            # if "kb_id" in data:
            #     current_doc = await sql_provider.get_record_by_id(doc_id)
            #     if current_doc.status == 100:
            #          await self.sync_kb_change_to_qdrant(doc_id, data["kb_id"])
            
            
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
            
            # 清理instruction chunks
            await self.instruction_datum_service.delete_by_doc_id(doc_id)
            
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
            
            # 6 删除第二步chunk生成的json
            
            
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
    
    
    async def get_kb_id_by_doc_id(self, doc_id: int) -> Optional[int]:
        """
        获取文档所属知识库 ID
        返回: int (kb_id) 或 None (如果不存在或未绑定)
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PdfDocument, sql_config_path=self.sql_config_path)
            doc = await sql_provider.get_record_by_id(doc_id)
            
            if doc:
                # 兼容处理：判断是 字典(dict) 还是 对象(Object)
                if isinstance(doc, dict):
                    return doc.get("kb_id")
                else:
                    # SQLAlchemy 对象使用属性访问
                    return getattr(doc, "kb_id", None)
            
            return None

        except Exception as e:
            self.logger.error(f"查询文档所属知识库失败: {e}")
            return None
        finally:
            if sql_provider: await sql_provider.close()
    
    
    async def get_doc_count_by_kb_id(self, kb_id: int) -> dict:
        """
        获取知识库文档统计信息
        返回格式: {"total": 100, "embedded": 80}
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PdfDocument, sql_config_path=self.sql_config_path)
            
            async with sql_provider.get_db_session() as session:
                # 1. 查询该知识库下的文档总数
                stmt_total = (
                    select(func.count(PdfDocument.id))
                    .where(PdfDocument.kb_id == kb_id)
                )
                
                # === 2. 查询已完成向量化的数量
                stmt_embedded = (
                    select(func.count(PdfDocument.id))
                    .join(PipelineTask, PdfDocument.id == PipelineTask.doc_id)
                    .where(PdfDocument.kb_id == kb_id)
                    .where(PipelineTask.task_type == TaskType.QDRANT_INDEX)
                    .where(PipelineTask.status == TaskLifecycle.SUCCESS)
                )

                # 并发执行查询
                total_result = await session.execute(stmt_total)
                embedded_result = await session.execute(stmt_embedded)
                
                total_count = total_result.scalar() or 0
                embedded_count = embedded_result.scalar() or 0
                progress = round(embedded_count / total_count, 2) if total_count > 0 else 0.0
                return {
                    "total": total_count,
                    "vectorized": embedded_count,
                    "progress": progress
                }

        except Exception as e:
            self.logger.error(f"查询统计失败: {e}")
            return {"total": 0, "vectorized": 0, "progress": 0.0}
        finally:
            if sql_provider: await sql_provider.close()
    
    
    async def get_unassigned_documents(
        self, 
        page: int = 1, 
        page_size: int = 20,
        keyword: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        获取未分配给任何知识库的文档列表 (kb_id IS NULL)
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PdfDocument, sql_config_path=self.sql_config_path)
            
            # 1. 核心条件：kb_id 必须为空
            condition = {"kb_id": None}
            
            # 2. 构造复杂查询 (关键词搜索)
            complex_filters = []
            if keyword:
                complex_filters.append(
                    PdfDocument.file_name.like(f"%{keyword}%")
                )
            
            # 3. 指定返回字段
            fields = ["id", "file_name", "file_size", "create_time", "status"]
            
            # 4. 分页查询
            result = await sql_provider.get_records_paginated(
                page=page,
                page_size=page_size,
                condition=condition, # 这里传入 kb_id=None
                filters=complex_filters,
                fields=fields,
                order_by=PdfDocument.create_time.desc()
            )
            
            # 5. 数据处理 (可选：格式化文件大小)
            for item in result['items']:
                size_bytes = item.get('file_size', 0)
                # 简单格式化 MB
                if size_bytes:
                    item['file_size_display'] = f"{round(size_bytes / (1024 * 1024), 1)} MB"
                else:
                    item['file_size_display'] = "0 MB"
                    
            return result
            
        except Exception as e:
            self.logger.error(f"查询未分配文档失败: {e}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()
    
    
    async def get_doc_count_by_kb_id_bake(self, kb_id: int) -> str:
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PdfDocument, sql_config_path=self.sql_config_path)
            # 1. 查询所有属于该知识库的文档
            docs = await sql_provider.get_record_by_condition({"kb_id": kb_id})
            return len(docs)
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