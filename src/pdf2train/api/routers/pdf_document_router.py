#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/19 15:41
@Author  : weiyutao
@File    : pdf_document_router.py
"""
from fastapi import APIRouter, Query, Depends, UploadFile, File, BackgroundTasks
from fastapi.responses import StreamingResponse
from typing import Dict, Union, List
from io import StringIO

from pdf2train.core.table.pdf_document import PdfDocument
from pdf2train.api.schema.pdf_document_schema import (
    PdfDocCreateReq, PdfDocUpdateReq, DocListReq, PdfDocContentSaveReq,
    PaginatedDocRes, PdfDocDeleteReq, UnassignedReq,
    PdfDocExportBooksReq, PdfDocCountByKbReq
)
from pdf2train.api.schema.knowledge_base_schema import RelationAction
from pdf2train.core.schema.base_schema import PageResult
from pdf2train.core.schema.pdf_document_dto import (
    PdfDocUpdateDTO, PdfDocFilterDTO, PdfDocRichDTO
)
from pdf2train.core.schema.qdrant_dto import VectorDeleteRequest
from pdf2train.core.manager.qdrant_manager import QdrantManager
from pdf2train.core.manager.knowledge_base_manager import KnowledgeBaseManager
from pdf2train.api.dependencies import get_pdf_manager, get_doc_relation_biz, get_qdrant_manager, get_knowledge_base_manager
from pdf2train.core.manager.pdf_document_manager import PdfDocumentManager
from pdf2train.core.business.doc_relation_biz import DocRelationBiz, KBUpdateDocsReqDTO
from pdf2train.utils.response import make_response
from pdf2train.core.schema.qdrant_dto import EmbeddingTaskDTO, QdrantPayloadUpdateDTO, VectorDeleteRequest

router = APIRouter(prefix="/api/pdf_document", tags=["PDF Document"])


@router.post("/upload")
async def upload_document(
    meta: PdfDocCreateReq = Depends(),
    file: UploadFile = File(...),
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """
    上传文档
    """
    try:
        doc: PdfDocument = await manager.upload_and_create(
            file=file,
            kb_id=meta.kb_id
        )
        print("======================================================")
        print(doc.h_title_llm_config_id)
        print(doc.instruction_gen_llm_config_id)
        print(doc.embedding_llm_config_id)
        print("======================================================")
        if any([meta.author, meta.original_title, meta.summary, meta.instruction_gen_llm_config_id]):
            update_dto = PdfDocUpdateDTO(
                author=meta.author,
                original_title=meta.original_title,
                summary=meta.summary,
                instruction_gen_llm_config_id=getattr(meta, 'instruction_gen_llm_config_id', None),
                h_title_llm_config_id=getattr(meta, 'h_title_llm_config_id', None),
                embedding_llm_config_id=getattr(meta, 'embedding_llm_config_id', None)
            )
            # 这里的 doc 是 upload_and_create 返回的完整对象或字典，取 id 进行更新
            doc_id = doc.id if hasattr(doc, 'id') else doc['id']
            await manager.update(doc_id, update_dto)
        return make_response(True, "上传成功！", doc)
    except Exception as e:
        import traceback
        return make_response(False, f"上传失败！\n {str(e)} \n {traceback.format_exc()}", code=500)
    
@router.post("/list", response_model=dict)
async def list_docs(
    req: DocListReq,
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """分页查询文档列表"""
    
    try:
        dto_data_res: PageResult[PdfDocRichDTO] = await manager.get_list_documents(
                req.page, 
                req.page_size,
                PdfDocFilterDTO(**req.model_dump(exclude_unset=True)), 
            )
        return make_response(success=True, message="查询成功", data=dto_data_res)
    except Exception as e:
            import traceback
            return make_response(False, f"查询失败！\n {str(e)} \n {traceback.format_exc()}", code=500)
    
@router.post("/update_bake", response_model=dict)
async def update_doc_bake(
    req: PdfDocUpdateReq,
    background_tasks: BackgroundTasks,
    manager: PdfDocumentManager = Depends(get_pdf_manager),
    relation_biz: DocRelationBiz = Depends(get_doc_relation_biz)
):
    """更新文档元数据"""
    try:
        # 1. 获取当前文档状态 (用于比对)
        current_doc: PdfDocument = await manager.pdf_service.get_by_id(req.id)
        if not current_doc:
            return make_response(False, "文档不存在", code=404)
        
        # 2. 预处理 req 数据
        req_data = req.model_dump(exclude_unset=True)
        
        has_kb_change = False
        new_kb_id = None
        if "kb_id" in req_data:
            new_kb_id = req_data["kb_id"]
            old_kb_id = current_doc.kb_id
            # 注意：数据库取出的 None 和前端传的 0 视为相等（都是无 KB）
            val_old = old_kb_id if old_kb_id else 0
            val_new = new_kb_id if new_kb_id else 0
            if val_old != val_new:
                has_kb_change = True
        
        
        # 检测模型变更
        old_model_id = current_doc.embedding_llm_config_id
        new_model_id = req_data.get("embedding_llm_config_id")
        has_model_change = "embedding_llm_config_id" in req_data and old_model_id != new_model_id
        
        # C. 判断是否正在解绑 (本次请求显式将 kb_id 设为 0 或 None)
        is_unbinding_now = has_kb_change and (new_kb_id is None or new_kb_id == 0)
        
        # 防御性拦截逻辑
        is_currently_bound = current_doc.kb_id is not None and current_doc.kb_id > 0
        if is_currently_bound and has_model_change and not is_unbinding_now:
            # 直接报错，阻止后续所有操作
            return make_response(
                False, 
                "操作被拒绝：该文档当前已绑定知识库，不允许直接修改嵌入模型。\n\n请先【解绑知识库】，然后再修改模型。", 
                code=400
            )
        # 4. 执行基础信息更新
        update_exclude_fields = {"id", "confirm_sync", "kb_id"}
        dto_data = req.model_dump(exclude_unset=True, exclude=update_exclude_fields)
        if dto_data:
            dto = PdfDocUpdateDTO(**dto_data)
            await manager.pdf_service.update(req.id, dto)
        
        # 5. 判断是否需要触发“关系/重算逻辑”
        should_trigger_biz = False
        target_kb_id = None
        action = RelationAction.BIND
        
        if has_kb_change:
            should_trigger_biz = True
            # 处理解绑逻辑: None 或 0 都视为解绑
            if new_kb_id is None or new_kb_id == 0:
                action = RelationAction.UNBIND
                target_kb_id = current_doc.kb_id
            else:
                action = RelationAction.BIND
                target_kb_id = new_kb_id
        
        elif has_model_change and current_doc.kb_id:
            # 场景：用户没改知识库，但改了模型，且文档当前就在知识库里
            # 这相当于：对“当前知识库”做一次“重新绑定检查”
            should_trigger_biz = True
            action = RelationAction.BIND
            target_kb_id = current_doc.kb_id

        # 6. 执行复杂业务逻辑
        if should_trigger_biz:
            delegate_req = KBUpdateDocsReqDTO(
                kb_id=target_kb_id,
                doc_ids=[req.id],
                action=action,
                force=req.confirm_sync  # 透传前端的确认
            )

            # 调用 Biz 层
            result = await relation_biz.process_relation_update(delegate_req, background_tasks)

            # 处理 Biz 层返回的状态
            if result["status"] == "CONFIRM_REQUIRED":
                # 返回 success=True，但带上特殊 code，前端弹窗
                return make_response(success=True, message=result["msg"], data=result["data"])
            return make_response(True, message=result["msg"], data=result["data"])

        return make_response(True, "更新成功")
    except Exception as e:
        import traceback
        return make_response(False, f"更新失败！{str(e)} \n {traceback.format_exc()}", code=500)
    
@router.post("/update", response_model=dict)
async def update_doc(
    req: PdfDocUpdateReq,
    background_tasks: BackgroundTasks,
    manager: PdfDocumentManager = Depends(get_pdf_manager),
    kb_manager: KnowledgeBaseManager = Depends(get_knowledge_base_manager),
    relation_biz: DocRelationBiz = Depends(get_doc_relation_biz),
    qdrant_manager: QdrantManager = Depends(get_qdrant_manager)
):
    """
    更新文档元数据

    处理文档更新的核心路由，支持以下场景：

    **场景分类：**
    1. **仅修改模型 + 文档已绑定知识库** → 拒绝操作（必须先解绑知识库）
    2. **仅修改模型 + 文档未绑定知识库** → 删除旧向量并重新嵌入
    3. **仅修改知识库** → 调用 process_relation_update 处理绑定/解绑逻辑
    4. **同时修改模型和知识库：**
       - 4a. 解绑知识库（new_kb_id=None/0）→ 先重新嵌入，再通过 process_relation_update 解绑
       - 4b. 绑定/更换知识库 → 验证模型匹配后，调用 process_relation_update
    5. **仅修改元数据** → 直接更新数据库

    **参数说明：**
    - req.embedding_llm_config_id: 新的嵌入模型ID
    - req.kb_id: 新的知识库ID（None/0表示解绑）
    - req.confirm_sync: 是否强制执行（跳过风险确认）
    """
    try:
        # ========== 1. 获取当前文档状态 ==========
        current_doc: PdfDocument = await manager.pdf_service.get_by_id(req.id)
        if not current_doc:
            return make_response(False, "文档不存在", code=404)

        req_data = req.model_dump(exclude_unset=True)

        # ========== 2. 变更检测 ==========
        # 2.1 检测知识库变更
        has_kb_change = False
        new_kb_id = None
        if "kb_id" in req_data:
            new_kb_id = req_data["kb_id"]
            old_kb_id = current_doc.kb_id
            # 将 None 和 0 视为等价（都表示未绑定）
            val_old = old_kb_id if old_kb_id else 0
            val_new = new_kb_id if new_kb_id else 0
            has_kb_change = (val_old != val_new)

        # 2.2 检测模型变更
        old_model_id = current_doc.embedding_llm_config_id
        new_model_id = req_data.get("embedding_llm_config_id")
        has_model_change = ("embedding_llm_config_id" in req_data and old_model_id != new_model_id)

        # 2.3 判断是否为解绑操作
        is_unbinding = has_kb_change and (new_kb_id is None or new_kb_id == 0)

        # ========== 3. 更新基础元数据（非关键字段）==========
        update_exclude_fields = {"id", "confirm_sync", "kb_id", "embedding_llm_config_id"}
        dto_data = req.model_dump(exclude_unset=True, exclude=update_exclude_fields)
        if dto_data:
            dto = PdfDocUpdateDTO(**dto_data)
            await manager.pdf_service.update(req.id, dto)

        # ========== 4. 核心业务逻辑分支 ==========

        # --- 场景 1 & 2: 仅修改模型（未修改知识库）---
        if has_model_change and not has_kb_change:
            # 场景 1: 文档当前已绑定知识库 → 拒绝操作
            if current_doc.kb_id and current_doc.kb_id > 0:
                return make_response(
                    False,
                    "操作被拒绝：文档已绑定知识库，不允许直接修改嵌入模型。\n"
                    "请先【解绑知识库】，然后再修改模型。",
                    code=400
                )

            # 场景 2: 文档未绑定知识库 → 删除旧向量并重新嵌入
            # 检查是否需要用户确认（如果文档已有向量数据）
            if not req.confirm_sync:
                # 检查文档是否已完成嵌入
                indexed_docs = await kb_manager.get_indexed_docs([req.id])
                if indexed_docs:
                    # 统计需要重新嵌入的数据量
                    chunks_count_map = await manager.document_chunk_service.get_indexed_counts_by_doc_ids([req.id])
                    qa_count_map = await manager.instruction_datum_service.get_indexed_counts_by_doc_ids([req.id])
                    total_chunks = sum(chunks_count_map.values())
                    total_qas = sum(qa_count_map.values())
                    total_impact = total_chunks + total_qas

                    if total_impact > 0:
                        return make_response(
                            success=True,
                            message="需要用户确认",
                            data={
                                "code": "CONFIRM_REQUIRED",
                                "warning_msg": f"修改嵌入模型将删除现有向量数据并重新嵌入。\n"
                                             f"影响范围：**{total_chunks} 条切片数据和 {total_qas} 条 QA 数据**。\n"
                                             f"是否继续？",
                                "affected_counts": total_impact,
                                "chunks_count": total_chunks,
                                "qa_count": total_qas
                            }
                        )

            # 获取旧模型对应的 collection_name
            old_collection_name = None
            if old_model_id:
                print(f"[DEBUG] 场景2: 开始获取旧模型的 collection_name, old_model_id={old_model_id}")
                collection_map = await manager.get_collection_names_by_doc_ids([req.id])
                old_collection_name = collection_map.get(req.id)
                print(f"[DEBUG] 场景2: 获取到 old_collection_name={old_collection_name}")

            # 删除旧向量（如果存在）
            if old_collection_name:
                try:
                    print(f"[DEBUG] 场景2: 开始删除旧向量, collection={old_collection_name}, doc_id={req.id}")
                    await qdrant_manager.delete_vectors(VectorDeleteRequest(
                        collection_name=old_collection_name,
                        filter_key="doc_kb_id",
                        filter_value=req.id
                    ))
                    print(f"[DEBUG] 场景2: 旧向量删除成功")
                except Exception as e:
                    # 向量删除失败不阻断流程（可能向量不存在）
                    print(f"[DEBUG] 场景2: 旧向量删除失败: {str(e)}")
                    pass
            else:
                print(f"[DEBUG] 场景2: 未找到 old_collection_name，跳过删除向量")

            # 重置嵌入配置并触发重新向量化
            await manager.reset_embedding_llm_config_id(
                doc_ids=[req.id],
                embedding_llm_config_id=new_model_id,
                kb_id=None  # 保持未绑定状态
            )

            # 提交后台嵌入任务
            await qdrant_manager.submit_embedding_task(
                dto=EmbeddingTaskDTO(doc_id=req.id),
                task_id=None,
                background_tasks=background_tasks
            )

            return make_response(True, "模型已更新，正在后台重新向量化")

        # --- 场景 3: 仅修改知识库（未修改模型）---
        elif has_kb_change and not has_model_change:
            # 确定操作类型和目标知识库
            if is_unbinding:
                action = RelationAction.UNBIND
                target_kb_id = current_doc.kb_id or 0
                # 特殊情况：文档本来就未绑定，无需解绑
                if target_kb_id == 0:
                    return make_response(True, "更新成功（文档本就未绑定知识库）")
            else:
                action = RelationAction.BIND
                target_kb_id = new_kb_id

            # 调用业务层处理关联/解绑逻辑
            delegate_req = KBUpdateDocsReqDTO(
                kb_id=target_kb_id,
                doc_ids=[req.id],
                action=action,
                force=req.confirm_sync
            )

            result = await relation_biz.process_relation_update(delegate_req, background_tasks)

            # 处理业务层返回结果
            if result["status"] == "CONFIRM_REQUIRED":
                return make_response(success=True, message=result["msg"], data=result["data"])
            elif result["status"] == "ERROR":
                # 解绑失败时的降级处理
                if action == RelationAction.UNBIND:
                    await manager.pdf_service.update_kb_by_ids([req.id], None)
                    return make_response(True, "强制解绑成功")
                return make_response(False, result["msg"], code=result.get("code", 400))

            return make_response(True, message=result["msg"], data=result.get("data"))

        # --- 场景 4: 同时修改模型和知识库 ---
        elif has_model_change and has_kb_change:
            # 场景 4a: 解绑知识库 + 修改模型
            if is_unbinding:
                # 检查是否需要用户确认（如果文档已有向量数据）
                if not req.confirm_sync:
                    indexed_docs = await kb_manager.get_indexed_docs([req.id])
                    if indexed_docs:
                        # 统计需要重新嵌入的数据量
                        chunks_count_map = await manager.document_chunk_service.get_indexed_counts_by_doc_ids([req.id])
                        qa_count_map = await manager.instruction_datum_service.get_indexed_counts_by_doc_ids([req.id])
                        total_chunks = sum(chunks_count_map.values())
                        total_qas = sum(qa_count_map.values())
                        total_impact = total_chunks + total_qas

                        if total_impact > 0:
                            return make_response(
                                success=True,
                                message="需要用户确认",
                                data={
                                    "code": "CONFIRM_REQUIRED",
                                    "warning_msg": f"解绑知识库并修改嵌入模型将删除现有向量数据并重新嵌入。\n"
                                                 f"影响范围：**{total_chunks} 条切片数据和 {total_qas} 条 QA 数据**。\n"
                                                 f"是否继续？",
                                    "affected_counts": total_impact,
                                    "chunks_count": total_chunks,
                                    "qa_count": total_qas
                                }
                            )

                # 步骤 1: 先删除旧向量并重新嵌入新模型
                # 获取旧模型对应的 collection_name
                old_collection_name = None
                if old_model_id:
                    print(f"[DEBUG] 场景4a: 开始获取旧模型的 collection_name, old_model_id={old_model_id}")
                    collection_map = await manager.get_collection_names_by_doc_ids([req.id])
                    old_collection_name = collection_map.get(req.id)
                    print(f"[DEBUG] 场景4a: 获取到 old_collection_name={old_collection_name}")

                # 删除旧向量（如果存在）
                if old_collection_name:
                    try:
                        print(f"[DEBUG] 场景4a: 开始删除旧向量, collection={old_collection_name}, doc_id={req.id}")
                        await qdrant_manager.delete_vectors(VectorDeleteRequest(
                            collection_name=old_collection_name,
                            filter_key="doc_kb_id",  # ✅ 后端接口只承认 doc_kb_id
                            filter_value=req.id
                        ))
                        print(f"[DEBUG] 场景4a: 旧向量删除成功")
                    except Exception as e:
                        print(f"[DEBUG] 场景4a: 旧向量删除失败: {str(e)}")
                        pass
                else:
                    print(f"[DEBUG] 场景4a: 未找到 old_collection_name，跳过删除向量")

                # 重置为新模型（此时 kb_id 设为 None）
                await manager.reset_embedding_llm_config_id(
                    doc_ids=[req.id],
                    embedding_llm_config_id=new_model_id,
                    kb_id=None
                )

                # 提交嵌入任务
                await qdrant_manager.submit_embedding_task(
                    dto=EmbeddingTaskDTO(doc_id=req.id),
                    task_id=None,
                    background_tasks=background_tasks
                )

                # 步骤 2: 通过业务层处理解绑逻辑（更新 Qdrant payload）
                if current_doc.kb_id and current_doc.kb_id > 0:
                    delegate_req = KBUpdateDocsReqDTO(
                        kb_id=current_doc.kb_id,
                        doc_ids=[req.id],
                        action=RelationAction.UNBIND,
                        force=True  # 强制执行，因为已经重新嵌入
                    )
                    await relation_biz.process_relation_update(delegate_req, background_tasks)

                return make_response(True, "已解绑知识库并切换模型，正在后台重新向量化")

            # 场景 4b: 绑定/更换知识库 + 修改模型
            else:
                # 验证新模型是否与目标知识库匹配
                target_kb = await kb_manager.get_kb_detail(new_kb_id)
                if not target_kb:
                    return make_response(False, "目标知识库不存在", code=404)

                if new_model_id != target_kb.embedding_model_id:
                    return make_response(
                        False,
                        f"操作被拒绝：您选择的嵌入模型（ID={new_model_id}）与目标知识库的模型（ID={target_kb.embedding_model_id}）不一致。\n"
                        "请选择匹配的模型，或分步操作：先解绑旧知识库，修改模型，再绑定新知识库。",
                        code=400
                    )

                # 模型匹配，调用业务层处理绑定逻辑
                # process_relation_update 会自动处理模型不一致的文档（删除旧向量+重新嵌入）
                delegate_req = KBUpdateDocsReqDTO(
                    kb_id=new_kb_id,
                    doc_ids=[req.id],
                    action=RelationAction.BIND,
                    force=req.confirm_sync
                )

                result = await relation_biz.process_relation_update(delegate_req, background_tasks)

                if result["status"] == "CONFIRM_REQUIRED":
                    return make_response(success=True, message=result["msg"], data=result["data"])
                elif result["status"] == "ERROR":
                    return make_response(False, result["msg"], code=result.get("code", 400))

                return make_response(True, message=result["msg"], data=result.get("data"))

        # --- 场景 5: 无关键变更，仅更新了元数据 ---
        else:
            return make_response(True, "更新成功")

    except Exception as e:
        import traceback
        return make_response(False, f"更新失败！{str(e)}\n{traceback.format_exc()}", code=500)
    
@router.post("/delete", response_model=dict)
async def delete_doc(
    req: PdfDocDeleteReq,
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """删除文档"""
    try:
        success = await manager.delete(req.doc_id)
        if success:
            return make_response(True, "删除成功")
        return make_response(False, "文档不存在或删除失败", code=404)
    except Exception as e:
        return make_response(False, f"删除异常: {str(e)}", code=500)

@router.post("/unassigned", response_model=dict)
async def get_unassigned_docs(
    req: UnassignedReq,
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """获取未分配知识库的文档"""
    # 提供的 Schema 中缺失 PdfDocUnassignedReq，这里直接用 Query 参数
    try:
        result = await manager.get_unassigned_documents(req.page, req.page_size, req.keyword)
        return make_response(True, "查询成功", result)
    except Exception as e:
        return make_response(success=False, message=str(e), code=500)

@router.get("/content", response_model=dict)
async def get_content(
    doc_id: int = Query(..., description="文档ID"),
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """获取Markdown内容"""
    try:
        content = await manager.get_markdown_content(doc_id)
        return make_response(True, "获取成功", {"content": content})
    except FileNotFoundError:
        return make_response(False, "文档不存在", code=404)
    except Exception as e:
        return make_response(False, str(e), code=500)
    
@router.post("/content/save", response_model=dict)
async def save_content(
    req: PdfDocContentSaveReq,
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """保存Markdown内容"""
    try:
        await manager.save_markdown_content(req.doc_id, req.content)
        return make_response(True, "保存成功")
    except Exception as e:
        return make_response(False, str(e), code=500)
   
@router.post("/export_books_jsonl")
async def export_books(
    req: PdfDocExportBooksReq,
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """导出书籍清单 (JSONL 流式下载)"""
    filter_dto = PdfDocFilterDTO(
        kb_id=req.kb_id,
        keyword=req.keyword,
        filter_step_type=req.filter_step_type,
        filter_step_status=req.filter_step_status
    )
    
    try:
        jsonl_content = await manager.export_books_jsonl(filter_dto)
        stream = StringIO(jsonl_content)
        return StreamingResponse(
            stream, 
            media_type="application/x-ndjson",
            headers={"Content-Disposition": "attachment; filename=books.jsonl"}
        )
    except Exception as e:
        return make_response(False, f"导出失败: {str(e)}", code=500)
    
@router.post("/get_doc_count_by_kb_id", response_model=dict)
async def get_doc_count(
    req: PdfDocCountByKbReq,
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """按知识库统计文档数"""
    try:
        count = await manager.get_doc_count_by_kb_id(req.kb_id)
        return make_response(True, "查询成功", count)
    except Exception as e:
        return make_response(success=False, message=str(e), code=500)

@router.get("/statistics", response_model=dict)
async def get_statistics(
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """获取统计概览"""
    try:
        stats = await manager.get_statistics()
        return make_response(True, "查询成功", stats)
    except Exception as e:
        return make_response(success=False, message=str(e), code=500)

@router.get("/chunk_count", response_model=dict)
async def get_chunk_count(
    doc_id: int = Query(...),
    manager: PdfDocumentManager = Depends(get_pdf_manager)
):
    """
    获取切片数量
    """
    # 模拟实现：假设 Document 有 task 结果包含 chunk count
    try:
        count = await manager.pdf_service.get_chunk_count(doc_id)
        return make_response(True, "查询成功", {"count": count})
    except Exception as e:
        return make_response(False, str(e), code=500)