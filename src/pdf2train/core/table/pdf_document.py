#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/17 12:34
@Author  : weiyutao
@File    : pdf_document.py
"""

from sqlalchemy import Column, Integer, String, DateTime, BigInteger, Text, JSON, ForeignKey
from sqlalchemy.sql import func
from enum import IntEnum, unique
from sqlalchemy.orm import relationship
from pydantic import BaseModel, Field
from typing import Optional

from pdf2train.core.table.base import Base
from pdf2train.core.table.pipeline_task import TaskType, TaskLifecycle, ExtractTaskResult, ChunkTaskResult, InstructionTaskResult


@unique
class DocStatus(IntEnum):
    """
    [宏观状态] 文档全局状态枚举
    用于前端列表页的快速筛选 (Filter)
    """
    PENDING = 0        # 刚上传，未开始
    RUNNING = 10    # 处理中 (任意子任务在运行，或处于中间排队态)
    SUCCESS = 100    # 所有步骤均成功
    FAILED = -1        # 任意步骤失败，流程终止


class CoverInfo(BaseModel):
    """
    [DTO] 封面图信息的统一数据结构
    """
    bucket: str = Field(..., description="封面图所在的桶 (通常是 public-assets)")
    path: str = Field(..., description="封面图的路径 (如 covers/101.jpg)")
    
    @property
    def full_path(self):
        """辅助方法: 方便日志打印"""
        return f"{self.bucket}/{self.path}"



class PdfDocument(Base):
    """
    PDF 文档信息表
    """
    __tablename__ = 'pdf_document'
    
    # === 核心主键 ===
    id = Column(BigInteger, primary_key=True, autoincrement=True, comment='主键id')
    
    # === 文件定位 (MinIO) ===
    bucket_name = Column(String(64), nullable=False, comment='MinIO原始上传文件存储桶')
    object_name = Column(String(512), nullable=False, comment='MinIO对象路径')
    file_name = Column(String(255), nullable=False, comment='原始文件名')
    content_type = Column(String(128), default='application/pdf', comment='文件类型')
    
    # === 核心元数据 ===
    file_size = Column(BigInteger, default=0, comment='大小(字节)')
    page_count = Column(Integer, default=0, comment='页数')
    cover_info = Column(JSON, nullable=True, comment='封面图信息(包含bucket和path)')
    author = Column(String(255), nullable=True, comment='作者')
    original_title = Column(String(255), nullable=True, comment='内置标题')
    summary = Column(Text, nullable=True, comment='简介')
    
    # === [更新] 业务状态与进度 ===
    # status 对应 DocStatus 枚举值
    status = Column(Integer, default=DocStatus.PENDING.value, comment='状态(0:未处理, 10:处理中, 20:合并中, 30:上传中, 100:完成, -1:失败)')
    file_hash = Column(String(100), nullable=False, unique=True, index=True, comment='文件内容SHA-256哈希')
    # [新增] 进度字段
    progress = Column(Integer, default=0, comment='总体进度百分比(0-100, -1为失败)')
    
    kb_id = Column(Integer, ForeignKey("knowledge_base.id"), nullable=True, index=True, comment='所属知识库ID')
    process_error = Column(Text, nullable=True, comment='全局错误摘要')
    instruction_gen_llm_config = Column(String(100), nullable=True, comment='指令生成使用的LLM配置名称')
    h_title_llm_config = Column(String(100), nullable=True, comment='多级标题处理使用的LLM配置名称')
    embedding_llm_config = Column(String(100), nullable=True, comment='语义嵌入LLM配置名称')
    # === 审计 ===
    user_name = Column(String(64), nullable=False, comment='上传人')
    create_time = Column(DateTime(timezone=True), server_default=func.now(), comment='上传时间')
    update_time = Column(DateTime(timezone=True), onupdate=func.now(), comment='更新时间')

    # === 7. 关联关系 ===
    # 使用字符串 "PipelineTask" 避免循环引用
    # cascade="all, delete-orphan": 删除 Document 时自动删除关联的所有 Task
    tasks = relationship(
        "PipelineTask", 
        back_populates="document", 
        order_by="PipelineTask.step_order", 
        cascade="all, delete-orphan",
    )
    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
    
    
    @property
    def cover(self) -> Optional[CoverInfo]:
        """
        [读] 自动将数据库的 JSON 转为 CoverInfo 对象
        使用方式: doc.cover.bucket
        """
        if not self.cover_info:
            return None
        try:
            return CoverInfo.model_validate(self.cover_info)
        except Exception:
            return None
    
    
    @cover.setter
    def cover(self, info: CoverInfo):
        """
        [写] 自动将 CoverInfo 对象转为 JSON 存入数据库
        使用方式: doc.cover = CoverInfo(bucket='a', path='b')
        """
        if info is None:
            self.cover_info = None
        elif isinstance(info, CoverInfo):
            self.cover_info = info.model_dump()
        else:
            raise ValueError("必须赋值为 CoverInfo 对象")
    
    
    @property
    def latest_extract_result(self) -> ExtractTaskResult | None:
        """
        [升级版] 获取最新的提取任务结果 (包含 Bucket 和 Path)
        返回: dictOrNone {'bucket': '...', 'path': '...'}
        """

        if not self.tasks: return None

        # 1. 筛选并排序
        extract_tasks = [t for t in self.tasks if t.task_type == TaskType.MINERU_EXTRACT]
        if not extract_tasks:
            return None
        
        extract_tasks.sort(key=lambda x: x.id, reverse=True)
        latest = extract_tasks[0]

        # 2. 返回完整信息
        if latest.status == TaskLifecycle.SUCCESS and latest.result_data:
            try:
                # 直接把数据库里的字典喂给 Pydantic
                # model_validate 是 Pydantic v2 的方法，v1 用 parse_obj
                return ExtractTaskResult.model_validate(latest.result_data)
            except Exception as e:
                # 防止旧数据格式不匹配导致崩坏
                print(f"解析任务结果失败: {e}")
                return None
        
        return None
    
    
    @property
    def latest_chunk_result(self) -> ChunkTaskResult | None:
        """
        [新增] 获取最新的切片任务结果 (包含 JSON 归档路径和 Chunk 数量)
        对应 TaskType.MARKDOWN_CHUNK 步骤
        """
        if not self.tasks: return None

        # 1. 筛选切片任务 (注意：这里假设你的切片任务枚举值是 MARKDOWN_CHUNK)
        # 如果你的枚举名叫 CHUNK 或其他名字，请相应修改
        chunk_tasks = [t for t in self.tasks if t.task_type == TaskType.MARKDOWN_CHUNK]
        
        if not chunk_tasks:
            return None
        
        # 2. 按 ID 倒序排列，取最新的一次尝试
        chunk_tasks.sort(key=lambda x: x.id, reverse=True)
        latest = chunk_tasks[0]

        # 3. 验证状态并解析
        if latest.status == TaskLifecycle.SUCCESS and latest.result_data:
            try:
                # 将数据库中存储的 JSON (dict) 转换为 ChunkTaskResult 对象
                return ChunkTaskResult.model_validate(latest.result_data)
            except Exception as e:
                # 防止脏数据导致报错
                # 在生产环境建议使用 logging.error 而不是 print
                print(f"解析切片任务结果失败: {e}")
                return None
        
        return None
    
    
    @property
    def latest_instruction_result(self) -> InstructionTaskResult | None:
        """
        [新增] 获取最新的指令生成任务结果
        对应 TaskType.INSTRUCTION_GEN (30) 步骤
        返回: 包含 total_count, model_name, type_distribution 等统计信息的对象
        """
        if not self.tasks: 
            return None

        # 1. 筛选指令生成任务
        # 注意：确保你的 TaskType 枚举中有 INSTRUCTION_GEN
        inst_tasks = [t for t in self.tasks if t.task_type == TaskType.INSTRUCTION_GEN]
        
        if not inst_tasks:
            return None
        
        # 2. 按 ID 倒序排列，取最新的一次尝试
        inst_tasks.sort(key=lambda x: x.id, reverse=True)
        latest = inst_tasks[0]

        # 3. 验证状态并解析
        if latest.status == TaskLifecycle.SUCCESS and latest.result_data:
            try:
                # 将数据库中存储的 JSON (dict) 转换为 InstructionTaskResult 对象
                return InstructionTaskResult.model_validate(latest.result_data)
            except Exception as e:
                # 生产环境建议使用 logging
                print(f"解析指令任务结果失败: {e}")
                return None
        
        return None