#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/25 18:57
@Author  : weiyutao
@File    : test_llm_instruction_gen_manager.py
"""

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import BackgroundTasks

from pdf2train.core.manager.instruction_gen_manager import InstructionGenManager
from pdf2train.core.table.pipeline_task import PipelineTask, TaskType
from pdf2train.core.table.pdf_document import PdfDocument
from pdf2train.core.table.llm_config import LLMConfig
from pdf2train.core.schema.instruction_datum_dto import InstructionDatumCoreDTO

# Import Services (We will mock these, but imports are needed for typing)
from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.core.service.document_chunk_service import DocumentChunkService
from pdf2train.core.service.instruction_datum_service import InstructionDatumService
from pdf2train.core.service.pipeline_task_service import PipelineTaskService
from pdf2train.core.service.llm_config_service import LLMConfigService
from pdf2train.core.service.qdrant_service import QdrantService
from pdf2train.tool.h1_context_assembler import H1ContextAssembler
from pdf2train.tool.instruction_llm_generator import InstructionLLMGenerator

from pdf2train.core.config import core_config

@pytest.mark.asyncio
class TestInstructionGenManager:

    @pytest.fixture
    def manager(self, mock_services):
        """Initialize Manager with Mocks"""
        return InstructionGenManager(
            pdf_document_service=PdfDocumentService(core_config.sql_config_test),
            document_chunk_service=DocumentChunkService(core_config.sql_config_test),
            instruction_datum_service=InstructionDatumService(core_config.sql_config_test),
            pipeline_task_service=PipelineTaskService(core_config.sql_config_test),
            llm_config_service=LLMConfigService(core_config.sql_config_test),
            qdrant_service=QdrantService(),
        )

    async def test_submit_task_validation_fail(self, manager: InstructionGenManager, mock_services):
        """Test: Submission fails if doc doesn't exist or has no chunks"""
        doc_id = 1
        bg_tasks = BackgroundTasks()

        # Case 1: Doc not found
        manager.pdf_document_service.get_by_id.return_value = None
        with pytest.raises(ValueError, match="不存在"):
            await manager.submit_instruction_task(doc_id, None, bg_tasks)

        # Case 2: Doc exists but no chunks
        manager.pdf_document_service.get_by_id.return_value = PdfDocument(id=doc_id)
        manager.document_chunk_service.get_counts_by_doc_ids.return_value = {doc_id: 0}
        
        with pytest.raises(ValueError, match="尚未切片"):
            await manager.submit_instruction_task(doc_id, None, bg_tasks)

    async def test_submit_task_success(self, manager: InstructionGenManager, mock_services):
        """Test: Submission successful and adds background task"""
        doc_id = 101
        bg_tasks = BackgroundTasks()
        
        # Setup Mocks
        manager.pdf_document_service.get_by_id.return_value = PdfDocument(id=doc_id)
        manager.document_chunk_service.get_counts_by_doc_ids.return_value = {doc_id: 50}
        manager.pipeline_task_service.get_specific_task_by_doc_id.return_value = PipelineTask(id=999)

        # Execute
        await manager.submit_instruction_task(doc_id, "DeepSeek-V3", bg_tasks)

        # Verify
        assert len(bg_tasks.tasks) == 1
        # Check if the correct function was added to background tasks
        func = bg_tasks.tasks[0].func
        assert func == manager.run_instruction_generation

    async def test_run_instruction_generation_flow(self, manager: InstructionGenManager, mock_services):
        """
        Test: Full generation flow
        1. Clean old data
        2. Get Chunks -> Assemble H1 -> Generate Instructions
        3. Save to DB -> Sync Vector -> Finish Task
        """
        doc_id = 202
        task_id = 888
        
        # --- 1. Mock Data Setup ---
        # Mock Raw Chunks
        raw_chunks = [
            {"id": "uuid-1", "chunk_index": 0, "content": "Intro..."},
            {"id": "uuid-2", "chunk_index": 1, "content": "Details..."}
        ]
        manager.document_chunk_service.export_chunks_json.return_value = raw_chunks
        
        # Mock Assembler Result (1 Chapter)
        manager.assembler.process.return_value = [
            {"h1_title": "Chapter 1", "prompt_text": "Full content of chapter 1..."}
        ]
        
        # Mock LLM Generator Result
        generated_item = {
            "id": str(uuid.uuid4()),
            "question": "What is X?",
            "answer": "X is Y.",
            "ref_chunk_ids": ["uuid-1"],
            "metadata": {"type": "Fact", "token_usage": 100}
        }
        manager.instruction_llm_generator.process_single_h1.return_value = [generated_item]
        
        # Mock DB Save Return
        manager.document_chunk_service.create_batch.return_value = True
        
        # Mock Ingest Data Export
        manager.instruction_datum_service.export_valid_instructions_for_ingest.return_value = [generated_item]
        
        # Mock Config Return
        manager.llm_config_service.get_config_by_doc_id.return_value = LLMConfig(model_name="GPT-4")

        # --- 2. Execute ---
        await manager.run_instruction_generation(doc_id, task_id, "DeepSeek-Config")

        # --- 3. Verify Interactions ---
        
        # Step 1: Status Updates (Running)
        assert manager.pipeline_task_service.update.call_count >= 2 # Init + Loop update
        
        # Step 2: Clean Old Data
        manager.instruction_datum_service.delete_by_doc_id.assert_called_once_with(doc_id=doc_id)
        
        # Step 3: Assembler Called
        manager.assembler.process.assert_called_once_with(raw_chunks)
        
        # Step 4: Generator Called
        manager.instruction_llm_generator.process_single_h1.assert_awaited_once()
        
        # Step 5: Batch Save Called
        # Verify DTO conversion happened (check the first arg of the first call)
        save_call_args = manager.instruction_datum_service.create_batch.call_args[0][0]
        assert len(save_call_args) == 1
        assert isinstance(save_call_args[0], InstructionDatumCoreDTO)
        assert save_call_args[0].h1_title == "Chapter 1"
        assert save_call_args[0].chunk_index_description == ["chunk 1"] # Index 0 -> chunk 1
        
        # Step 6: Vector Sync Called
        manager.qdrant_service.ingest_data_list.assert_awaited_once()
        manager.instruction_datum_service.mark_as_indexed.assert_awaited_once_with(doc_id)
        
        # Step 7: Finish Task
        # Check the final status update
        finish_call_args = manager.pipeline_task_service.update.call_args_list[-1]
        update_dto = finish_call_args[0][1] # Get the DTO argument
        assert update_dto.status == TaskLifecycle.SUCCESS.value
        assert update_dto.progress == 100
        assert update_dto.result_data["total_count"] == 1
        
        # Step 8: Next Step Activated
        mock_services["task_service"].activate_next_step.assert_awaited_once_with(
            doc_id=doc_id, current_step_order=3
        )
        
        print("[Success] Instruction Gen Flow Verified")

    async def test_run_instruction_gen_failure(self, manager, mock_services):
        """Test: Task should fail gracefully if exception occurs"""
        doc_id = 303
        task_id = 777
        
        # Mock Failure at Assembler Step
        mock_services["chunk_service"].export_chunks_json.side_effect = Exception("DB Connection Lost")
        
        # Execute
        await manager.run_instruction_generation(doc_id, task_id)
        
        # Verify Task Marked as Failed
        calls = mock_services["task_service"].update.call_args_list
        last_update = calls[-1][0][1] # PipelineTaskUpdateDTO
        
        assert last_update.status == TaskLifecycle.FAILED.value
        assert "DB Connection Lost" in last_update.error_message