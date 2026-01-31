#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/27 22:10
@Author  : weiyutao
@File    : qdrant_manager.py
"""

import math
import logging
import traceback
from typing import Dict, Any, List
from fastapi import BackgroundTasks
from datetime import datetime

from pdf2train.core.schema.qdrant_dto import EmbeddingTaskDTO, IngestBatchDTO, MetadataUpdateDTO
from pdf2train.core.schema.qdrant_dto import QdrantPayloadUpdateDTO
from pdf2train.core.schema.qdrant_dto import IngestRequest, EmbeddingConfigOverride

from pdf2train.core.table.pdf_document import PdfDocument
from pdf2train.core.table.pipeline_task import PipelineTask, TaskLifecycle, TaskType, InstructionStatus, IndexStatus
from pdf2train.core.schema.pipeline_task_dto import PipelineTaskUpdateDTO

# Services
from pdf2train.core.service.embedding_sql_service import EmbeddingSqlService
from pdf2train.core.service.qdrant_service import QdrantService
from pdf2train.core.service.document_chunk_service import DocumentChunkService
from pdf2train.core.service.pipeline_task_service import PipelineTaskService
from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.core.service.instruction_datum_service import InstructionDatumService
from pdf2train.core.service.llm_config_service import LLMConfigService

from pdf2train.core.table.pipeline_task import TaskType, TaskLifecycle, IndexStatus, IndexTaskResult

class QdrantManager:
    def __init__(
        self,
        embedding_sql_service: EmbeddingSqlService,
        document_chunk_service: DocumentChunkService,
        pipeline_task_service: PipelineTaskService,
        pdf_document_service: PdfDocumentService,
        instruction_datum_service: InstructionDatumService,
        llm_config_service: LLMConfigService,
        qdrant_service: QdrantService
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.embedding_sql_service = embedding_sql_service
        self.document_chunk_service = document_chunk_service
        self.pipeline_task_service = pipeline_task_service
        self.pdf_document_service = pdf_document_service
        self.instruction_datum_service = instruction_datum_service
        self.llm_config_service = llm_config_service
        self.qdrant_service = qdrant_service

    async def validate_and_init_task(
        self, 
        doc_id: int,
    ) -> int:
        """
        [Sync Phase] 仅负责校验和初始化数据库状态
        返回 task_id 供 Router 使用
        执行嵌入步骤的前提是已经完成了chunk和instruction gen步骤
        """
        try:
            # 1. 校验文档
            doc: PdfDocument = await self.pdf_document_service.get_by_id(doc_id)
            if not doc: raise ValueError(f"文档 {doc_id} 不存在")

            # 2. 校验切片
            counts_chunks: Dict[int, int] = await self.document_chunk_service.get_counts_by_doc_ids([doc_id])
            if counts_chunks.get(doc_id, 0) == 0: raise ValueError("尚未生成语义块数据！")

            # 3. 校验指令数据
            counts_instructions: Dict[int, int] = await self.instruction_datum_service.get_counts_by_doc_ids([doc_id])
            if counts_instructions.get(doc_id, 0) == 0: raise ValueError("尚未生成指令数据！")

            # 3. 获取并更新任务状态
            task: PipelineTask = await self.pipeline_task_service.get_specific_task_by_doc_id(
                doc_id=doc_id, 
                task_type_val=TaskType.QDRANT_INDEX.value
            )
            if not task: raise ValueError("Task未初始化")
            
            # 更新状态为“提交中/等待执行”
            # 在执行任务前更新task和pdf_document表格的状态
            await self.pipeline_task_service.update_and_refresh_parent_doc_status(
                task.id,
                PipelineTaskUpdateDTO(
                    status=TaskLifecycle.RUNNING.value,
                    detailed_status=IndexStatus.DATA_PREPARING.value,
                    progress=IndexStatus.DATA_PREPARING.value,
                    error_message="",
                    start_time=datetime.now()
                )
            )
            return task.id
        except Exception as e:
            raise ValueError("校验失败！{str(e)}") from e

    async def submit_embedding_task(
        self, 
        dto: EmbeddingTaskDTO, 
        task_id: int,
        background_tasks: BackgroundTasks,
    ) -> Dict[str, Any]:
        """
        语义嵌入操作步骤的执行
        """
        try:
            # 1. 验证文档是否存在
            doc_exists: PdfDocument = await self.pdf_document_service.get_by_id(dto.doc_id)
            if not doc_exists: raise ValueError(f"文档 {dto.doc_id} 不存在")

            # 2. 获取配置
            embedding_config: EmbeddingConfigOverride = await self.llm_config_service.get_embedding_config_override(doc_id=dto.doc_id)

            # 3. 异步执行
            background_tasks.add_task(
                self._safe_run_embedding_logic,
                doc_id=dto.doc_id,
                task_id=task_id,
                embed_config=embedding_config
            )
            return {"doc_id": dto.doc_id, "status": "processing"}
        except Exception as e:
            import traceback
            raise ValueError(f"语义嵌入步骤执行失败！{str(e)} \n {traceback.format_exc()}") from e

    async def _safe_run_embedding_logic(
        self, 
        doc_id: int, 
        task_id: int,
        embed_config: EmbeddingConfigOverride
    ):
        """向量化任务执行"""
        try:
            assert task_id is not None
            # 1. 准备数据，仅嵌入索引状态为false的
            chunks = await self.document_chunk_service.export_chunks_as_ingest_chunks(doc_id, True)
            instructions = await self.instruction_datum_service.export_instructions_as_ingest_chunks(doc_id, True)
            all_data = chunks + instructions
            total_count = len(all_data)

            if total_count == 0: raise ValueError("该文档没有需要嵌入的数据！")

            # 2. 批处理开始-更新任务状态
            await self.pipeline_task_service.update(
                task_id=task_id, 
                dto=PipelineTaskUpdateDTO(
                    detailed_status=IndexStatus.BATCH_UPSERTING.value,
                    progress=IndexStatus.BATCH_UPSERTING.value
                )
            )
            
            BATCH_SIZE = 32
            processed_count = 0
            num_batches = math.ceil(total_count / BATCH_SIZE)

            for i in range(num_batches):
                batch = all_data[i*BATCH_SIZE : (i+1)*BATCH_SIZE]
                
                # 2.1 调用远程向量库 (Service)
                ingest_req = IngestRequest(chunks=batch, embed_config=embed_config)
                await self.qdrant_service.ingest_api(ingest_req)

                # 2.2 更新本地数据库状态 (Service)
                chunk_ids = [str(c['metadata'].get('chunk_id') or c['metadata'].get('id')) for c in batch]
                await self.embedding_sql_service.mark_chunks_as_indexed(chunk_ids)

                # 2.3 更新进度
                processed_count += len(batch)
                if task_id and i % 5 == 0:
                    progress = 20 + int((processed_count / total_count) * 80)
                    await self.pipeline_task_service.update(
                        task_id=task_id, 
                        dto=PipelineTaskUpdateDTO(
                            progress=min(99, progress)
                        )
                    )

            # 3. 关联知识库 (Post-Processing)
            kb_id = await self.pdf_document_service.get_kb_id_by_doc_id(doc_id)
            if kb_id:
                payload_dto = QdrantPayloadUpdateDTO(
                    collection_name=embed_config.model_name,
                    filter_key="doc_kb_id",
                    filter_value=doc_id,
                    payload={"kb_id": kb_id}
                )
                await self.qdrant_service.update_kb_id_in_payload(payload_dto)

            # 4. 完成
            result_data = IndexTaskResult(indexed_count=processed_count, doc_id=doc_id)
            await self.pipeline_task_service.update_and_refresh_parent_doc_status(
                task_id=task_id,
                update_dto=PipelineTaskUpdateDTO(
                    status=TaskLifecycle.SUCCESS.value,
                    detailed_status=IndexStatus.SUCCESS.value,
                    progress=100,
                    result_data=result_data.model_dump(),
                    end_time=datetime.now()
                )
            )
        except Exception as e:
            import traceback
            error_info = f"Doc {doc_id} 向量化任务执行失败！:{str(e)} \n{traceback.format_exc()}"
            self.logger.error(error_info)
            if task_id:
                await self.pipeline_task_service.update_and_refresh_parent_doc_status(
                    task_id=task_id,
                    update_dto=PipelineTaskUpdateDTO(
                        status=TaskLifecycle.FAILED.value,
                        detailed_status=IndexStatus.FAILED.value,
                        error_message=str(e)
                    )
                )
            raise ValueError(error_info)

    async def update_metadata(self, dto: MetadataUpdateDTO) -> bool:
        """
        更新qdrant元数据
        1、先更新元数据
        2、再更新对应的数据库
        """
        try:
            # 1. 获取 Collection Name (如果 Router 没传，需要反查)
            if not dto.collection_name:
                first_doc_id = dto.doc_ids[0]
                model_name = await self.llm_config_service.get_collection_name_by_doc_id(first_doc_id)
                if not model_name: raise ValueError(f"无法找到文档 {first_doc_id} 对应的向量集合")
                dto.collection_name = model_name

            # 2. 更新 Qdrant (原子操作)
            payload_dto = QdrantPayloadUpdateDTO(
                collection_name=dto.collection_name,
                filter_key="doc_id", # 或者 doc_kb_id，取决于你的 Qdrant schema
                filter_value=dto.doc_ids,
                payload={"kb_id": dto.kb_id}
            )
            await self.qdrant_service.update_kb_id_in_payload(payload_dto)
            
            # 3. 更新 SQL (原子操作)
            return await self.pdf_document_service.update_kb_by_ids(dto.doc_ids, dto.kb_id)
        except Exception as e:
            self.logger.error(f"元数据更新失败: {e}")
            raise e

    async def ingest(self, dto: IngestBatchDTO) -> int:
        """单独的ingest语义嵌入操作"""
        try:
            req = IngestRequest(
                chunks=dto.chunks,
                embed_config=EmbeddingConfigOverride(model_name=dto.embedding_model)
            )
            return await self.qdrant_service.ingest_api(req)
        except Exception as e:
            self.logger.error(f"批量入库失败: {e}")
            raise e