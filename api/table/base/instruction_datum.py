#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/29 09:12
@Author  : weiyutao
@File    : instruction_datum.py
"""

import uuid
from sqlalchemy import Column, Integer, String, BigInteger, Text, JSON, ForeignKey, Boolean, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from api.table.base.base import Base


class InstructionDatum(Base):
    """
    [新增表] 指令微调数据集明细表
    存储大模型生成的 QA 对。
    设计上与 DocumentChunk 保持一致，支持后续直接向量化 (Embedding) 和检索。
    """
    __tablename__ = 'instruction_datum'

    # === 1. 核心主键 (与 DocumentChunk 保持一致) ===
    # 使用 UUID 字符串，直接对应 Qdrant Point ID，方便向量化
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), comment='唯一ID (UUID)')

    # === 2. 关联信息 ===
    # 关联主文档 (级联删除)
    doc_id = Column(BigInteger, ForeignKey('pdf_document.id', ondelete='CASCADE'), nullable=False, index=True, comment='关联文档ID')
    # 关联具体的生成任务 (方便追溯批次)
    task_id = Column(BigInteger, ForeignKey('pipeline_task.id'), nullable=False, index=True, comment='关联任务ID')

    # === 3. 核心内容 (QA 数据) ===
    # 对应 DocumentChunk 的 content，这里拆分为 Q 和 A，但逻辑上是核心语料
    system_prompt = Column(Text, nullable=False, comment='系统提示词 (system_prompt)')
    question = Column(Text, nullable=False, comment='指令/问题 (Instruction/Question)')
    answer = Column(Text, nullable=False, comment='回答 (Output)')
    chunk_index_description = Column(JSON, default=[], comment='引用的Chunk编号描述列表')
    # 思维链 (可选，用于增强检索或训练)
    chain_of_thought = Column(Text, nullable=True, comment='思维链推理过程')

    # === 4. 上下文与引用 ===
    # 来源章节标题 (类似 meta_info 中的 H1)
    h1_title = Column(String(512), nullable=True, comment='来源章节标题')
    type = Column(String(64), default='general', index=True, comment='指令类型 (如: 原理机制/操作指南/概念解释)')
    # 关键引用：记录这条 QA 是依据哪些 DocumentChunk 生成的
    # 存 JSON list: ["uuid-chunk-1", "uuid-chunk-2"]
    ref_chunk_ids = Column(JSON, default=[], comment='引用的 DocumentChunk ID 列表')

    # === 5. 元数据 (扩展字段) ===
    # 统一存储非检索核心字段，如: {"model": "deepseek-chat", "tokens": 150, "type": "原理机制"}
    meta_info = Column(JSON, default={}, comment='结构化元数据')

    # === 6. 向量化状态 (与 DocumentChunk 完全一致) ===
    # 用于控制是否已同步到 Qdrant
    is_indexed = Column(Boolean, default=False, comment='是否已同步至向量库')
    qdrant_point_id = Column(String(36), nullable=True, comment='向量库中的对应ID (通常等于 id)')

    # === 7. 审核状态 (方案B的核心) ===
    # 0: 待审核 (默认)
    # 1: 有效/通过
    # -1: 无效/拒绝 (导出时过滤)
    is_valid = Column(Integer, default=0, index=True, comment='审核状态(0:待审, 1:有效, -1:无效)')

    # === 8. 审计 ===
    create_time = Column(DateTime(timezone=True), server_default=func.now(), comment='创建时间')
    update_time = Column(DateTime(timezone=True), onupdate=func.now(), comment='更新时间')

    # === 辅助属性 ===
    @property
    def embedding_content(self) -> str:
        """
        [核心] 获取用于向量化的文本内容
        通常向量化 QA 对时，策略是：
        1. 只向量化 Question (用于检索问题)
        2. 向量化 "Q: ... \nA: ..." (用于检索知识)
        这里提供默认的组合格式
        """
        return f"Instruction: {self.question}\nResponse: {self.answer}"

    @property
    def q_type(self) -> str:
        """快捷访问元数据中的类型"""
        return self.meta_info.get('type', 'general')