#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/17 12:35
@Author  : weiyutao
@File    : init_tables.py
"""


from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.exc import OperationalError


from agent.config.sql_config import SqlConfig
from api.table.base.base import Base
from api.table.base.pdf_document import PdfDocument
from api.table.base.pipeline_task import PipelineTask
from api.table.base.document_chunk import DocumentChunk



ROOT_DIRECTORY = Path(__file__).parent.parent.parent
SQL_CONFIG_PATH = str(ROOT_DIRECTORY / "config" / "yaml" / "postgresql.yaml")

sql_config = SqlConfig.from_file(SQL_CONFIG_PATH)

async def check_tables_exist():
    """检查表是否已存在"""
    engine = create_async_engine(sql_config.sql_url)
    
    async with engine.begin() as conn:
        # 获取所有需要创建的表名
        table_names = [table.name for table in Base.metadata.tables.values()]
        print(table_names)
        # 检查每个表是否存在
        existing_tables = []
        for table_name in table_names:
            try:
                # 尝试查询表的存在性
                result = await conn.execute(text(f"SELECT 1 FROM {table_name} LIMIT 1"))
                existing_tables.append(table_name)
            except Exception:
                # 表不存在或查询失败
                pass
    
    await engine.dispose()
    return existing_tables

async def drop_all_tables():
    """删除所有表"""
    engine = create_async_engine(sql_config.sql_url)
    
    async with engine.begin() as conn:
        # 删除所有表
        await conn.run_sync(Base.metadata.drop_all)
    
    await engine.dispose()
    print("所有表已删除！")

async def create_all_tables():
    """异步创建所有表"""
    engine = create_async_engine(sql_config.sql_url)
    
    async with engine.begin() as conn:
        # 这会创建所有继承自Base的表
        await conn.run_sync(Base.metadata.create_all)
    
    await engine.dispose()
    print("所有表创建完成！")

async def create_tables_with_check():
    """检查表是否存在，如果存在则询问用户是否删除重建"""
    existing_tables = await check_tables_exist()
    all_required_tables = [table.name for table in Base.metadata.tables.values()]  # 定义所需的所有表
    
    if existing_tables:
        print(f"检测到以下表已存在: {', '.join(existing_tables)}")
        
        while True:
            user_choice = input("是否要删除现有表并重新创建？(y/n): ").strip().lower()
            
            if user_choice in ['y', 'yes', '是']:
                print("正在删除现有表...")
                await drop_all_tables()
                print("正在重新创建表...")
                await create_all_tables()
                break
            elif user_choice in ['n', 'no', '否']:
                print("保留现有表，创建缺失的表...")
                # 创建不存在的表
                await create_all_tables()
                break
            else:
                print("请输入 y(是) 或 n(否)")
    else:
        print("未检测到现有表，开始创建新表...")
        await create_all_tables()

async def init_database():
    """初始化数据库：创建表和默认用户"""
    print("开始初始化数据库...")
    
    try:
        # 1. 检查并创建表
        await create_tables_with_check()
        
        print("数据库初始化完成！")
    
    except Exception as e:
        print(f"数据库初始化过程中发生错误: {e}")
        raise


if __name__ == '__main__':
    import asyncio
    asyncio.run(init_database())