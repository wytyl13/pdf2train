#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/19 14:52
@Author  : weiyutao
@File    : pdf_document_manager.py
"""

import hashlib
import uuid
import os
import logging
from typing import Optional, List, Any, Dict, Union
from fastapi import UploadFile
from pathlib import Path
from dotenv import dotenv_values
from urllib.parse import quote
from datetime import datetime

from pdf2train.core.schema.pdf_document_dto import PdfDocCoreDTO, PdfDocUpdateDTO, PdfDocFilterDTO, PdfDocRichDTO
from pdf2train.utils.pdf_utils import PdfUtils
from pdf2train.core.service.pdf_document_service import PdfDocumentService
from pdf2train.core.service.minio_service import MinioService
from pdf2train.core.service.llm_config_service import LLMConfigService
from pdf2train.core.service.knowledge_base_service import KnowledgeBaseService
from pdf2train.core.table.llm_enum import ModelType
from pdf2train.core.config import core_config

ROOT_DIRECTORY = Path(__file__).parent.parent.parent.parent.parent
ENV_PATH = str(ROOT_DIRECTORY / ".env")
environment = dotenv_values(ENV_PATH)
MINIO_BASE_URL = core_config.minio_config.minio_public_url or environment.get("MINIO_BASE_URL", "http://localhost:9000")

class PdfDocumentManager:
    def __init__(
        self, 
        pdf_service: PdfDocumentService, 
        minio_service: MinioService,
        llm_config_service: LLMConfigService,
        kb_service: KnowledgeBaseService
    ):
        # 在fastapi中按需加载manager，而manager依赖pdf_service，所以该服务是用的时候才被加载的
        self.pdf_service = pdf_service 
        self.minio_service = minio_service
        self.llm_config_service = llm_config_service
        self.kb_service = kb_service
        self.minio_base_url = MINIO_BASE_URL
        self.logger = logging.getLogger(self.__class__.__name__)

    def _get_base_url(self) -> str:
        return self.minio_base_url

    def _format_size(self, size: int) -> str:
        """辅助函数：格式化文件大小"""
        if not size: return "0 B"
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} TB"

    async def _enrich_doc_list(self, docs: List[Any]) -> List[PdfDocRichDTO]:
        """
        返回 List[PdfDocResponse] 对象列表
        """
        if not docs:
            return []

        # 1. 提取所有需要查询的 KB ID
        # 注意：这里假设 doc 是 ORM 对象，直接访问属性
        kb_ids_to_query = {doc.kb_id for doc in docs if doc.kb_id}

        # 2. 调用 KnowledgeBaseService 批量获取名称 (解耦)
        kb_name_map = {}
        if kb_ids_to_query:
            # 这一步调用的是 Service，避免了 Manager 间的循环依赖
            kb_name_map = await self.kb_service.get_names_by_ids(kb_ids_to_query)

        # 3. 获取默认配置 (只需查一次)
        default_embedding = None
        try:
            default_embedding = await self.llm_config_service.get_active_config_name(
                model_type=ModelType.EMBEDDING.value
            )
        except Exception as e:
            self.logger.warning(f"获取默认Embedding配置失败: {e}")

        base_url = self._get_base_url()
        result_list = []

        # 4. 遍历并组装 Rich DTO
        for doc in docs:
            # A. ORM -> Core DTO (基础字段转换)
            # 使用 model_validate 读取 ORM 对象，转为字典方便修改
            # 这一步确保了 id, status, create_time 等基础字段的正确性
            core_data = PdfDocCoreDTO.model_validate(doc).model_dump()

            # B. 计算虚字段 (Computed Fields)
            
            # 知识库名称
            kb_name = kb_name_map.get(doc.kb_id) or "未关联知识库"

            # 下载链接拼接
            download_url = None
            if doc.bucket_name and doc.object_name:
                download_url = f"{base_url}/{doc.bucket_name}/{doc.object_name}"

            # 封面链接拼接
            cover_url = None
            # 注意：在 CoreDTO 中 cover_info 已经被转为 Dict 或 None
            c_info = core_data.get('cover_info')
            if c_info:
                # 兼容字典访问
                c_bucket = c_info.get('bucket')
                c_path = c_info.get('path')
                if c_bucket and c_path:
                    cover_url = f"{base_url}/{c_bucket}/{c_path}"

            # 文件大小格式化
            file_size_display = self._format_size(doc.file_size)

            # 补全 LLM 配置
            embedding_config = core_data.get('embedding_llm_config') or default_embedding
            
            if not core_data.get('embedding_llm_config'):
                # core_data和PdfDocRichDTO重复字段这里处理
                core_data['embedding_llm_config'] = default_embedding

            # C. 实例化 Rich DTO
            rich_dto = PdfDocRichDTO(
                **core_data,
                kb_name=kb_name,
                download_url=download_url,
                cover_url=cover_url,
                file_size_display=file_size_display
            )
            
            result_list.append(rich_dto)

        return result_list

    async def upload_and_create(
        self, 
        file: UploadFile, 
        kb_id: Optional[int] = None, 
        bucket_name: str = "pdf-raw",
        user_name: str = "system"
    ):
        """
        [核心业务] 上传 PDF 并创建记录
        流程：
        1. 读取流 -> 计算 Hash -> 判重 (秒传)
        2. PdfUtils -> 提取元数据 & 封面 (CPU 密集)
        3. MinioService -> 上传文件 & 封面 (IO 密集)
        4. PdfDocumentService -> 写入数据库
        """
        # 1. 读取文件内容 & 计算 Hash
        content = await file.read()
        file_hash = hashlib.sha256(content).hexdigest()
        file_size = len(content)
        filename = file.filename
        
        # 2. 判重逻辑 (De-duplication)
        existing_doc = await self.pdf_service.get_by_hash(file_hash)
        if existing_doc:
            self.logger.info(f"检测到重复文件 {filename} (Hash: {file_hash})，执行秒传逻辑。")
            return existing_doc

        # 3. [CPU 任务] 提取元数据和封面
        # 注意：这里调用的是工具类，不涉及 IO
        meta_dict = PdfUtils.extract_metadata(content)
        # 格式化meta_dict,minio不支持中文
        minio_metadata = {}
        if meta_dict:
            for k, v in meta_dict.items():
                if v:
                    try:
                        # 将中文转为 ASCII 安全字符
                        minio_metadata[k] = quote(str(v)) 
                    except Exception:
                        pass # 如果转换失败，忽略该字段
        cover_bytes = PdfUtils.generate_cover(content)

        # 4. [IO 任务] 准备路径并上传
        ext = os.path.splitext(filename)[1]
        if not ext: ext = ".pdf"
        
        # 生成唯一路径
        unique_name = f"{uuid.uuid4()}{ext}"
        object_name = f"kb_{kb_id}/{unique_name}" if kb_id else f"common/{unique_name}"

        # 上传主文件 (将提取到的 meta 注入 MinIO 属性中)
        await self.minio_service.upload_file_stream(
            bucket_name=bucket_name,
            object_name=object_name,
            file_data=content,
            content_type=file.content_type or "application/pdf",
            metadata=minio_metadata
        )

        # 上传封面 (如果有)
        cover_info = None
        if cover_bytes:
            cover_path = f"covers/{object_name}.jpg" # 保持路径结构一致
            await self.minio_service.upload_file_stream(
                bucket_name="public-assets", # 封面通常存放在公开桶
                object_name=cover_path,
                file_data=cover_bytes,
                content_type="image/jpeg"
            )
            cover_info = {"bucket": "public-assets", "path": cover_path}

        # 5. [DB 任务] 组装 DTO 并落库
        # 将 MinIO 元数据 key (kebab-case) 映射回 DB 字段 (snake_case)
        doc_dto = PdfDocCoreDTO(
            kb_id=kb_id,
            file_name=filename,
            file_hash=file_hash,
            file_size=file_size,
            bucket_name=bucket_name,
            object_name=object_name,
            content_type=file.content_type,
            user_name=user_name,
            status=0, # PENDING
            
            # 映射元数据
            page_count=int(meta_dict.get("pages", 0)),
            author=meta_dict.get("author"),
            original_title=meta_dict.get("original-title"),
            instruction_gen_llm_config=await self.llm_config_service.get_active_config_name(model_type=ModelType.LLM.value),
            h_title_llm_config=await self.llm_config_service.get_active_config_name(model_type=ModelType.LLM.value),
            embedding_llm_config=await self.llm_config_service.get_active_config_name(model_type=ModelType.EMBEDDING.value),
            cover_info=cover_info,
            process_error=None,
        )

        # 6. 调用 Service 创建
        new_id = await self.pdf_service.create(doc_dto)
        
        # 7. 返回完整对象
        return await self.pdf_service.get_by_id(new_id)

    async def get_list_documents(self, page: int, size: int, filter_dto: PdfDocFilterDTO) -> Dict[str, Union[PdfDocRichDTO, int]]:
        """
        获取文档列表
        Manager 可以在这里做一些 VO 转换，或者直接返回
        """
        # 1. 获取原始数据库数据
        items, total = await self.pdf_service.search_paginated(page, size, filter_dto)

        # 2. 后处理为富文本数据
        result: List[PdfDocRichDTO] = await self._enrich_doc_list(items)
        
        # 返回字典数据给前端
        return {
            "items": result,
            "total": total,
            "page": page,
            "size": size
        }

    async def update(self,doc_id: int, update_dto: PdfDocUpdateDTO):
        """
        更新文档信息
        业务逻辑：如果修改了 critical 字段（如切片规则），可能需要触发后台任务
        """
        # 1. 执行更新
        success = await self.pdf_service.update(doc_id, update_dto)
        if not success:
            raise ValueError(f"文档 {update_dto.doc_id} 不存在")
            
        # 2. [副作用处理] 比如：如果更新了 kb_id，可能需要把该文档的向量从旧库移到新库
        # if update_dto.kb_id is not None:
        #      await self.chunk_service.move_chunks(...)
             
        return True

    async def delete(self, doc_id: int):
        """删除文档 (联动删除 MinIO)"""
        # 1. 先查
        doc = await self.pdf_service.get_by_id(doc_id)
        if not doc:
            raise FileNotFoundError(f"文档 ID {doc_id} 不存在")

        # 2. 删文件 (不抛异常，尽力而为)
        try:
            await self.minio_service.remove_object(doc.bucket_name, doc.object_name)
            # 如果有封面，也可以顺便删封面，这里略
        except Exception as e:
            print(f"Warning: Failed to delete file from MinIO: {e}")

        # 3. 删库
        return await self.pdf_service.delete(doc_id)

    async def export_books_data(
        self, 
        filter_dto: PdfDocFilterDTO
    ) -> List[Dict[str, Any]]:
        """
        [新增] 导出书籍清单数据
        获取全量符合条件的数据，并进行格式化，便于生成 Excel/CSV
        """
        try:
            # 1. 复用 get_document_list 获取全量数据
            # [修改点2] 将 kb_id 透传给 get_document_list
            result = await self.get_list_documents(
                page=None,
                size=None,
                filter_dto=filter_dto
            )
            
            raw_items = result.get("items", [])
            export_list = []

            # 2. 定义状态映射
            status_map = {
                0: "待处理",
                10: "解析中",
                20: "合并中",
                30: "上传中",
                100: "已完成",
                -1: "失败"
            }

            # 3. 数据格式化 (保持原有逻辑不变)
            for item in raw_items:
                # 计算文件大小 (MB)
                file_size = item.file_size or 0
                size_mb = round(file_size / (1024 * 1024), 2)
                
                # 格式化时间
                create_time = item.create_time
                create_time_str = ""
                if create_time:
                    create_time_str = create_time.strftime("%Y-%m-%d %H:%M:%S") if isinstance(create_time, datetime) else str(create_time)

                # 获取状态文本
                status_code = item.status
                status_text = status_map.get(status_code, f"未知({status_code})")

                # 构造导出行的字典
                row = {
                    "文档ID": str(item.id),
                    "文件名称": item.file_name,
                    "原标题": item.original_title or "",
                    "作者": item.author or "未知",
                    "文件大小(MB)": size_mb,
                    "页数": item.page_count,
                    "当前状态": status_text,
                    "上传用户": item.user_name,
                    "上传时间": create_time_str,
                    "摘要": item.summary or "",
                    "下载链接": item.download_url or ""
                }
                export_list.append(row)

            return export_list

        except Exception as e:
            self.logger.error(f"导出数据准备失败: {str(e)}")
            raise e

    async def export_books_jsonl(self, filter_dto: PdfDocFilterDTO) -> str:
        """
        导出符合条件的文档为 JSONL 格式字符串
        业务逻辑：查出所有 ORM 对象 -> 拼接成 JSONL 字符串
        """
        import json
        data_list = await self.export_books_data(filter_dto)
        if not data_list:
            return ""
        lines = [
            json.dumps(row, ensure_ascii=False, default=str) 
            for row in data_list
        ]
        return "\n".join(lines)

    async def get_doc_count_by_kb_id(self, kb_id: int) -> int:
        return await self.pdf_service.get_doc_count_by_kb_id(kb_id)

    async def get_statistics(self):
        """
        获取统计面板数据
        业务逻辑：可能需要聚合多个 Service 的数据
        """
        try:
            # 1. 获取原始计数
            # 例如: {0: 3, 10: 2, 20: 1, 30: 1, 100: 3, -1: 2}
            raw_counts = await self.pdf_service.get_stats_group_by_status()
            
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

    async def get_unassigned_documents(self, page: int, size: int, keyword: str = None):
        """
        获取未分配知识库的文档 (kb_id IS NULL)
        """
        items, total = await self.pdf_service.search_paginated(
            page, size, 
            # 构造一个特殊的 filter
            PdfDocFilterDTO(kb_id=None, keyword=keyword) # 假设 None 代表查询 null
        )
        return {"items": items, "total": total}

    async def get_markdown_content(self, doc_id: int) -> str:
        """
        获取 Markdown 内容
        """
        # 1. 改用 Service 调用，复用连接池，不要手动 new SqlProvider
        # Service 层已经封装了 get_with_relations
        doc = await self.pdf_service.get_with_relations(doc_id, relations=["tasks"])
        
        if not doc:
            raise FileNotFoundError(f"文档 ID {doc_id} 不存在")
        
        # 2. 【核心逻辑保留】直接调用 Model 层的智能属性
        # 这里完全沿用你的代码
        result = getattr(doc, 'latest_extract_result', None)
        
        if not result:
            # 这里抛出异常给前端提示，或者返回空字符串看你需求
            raise ValueError(f"文档 {doc_id} 尚未生成解析结果")
        
        # 3. 读 MinIO
        try:
            return await self.minio_service.read_object_text(
                result.md_bucket,      
                result.markdown_path   
            )
        except Exception as e:
            self.logger.error(f"MinIO 读取失败: {e}")
            raise RuntimeError(f"底层存储读取失败: {str(e)}")

    async def save_markdown_content(self, doc_id: int, new_content: str) -> bool:
        """
        保存/更新 Markdown 内容
        """
        # 1. 改用 Service 调用
        doc = await self.pdf_service.get_with_relations(doc_id, relations=["tasks"])
        
        if not doc:
            raise FileNotFoundError(f"文档 ID {doc_id} 不存在")
            
        # 2. 【核心逻辑保留】获取路径
        result = getattr(doc, 'latest_extract_result', None)
        
        if not result:
             raise ValueError("无法保存：该文档尚未生成初始 Markdown 文件")
        
        # 3. 写 MinIO
        try:
            await self.minio_service.put_object_text(
                result.md_bucket, 
                result.markdown_path, 
                new_content
            )
            return True
        except Exception as e:
            self.logger.error(f"MinIO 写入失败: {e}")
            raise RuntimeError(f"底层存储写入失败: {str(e)}")