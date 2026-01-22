#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/19 12:34
@Author  : weiyutao
@File    : conftest.py
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


from pdf2train.core.table.base import Base
from pdf2train.core.table.pdf_document import PdfDocument
from pdf2train.core.table.pipeline_task import PipelineTask
from pdf2train.core.table.knowledge_base import KnowledgeBase


from pdf2train.core.config import core_config

# 定义内存数据库地址
TEST_DB_URL = core_config.sql_config_test.sql_url


@pytest_asyncio.fixture(scope="function")
async def db_session():
    """
    负责建表、删表、提供 Session
    """
    # 使用测试配置里的 URL 创建引擎
    engine = create_async_engine(TEST_DB_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)   # 清空测试库
        await conn.run_sync(Base.metadata.create_all) # 重建表结构

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        yield session
        await session.rollback()

    await engine.dispose()