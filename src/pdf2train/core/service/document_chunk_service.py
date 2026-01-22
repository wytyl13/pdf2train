#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/24 13:26
@Author  : weiyutao
@File    : document_chunk_service.py
"""

import logging
import json
from typing import Dict, Any, List, Optional, AsyncGenerator
from sqlalchemy import text, and_, bindparam, select

# 导入数据库模型
from pdf2train.core.provider.sql_provider import SqlProvider
from pdf2train.api.schema.qdrant_schema import VectorDeleteRequest, IngestRequest

from pdf2train.core.table.pdf_document import PdfDocument
from pdf2train.core.table.document_chunk import DocumentChunk
from pdf2train.core.table.pipeline_task import TaskType, TaskLifecycle, ChunkStatus, ChunkTaskResult

from pdf2train.api.service.base.llm_config_service import LLMConfigService
from pdf2train.api.service.base.pipeline_task_service import PipelineTaskService
from pdf2train.api.service.base.update_doc_to_kb_service import UpdateDocToKbService

class DocumentChunkService:
    """
    文档切片 (Chunk) 业务服务
    负责 Chunk 的批量存储、列表查询、编辑以及状态管理
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
            sql_provider = SqlProvider(model=DocumentChunk)
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
            sql_provider = SqlProvider(model=DocumentChunk)
            
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

    async def export_chunks_as_ingest_chunks(self, doc_id: int) -> List[Dict[str, Any]]:
        """
        [向量化专用] 将文档原始切片导出为标准入库格式
        用于和 Instruction 数据合并后一同入库
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=DocumentChunk)
            
            # 1. 查询该文档所有切片，document_chunk表格没有is_valid这个字段
            stmt = text("""
                SELECT * FROM document_chunks 
                WHERE document_id = :doc_id 
                ORDER BY chunk_index ASC
            """)
            async with sql_provider.get_db_session() as session:
                result = await session.execute(stmt, {"doc_id": doc_id})
                rows = result.fetchall()
            
            ingest_list = []
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
            
        except Exception as e:
            self.logger.error(f"导出原始切片(Ingest格式)失败: {e}")
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
            sql_provider = SqlProvider(model=DocumentChunk)
            
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
        meta_info: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        [主入口] 更新切片信息
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=DocumentChunk)
            
            # --- 1. 使用封装好的接口查询当前状态 ---
            current_record = await sql_provider.get_record_by_id(record_id=chunk_id)
            
            if not current_record:
                self.logger.warning(f"更新失败: 找不到 Chunk {chunk_id}")
                return False

            # --- 2. 准备更新数据 ---
            db_update_data = {}
            if content is not None:
                db_update_data["content"] = content
                db_update_data["token_count"] = len(content)
            
            if meta_info is not None:
                db_update_data["meta_info"] = meta_info

            if not db_update_data:
                return False

            # --- 3. 执行数据库更新 ---
            await sql_provider.update_record(record_id=chunk_id, data=db_update_data)
            
            # --- 4. 准备同步用的"最终态数据" ---
            final_chunk_data = {**current_record, **db_update_data}
            
            # --- 5. 检查并执行同步 (根据数据库里的既定规则) ---
            current_indexed_status = final_chunk_data.get("is_indexed", False)
            if current_indexed_status is True:
                # 情况 B: 有效且需要索引 (is_indexed=True)
                await self._sync_single_chunk_to_kb(final_chunk_data)
            else:
                # --- C. 尝试删除向量数据
                await self._delete_chunk_vector(final_chunk_data)
            
            return True

        except Exception as e:
            self.logger.error(f"更新 Chunk 流程异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def _delete_chunk_vector(self, chunk_data: Dict[str, Any]):
        """[私有辅助] 从向量库中物理删除 Chunk"""
        try:
            doc_id = chunk_data.get("document_id")
            chunk_id = str(chunk_data.get("id"))
            
            collection_name = await self.update_doc_to_kb_service.get_collection_name_by_doc_id(doc_id)
            if not collection_name: return

            self.logger.info(f"Chunk {chunk_id} 标记为无效，正在清理向量...")
            
            await self.update_doc_to_kb_service.delete_vector(
                vector_delete_request=VectorDeleteRequest(
                    collection_name=collection_name,
                    filters={
                        "chunk_id": chunk_id,
                        "type": "document_chunk"
                    }
                )
            )
        except Exception as e:
            self.logger.error(f"Chunk 向量删除失败: {e}")

    async def _sync_single_chunk_to_kb(self, chunk_data: Dict[str, Any]):
        """
        [私有辅助函数] 将单个数据库切片数据(字典)，同步到向量知识库
        """
        try:
            # 注意：因为输入是字典，所以用 get 或 [] 访问
            doc_id = chunk_data.get("document_id")
            chunk_id = str(chunk_data.get("id"))
            
            # 1. 获取 Embedding 配置
            embedding_config_override = await self.llm_config_service.get_embedding_config_override(doc_id=doc_id)
            
            # 2. 准备基础数据
            raw_meta = chunk_data.get("meta_info", {})
            base_meta = raw_meta if isinstance(raw_meta, dict) else {} 
            
            # 3. 构造标准 Vector Metadata
            vector_metadata = {
                **base_meta,
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "doc_kb_id": doc_id,
                "filename": base_meta.get("filename", ""), 
                "chunk_index": chunk_data.get("chunk_index"),
                "type": "document_chunk",
                "is_instruction": False
            }

            # 4. 构造 Payload
            chunk_payload = {
                "text": chunk_data.get("content"), # 获取最新的 content
                "metadata": vector_metadata
            }
            
            # 5. 构造请求
            ingest_request = IngestRequest(
                chunks=[chunk_payload],
                embed_config=embedding_config_override
            )
            
            self.logger.info(f"🔄 同步 Chunk 到向量库: ID={chunk_id}, Doc={doc_id}")
            
            await self.update_doc_to_kb_service.call_vector_api(ingest_request=ingest_request)
            return True

        except Exception as e:
            self.logger.error(f"❌ 向量库同步失败 (Chunk {chunk_data.get('id')}): {str(e)}")
            return False

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
            sql_provider = SqlProvider(model=DocumentChunk)
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

            # 删除语义嵌入向量
            collection_name = await self.update_doc_to_kb_service.get_collection_name_by_doc_id(doc_id=doc_id)
            if not collection_name:
                self.logger.error(f"无法获取 doc_id={doc_id} 的 Collection Name，跳过向量删除")
            else:
                try:
                    # 物理删除指令数据嵌入qdrant数据    
                    await self.update_doc_to_kb_service.delete_vector(
                        vector_delete_request=VectorDeleteRequest(
                            collection_name=collection_name,
                            filter_key="chunk_id",
                            filter_value=chunk_id
                        )
                    )
                except Exception as ve:
                    self.logger.error(f"SQL已删，但向量删除失败: {ve}")

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

    async def get_chunk_index_by_chunk_id(self, chunk_id: str) -> Optional[int]:
        """
        [辅助方法] 根据 Chunk ID 快速反查 Document ID
        用于删除、校验或联动更新
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=DocumentChunk)
            result = await sql_provider.get_record_by_condition(
                condition={"id": chunk_id},
                fields=["chunk_index"]
            )
            if result:
                return result[0].get("chunk_index")
            else:
                return None
        except Exception as e:
            self.logger.error(f"查询 Document ID 失败 (Chunk {chunk_id}): {str(e)}")
            return None
        finally:
            if sql_provider: await sql_provider.close()

    async def get_document_id_by_chunk_id(self, chunk_id: str) -> Optional[int]:
        """
        [辅助方法] 根据 Chunk ID 快速反查 Document ID
        用于删除、校验或联动更新
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=DocumentChunk)
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

    async def get_doc_ids_by_kb_ids(self, kb_ids: List[int]) -> List[int]:
        """
        根据知识库 ID 列表 (kb_ids)，查询 pdf_documents 表，
        返回所有关联的 document_id 列表。
        """
        if not kb_ids:
            return []

        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PdfDocument)
            
            # 使用 IN 查询
            stmt = (
                select(PdfDocument.id)
                .where(PdfDocument.kb_id.in_(kb_ids))
            )
            async with sql_provider.get_db_session() as session:
                # 传入 tuple 以适配 IN 子句
                result = await session.execute(stmt)
                rows = result.fetchall()
            
            # 提取 id (rows 是 list of tuples, e.g. [(1,), (2,)])
            doc_ids = [row[0] for row in rows]
            
            self.logger.info(f"根据 KB IDs {kb_ids} 查找到 {len(doc_ids)} 个文档")
            return doc_ids

        except Exception as e:
            self.logger.error(f"查询 KB 文档列表失败: {str(e)}")
            return []
        finally:
            if sql_provider: await sql_provider.close()

    async def generate_pretrain_content_stream_by_kb_ids(self, kb_ids: List[int]):
        """
        [组合逻辑] 
        1. 先根据 kb_ids 查出所有 doc_ids
        2. 再调用底层的 doc_ids 流式生成器
        """
        # 1. 获取文档 ID 列表
        doc_ids = await self.get_doc_ids_by_kb_ids(kb_ids)
        
        if not doc_ids:
            self.logger.warning(f"KB IDs {kb_ids} 下未发现有效文档")
            yield "" 
            return
        # 2. 复用已有的 generate_pretrain_content_stream 方法
        async for chunk in self.generate_pretrain_content_stream(doc_ids):
            yield chunk

    async def generate_pretrain_content_stream(self, doc_ids: List[int]) -> AsyncGenerator[str, None]:
        """
        [流式生成器] 核心方法
        逐个查询文档，拼接内容，并 yield 给 HTTP 响应流。
        """
        ALLOWED_META_KEYS = {'filename', 'h1', 'h2', 'h3', 'h4', 'h5', 'length'}
        sql_provider = None
        try:
            # 初始化 SQL Provider
            sql_provider = SqlProvider(model=DocumentChunk)
            
            # 预编译 SQL 语句，提高性能
            stmt = text("""
                SELECT content, meta_info 
                FROM document_chunks 
                WHERE document_id = :doc_id 
                ORDER BY chunk_index ASC
            """)
            for doc_id in doc_ids:
                async with sql_provider.get_db_session() as session:
                    result = await session.execute(stmt, {"doc_id": doc_id})
                    rows = result.fetchall()

                if not rows:
                    continue

                chunk_contents = []
                # 用于存储从第一个 chunk 提取的原始 meta
                raw_base_meta = {}

                for i, row in enumerate(rows):
                    content_val = row[0]
                    meta_val = row[1]

                    # A. 收集文本
                    if content_val and content_val.strip():
                        chunk_contents.append(content_val)
                    
                    # B. 抓取第一个 chunk 的 meta_info 作为基准
                    # 通常文件名和 H1 标题在第一个切片中最准确
                    if i == 0 and meta_val and isinstance(meta_val, dict):
                        raw_base_meta = meta_val

                if not chunk_contents:
                    continue

                # 2. 拼接全文 (使用双换行分隔段落)
                full_text = "\n\n".join(chunk_contents)

                # 3. 过滤元数据
                # 只保留白名单中的 key，且值不为空
                final_meta = {
                    key: raw_base_meta[key]
                    for key in ALLOWED_META_KEYS
                    if key in raw_base_meta and raw_base_meta[key]
                }
                
                # 4. 组装 JSONL 行
                json_entry = {
                    "text": full_text,
                    "meta": final_meta
                }

                # 5. Yield JSON 字符串 (一行一条)
                yield json.dumps(json_entry, ensure_ascii=False) + "\n"

        except Exception as e:
            self.logger.error(f"流式生成预训练数据异常: {str(e)}")
            # 遇到错误生成一条带标记的数据，或者直接跳过
            error_entry = {
                "text": "",
                "meta": {"error": str(e), "doc_id": doc_id}
            }
            yield json.dumps(error_entry, ensure_ascii=False) + "\n"
            
        finally:
            if sql_provider:
                await sql_provider.close()
            
    async def delete_chunks_by_doc_id(self, doc_id: int) -> int:
        """
        根据文档ID 删除所有切片 (用于级联删除)
        [修改后] 使用 SqlProvider 的标准接口
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=DocumentChunk)
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
            
            collection_name = await self.update_doc_to_kb_service.get_collection_name_by_doc_id(doc_id=doc_id)
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
                                "type": "document_chunk"
                            }
                        )
                    )
                except Exception as ve:
                    self.logger.error(f"SQL已删，但向量删除失败: {ve}")
            
            return deleted_count
        except Exception as e:
            import traceback
            self.logger.error(f"删除 Chunks 异常: {str(e)} \n {traceback.format_exc()}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()