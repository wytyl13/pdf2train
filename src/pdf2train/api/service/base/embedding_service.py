#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/08 15:46
@Author  : weiyutao
@File    : embedding_service.py
"""

from datetime import datetime
import logging
import traceback
from typing import List, Dict, Any
from sqlalchemy import update
import math
import httpx
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from pdf2train.core.provider.sql_provider import SqlProvider
from pdf2train.api.schema.qdrant_schema import MetadataUpdateRequest, IngestRequest, EmbeddingConfigOverride

from pdf2train.core.table.document_chunk import DocumentChunk
from pdf2train.core.table.pipeline_task import TaskType, TaskLifecycle, IndexStatus, IndexTaskResult
from pdf2train.core.table.instruction_datum import InstructionDatum

from pdf2train.api.service.base.document_chunk_service import DocumentChunkService
from pdf2train.api.service.base.pdf_document_service import PdfDocumentService
from pdf2train.api.service.base.update_doc_to_kb_service import UpdateDocToKbService
from pdf2train.api.service.base.instruction_datum_service import InstructionDatumService
from pdf2train.api.service.base.pipeline_task_service import PipelineTaskService


class EmbeddingService:
    """
    语义向量化服务
    负责将 DocumentChunk 数据同步到 Wangeng 向量数据库
    """

    def __init__(
        self, 
        document_chunk_service: DocumentChunkService,
        pipeline_task_service: PipelineTaskService,
        pdf_document_service: PdfDocumentService,
        update_doc_to_kb_service: UpdateDocToKbService,
        instruction_datum_service: InstructionDatumService
    ):
        self.document_chunk_service = document_chunk_service
        self.pipeline_task_service = pipeline_task_service
        self.pdf_document_service = pdf_document_service
        self.update_doc_to_kb_service = update_doc_to_kb_service
        self.instruction_datum_service = instruction_datum_service
        self.logger = logging.getLogger(self.__class__.__name__)


    async def update_docs_to_kb(
        self, 
        metadata_update_request: MetadataUpdateRequest,
        update_sql: bool = True
    ) :
        """
        将一批文档关联到指定知识库
        """
        return await self.update_doc_to_kb_service.update_docs_to_kb(
            metadata_update_request=metadata_update_request,
            update_sql=update_sql
        )
        

    async def run_embedding_for_doc(
        self, 
        doc_id: int,
        embed_config_override: EmbeddingConfigOverride
    ) -> bool:
        """
        [核心入口] 为指定文档的所有 Chunk 执行向量化
        """
        self.logger.info(f"开始文档 Doc {doc_id} 的向量化任务...")
        
        try:
            # 0 获取task_id
            tasks = await self.pipeline_task_service.get_tasks_by_doc_id(doc_id)
            extract_task = next((t for t in tasks if t['task_type'] == TaskType.QDRANT_INDEX.value), None)
            if not extract_task: return []
            task_id = extract_task['id']
            
            # 1. 更新状态 DATA_PREPARING
            await self.pipeline_task_service.update_task_status(
                task_id=task_id,
                status=TaskLifecycle.RUNNING.value,
                detailed_status=IndexStatus.DATA_PREPARING.value,
                progress=IndexStatus.DATA_PREPARING.value,
            )
            
            # 2. 获取该文档的所有切片数据
            chunks = await self.document_chunk_service.export_chunks_as_ingest_chunks(doc_id)
            raw_chunks = await self.instruction_datum_service.export_instructions_as_ingest_chunks(doc_id=doc_id)
            chunks += raw_chunks
            total_chunks = len(chunks)
            
            # 3. 处理空数据并更新状态
            if total_chunks == 0:
                msg = f"Doc {doc_id} 无切片，直接标记成功"
                self.logger.warning(msg)
                await self.pipeline_task_service.update_task_status(
                    task_id,
                    status=TaskLifecycle.SUCCESS.value,
                    detailed_status=IndexStatus.SUCCESS.value,
                    progress=IndexStatus.SUCCESS.value
                )
                return True

            # 4. 状态初始化
            base_progress = 20
            await self.pipeline_task_service.update_task_status(
                task_id=task_id,
                status=TaskLifecycle.RUNNING.value,
                detailed_status=IndexStatus.BATCH_UPSERTING.value,
                progress=base_progress
            )
            processed_count = 0
            BATCH_SIZE = 128
            num_batches = math.ceil(total_chunks / BATCH_SIZE)
            
            checkpoints = [0.2, 0.4, 0.6, 0.8, 1.0]
            next_checkpoint_idx = 0
            
            for i in range(num_batches):
                start_idx = i * BATCH_SIZE
                end_idx = min((i + 1) * BATCH_SIZE, total_chunks)
                batch_chunks = chunks[start_idx:end_idx]
                
                # 5.1 处理批次 (构造Payload -> API -> 更新Chunk状态)
                success_count = await self._process_batch(
                    batch_chunks,
                    embed_config_override=embed_config_override
                )
                processed_count += success_count
                
                # 5.2 计算当前处理比例 (0.0 ~ 1.0)
                current_ratio = processed_count / total_chunks
                
                # 5.3 检查是否达到更新阈值
                # 只有当比率跨过 0.2, 0.4 等节点时才更新数据库
                if next_checkpoint_idx < len(checkpoints) and current_ratio >= checkpoints[next_checkpoint_idx]:
                    
                    # progress = 20 + ratio * 80
                    calc_progress = base_progress + int(current_ratio * 80)
                    
                    # 边界保护
                    calc_progress = max(20, min(100, calc_progress))
                    
                    # 实时更新数据库
                    runtime_data = {
                        "current_processed": processed_count, 
                        "total": total_chunks,
                        "ratio": f"{current_ratio:.2f}"
                    }
                    
                    await self.pipeline_task_service.update_task_status(
                        task_id=task_id,
                        status=TaskLifecycle.RUNNING.value,
                        detailed_status=IndexStatus.BATCH_UPSERTING.value,
                        progress=calc_progress,
                    )
                    
                    self.logger.info(f"Doc {doc_id} 进度更新: {calc_progress}% (Ratio: {current_ratio:.2f})")
                    
                    # 指向下一个检查点
                    next_checkpoint_idx += 1


            # 新增 更新qdrant绑定字段
            kb_id = await self.pdf_document_service.get_kb_id_by_doc_id(doc_id)
            if kb_id:
                metadata_update_request = MetadataUpdateRequest(
                    collection_name=embed_config_override.model_name,
                    filter_key="doc_kb_id",
                    filter_value=doc_id,
                    payload={"kb_id": kb_id}
                )
                await self.update_docs_to_kb(
                    metadata_update_request=metadata_update_request,
                    update_sql=False
                )
            
            # 6. 任务完成 (强制设为 100)
            final_result = IndexTaskResult(
                indexed_count=processed_count,
                doc_id=doc_id
            )
            await self.pipeline_task_service.update_task_status(
                task_id=task_id,
                status=TaskLifecycle.SUCCESS.value,
                detailed_status=IndexStatus.SUCCESS.value,
                progress=IndexStatus.SUCCESS.value,
                result_data=final_result.model_dump()
            )
            self.logger.info(f"Doc {doc_id} 向量化完成，共索引 {processed_count} 条")
            return True

        except Exception as e:
            self.logger.error(f"Doc {doc_id} 向量化失败: {str(e)}\n{traceback.format_exc()}")
            if task_id:
                await self.pipeline_task_service.update_task_status(
                    task_id=task_id,
                    status=TaskLifecycle.FAILED.value,
                    detailed_status=IndexStatus.FAILED.value,
                    error_message=str(e)
                )
            raise e
            

    def _build_payload(self, doc_id: int, batch_chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        [内部辅助] 将数据库原始数据转换为 Wangeng API 所需的 payload 格式
        """
        payload_chunks = []
        for chunk in batch_chunks:
            # 1. 提取核心内容，过滤空文本
            content = chunk.get("content")
            if not content or not content.strip():
                continue

            # 2. 构造元数据 (Metadata)
            # 从数据库读取原始元数据，若不存在则初始化为空字典
            meta_info = chunk.get("metadata", {}) or {} 
            # 3. 组装扁平化的向量库元数据
            vector_metadata = {
                "chunk_id": str(chunk["id"]),      # [核心] 用于保持 SQL 与 Qdrant 的 ID 强一致
                "doc_kb_id": doc_id,                  # 所属文档 ID
                "chunk_index": chunk.get("chunk_index", 0),
                "token_count": chunk.get("token_count", 0),
                **meta_info                         # 解包 H1, H2, file_name 等业务元数据
            }
            
            # 4. 补充页码信息 (如果有)
            if "page_numbers" in chunk:
                vector_metadata["page_numbers"] = chunk["page_numbers"]

            # 5. 组装成符合 Wangeng 接口定义的格式
            payload_chunks.append({
                "text": content,
                "metadata": vector_metadata
            })
            
        return payload_chunks


    async def call_vector_api(
        self,
        ingest_request: IngestRequest
    ):
        return await self.update_doc_to_kb_service.call_vector_api(ingest_request=ingest_request)


    @retry(
        stop=stop_after_attempt(3), 
        wait=wait_fixed(2),
        retry=retry_if_exception_type(httpx.HTTPError) # 只重试网络错误
    )
    async def update_metadata(
        self, 
        metadata_update_request: MetadataUpdateRequest
    ) -> int:
        """
        [独立抽取的 API 调用方法] 负责发送请求，包含重试逻辑
        """
        return await self.update_doc_to_kb_service.update_metadata(
            metadata_update_request=metadata_update_request
        )


    async def _process_batch(
        self, 
        batch_chunks: List[Dict],
        embed_config_override: EmbeddingConfigOverride
    ) -> int:
        """[内部方法] 处理单个批次：构造Payload -> 异步API调用 -> 批量更新DB状态"""
        # 1. 构造 Wangeng API 需要的 Payload
        payload = [
            c for c in batch_chunks 
            if c.get("text") and c.get("text").strip()
        ]
        
        if not payload:
            return 0
        # 2. 调用带有重试机制的异步 API 客户端
        await self.call_vector_api(
            ingest_request = IngestRequest(
                chunks=payload,
                embed_config=embed_config_override
            )
        )

        # 3. 批量将数据库中的 Chunk 标记为已索引
        chunk_ids = []
        for c in payload:
            metadata = c.get("metadata", {})
            # 兼容两种 key (防止 export 没写对)
            cid = metadata.get("chunk_id") or metadata.get("id")
            if cid:
                chunk_ids.append(str(cid))
        if chunk_ids:
            await self._mark_chunks_as_indexed(chunk_ids)
        
        return len(payload)


    async def _mark_chunks_as_indexed(self, chunk_ids: List[str]):
        """
        [内部辅助] 批量将 Chunk 标记为已索引
        """
        if not chunk_ids:
            return

        sql_provider = None
        try:
            sql_provider = SqlProvider(model=DocumentChunk)
            
            # 使用 SQLAlchemy Core 的 update 语句进行批量更新
            # update document_chunks set is_indexed=true, qdrant_point_id=id where id in (...)
            stmt = update(DocumentChunk).where(
                DocumentChunk.id.in_(chunk_ids)
            ).values(
                is_indexed=True,
                # 如果您的表结构里 qdrant_point_id 是必须的，这里可以赋值
                qdrant_point_id=DocumentChunk.id 
            )
            
            # 3. 构造 InstructionDatum 的更新语句
            # 注意：需要引入 InstructionDatum 模型
            stmt_inst = (
                update(InstructionDatum)
                .where(InstructionDatum.id.in_(chunk_ids))
                .values(
                    is_indexed=True,
                    qdrant_point_id=InstructionDatum.id,
                    update_time=datetime.now()
                )
            )
            async with sql_provider.get_db_session() as session:
                await session.execute(stmt)
                await session.execute(stmt_inst)
                await session.commit()
                
        except Exception as e:
            self.logger.error(f"更新索引状态失败: {e}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()