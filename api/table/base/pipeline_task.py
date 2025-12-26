#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/22 11:15
@Author  : weiyutao
@File    : pipeline_task.py
"""

from sqlalchemy import Column, Integer, String, DateTime, BigInteger, Text, JSON, ForeignKey
from dataclasses import dataclass
from sqlalchemy.orm import relationship
from enum import IntEnum, unique
from typing import Optional, Tuple
from pydantic import BaseModel, Field

from api.table.base.base import Base


class ExtractTaskResult(BaseModel):
    """
    Extract 任务结果的统一数据结构
    用于：存入数据库(序列化) 和 读取使用(反序列化)
    """
    # 1. Markdown 信息
    md_bucket: str = Field(..., description="Markdown文件所在的桶 (通常是私有桶)")
    markdown_path: str = Field(..., description="Markdown文件的路径")
    
    # 2. 图片信息 (可选)
    images_bucket: Optional[str] = Field(None, description="图片所在的桶 (通常是公共桶)")
    images_path: Optional[str] = Field(None, description="图片文件夹前缀")

    # 辅助方法：判断是否有图片
    @property
    def has_images(self) -> bool:
        return bool(self.images_path and self.images_bucket)
    
    # 辅助方法：获取MD完整定位
    @property
    def md_location(self) -> Tuple[str, str]:
        return self.md_bucket, self.markdown_path


class ChunkTaskResult(BaseModel):
    """
    Chunk (切片) 任务结果的统一数据结构
    用途：
    1. 记录中间产物 JSON 文件的位置（用于 Debug、导出或灾备恢复）。
    2. 记录关键统计信息（切片数量），方便前端列表页直接展示，无需每次都 count(*) 数据库。
    """
    
    # === 1. JSON 归档文件信息 ===
    json_bucket: str = Field(..., description="Chunk JSON文件所在的桶")
    json_path: str = Field(..., description="Chunk JSON文件的路径 (如 parsed_results/doc_101.json)")
    
    # === 2. 统计元数据 (强烈建议加上) ===
    chunk_count: int = Field(default=0, description="生成的切片总数")
    

    # 辅助方法：获取文件完整定位
    @property
    def json_location(self) -> Tuple[str, str]:
        """返回 (bucket, object_name) 元组，方便 MinIO 客户端调用"""
        return self.json_bucket, self.json_path
    
    @property
    def download_url(self) -> str:
        """(逻辑示例) 如果需要拼接下载路径"""
        # 注意：这里可能需要结合外部的 endpoint，或者仅作为标识
        return f"/{self.json_bucket}/{self.json_path}"


@unique
class TaskType(IntEnum):
    """任务类型定义"""
    MINERU_EXTRACT = 10    # 步骤1
    MARKDOWN_CHUNK = 20    # 步骤2
    INSTRUCTION_GEN = 30   # 步骤3
    QDRANT_INDEX = 40      # 步骤4
    
    
# === Level 1: 通用生命周期 (给系统调度用) ===
# 所有任务类型都必须映射到这 5 个状态之一
@unique
class TaskLifecycle(IntEnum):
    """
    [通用状态] 任务生命周期
    系统调度层只关心这个状态
    """
    WAITING_PARENT = -2 # 还没轮到我 (前置任务未完成)
    PENDING = 0       # 未开始
    RUNNING = 10      # 执行中 (对应 DocStatus.PROCESSING)
    SUCCESS = 100     # 成功结束 (对应 DocStatus.COMPLETED)
    FAILED = -1       # 失败
    SKIPPED = 30      # 跳过 (特殊状态，依然属于 Processing 范围内，或者你可以定义为 101)
    
    
# === Level 2: 步骤专属状态 (给业务逻辑用) ===
# 步骤 1 专属状态 (你提供的那些)
class ExtractStatus(IntEnum):
    PENDING = 0          # 初始化
    OCR_PROCESSING = 10 
    LAYOUT_MERGING = 20
    RESULT_UPLOADING = 30
    SUCCESS = 100
    FAILED = -1
    
    
# 步骤 2 专属状态 (假设)
class ChunkStatus(IntEnum):
    PENDING = 0
    SPLITTING = 10
    CLEANING = 20
    RESULT_UPLOADING = 50
    SUCCESS = 100
    FAILED = -1
    
    
# 步骤 3 专属状态
class InstructionStatus(IntEnum):
    """[新增] 指令生成步骤的详细状态"""
    PENDING = 0
    PROMPT_CONSTRUCTING = 10  # 构造提示词
    LLM_GENERATING = 20       # 调用大模型生成中
    QA_FILTERING = 30         # 质量过滤/去重
    DATASET_SAVING = 40       # 保存为训练格式 (JSONL)
    SUCCESS = 100
    FAILED = -1


# 步骤 4 专属状态
class IndexStatus(IntEnum):
    PENDING = 0
    BATCHING = 10
    UPSERTING = 20
    SUCCESS = 100
    FAILED = -1
    
    
class PipelineTask(Base):
    """
    任务流水表
    """
    __tablename__ = 'pipeline_task'

    # === 1. 基础字段 ===
    id = Column(BigInteger, primary_key=True, autoincrement=True, comment='任务ID')
    
    # === 2. 关联主表 ===
    # 使用字符串 'pdf_document.id'
    doc_id = Column(BigInteger, ForeignKey('pdf_document.id'), nullable=False, index=True, comment='关联文档ID')
    
    # === 3. 任务定义 ===
    task_type = Column(Integer, nullable=False, comment='任务类型(10:Extract, 20:Chunk, 30:Index)')
    step_order = Column(Integer, default=1, nullable=False, comment='执行顺序(1,2,3...)')
    task_name = Column(String(64), comment='任务展示名称')
    
    # === 4. 双层状态管理 (核心) ===
    
    # Layer 1: 通用生命周期 (0:Pending, 1:Running, 2:Success, -1:Fail)
    # 用于计算主文档的 Global Status
    status = Column(Integer, default=TaskLifecycle.PENDING.value, index=True, comment='系统调度状态')
    
    # Layer 2: 业务详细状态 (Int)
    # 存储 ExtractStatus.MERGING (20) 或 ChunkStatus.CLEANING (10) 等具体值
    # 用于前端展示具体的 "正在合并..." 文案
    detailed_status = Column(Integer, default=0, comment='业务详细状态值')
    progress = Column(Integer, default=0, comment='任务内进度(0-100)')
    # === 5. 输入输出 ===
    # input_params: 记录参数，如 {"chunk_size": 500}
    input_params = Column(JSON, nullable=True, comment='执行参数')

    # result_data: 存储产出路径，如 {"markdown_path": "minio://..."}
    result_data = Column(JSON, nullable=True, comment='产出结果(JSON)')
    
    # === 6. 错误与性能 ===
    error_message = Column(Text, nullable=True, comment='错误堆栈信息')
    start_time = Column(DateTime, nullable=True, comment='开始时间')
    end_time = Column(DateTime, nullable=True, comment='结束时间')
    cost_ms = Column(BigInteger, default=0, comment='耗时(毫秒)')

    # === 7. 关联关系 ===
    document = relationship("PdfDocument", back_populates="tasks")
    
