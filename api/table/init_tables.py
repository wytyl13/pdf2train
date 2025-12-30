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
import sys


from agent.config.sql_config import SqlConfig
from api.table.base.base import Base
from api.table.base.pdf_document import PdfDocument
from api.table.base.pipeline_task import PipelineTask
from api.table.base.document_chunk import DocumentChunk
from api.table.base.instruction_datum import InstructionDatum
from api.table.base.llm_config import LLMConfig

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

async def create_tables_with_check(auto_choice: str = None):
    """
    检查表是否存在
    :param auto_choice: 'y' (强制重建), 'n' (保留跳过), None (询问用户)
    """
    existing_tables = await check_tables_exist()
    
    if existing_tables:
        print(f"检测到以下表已存在: {', '.join(existing_tables)}")
        
        # ---------------- 核心逻辑开始 ----------------
        if auto_choice:
            # 【情况 A】：有参数 (-y 或 -n)，自动决定，不询问
            print(f"检测到参数 -{auto_choice}，自动执行...")
            user_choice = auto_choice
        else:
            # 【情况 B】：无参数，进入循环询问
            while True:
                # 这里会阻塞等待用户输入
                val = input("是否要删除现有表并重新创建？(y/n): ").strip().lower()
                if val in ['y', 'yes', '是']:
                    user_choice = 'y'
                    break
                elif val in ['n', 'no', '否']:
                    user_choice = 'n'
                    break
                print("请输入 y(是) 或 n(否)")
        # ---------------- 核心逻辑结束 ----------------

        # 根据决定执行操作
        if user_choice == 'y':
            print("正在删除现有表...")
            await drop_all_tables()
            print("正在重新创建表...")
            await create_all_tables()
        else:
            print("保留现有表，仅创建缺失的表...")
            await create_all_tables()

    else:
        print("未检测到现有表，开始创建新表...")
        await create_all_tables()

async def init_database(auto_choice: str = None):
    """初始化数据库：创建表和默认用户"""
    print("开始初始化数据库...")
    
    try:
        # 1. 检查并创建表
        await create_tables_with_check(auto_choice=auto_choice)
        
        print("数据库初始化完成！")
    
    except Exception as e:
        print(f"数据库初始化过程中发生错误: {e}")
        sys.exit(1)


if __name__ == '__main__':
    import asyncio
    # 1. 解析命令行参数
    mode = None
    args = sys.argv[1:] # 获取脚本后的参数

    if '-y' in args or '--yes' in args:
        mode = 'y'
    elif '-n' in args or '--no' in args:
        mode = 'n'
    asyncio.run(init_database(auto_choice=mode))