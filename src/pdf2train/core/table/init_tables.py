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
from dotenv import load_dotenv, dotenv_values
from typing import List


from pdf2train.core.provider.sql_provider import SqlProvider
from pdf2train.core.config import core_config
from pdf2train.core.table.base import Base
from pdf2train.core.table.pdf_document import PdfDocument
from pdf2train.core.table.pipeline_task import PipelineTask
from pdf2train.core.table.document_chunk import DocumentChunk
from pdf2train.core.table.instruction_datum import InstructionDatum
from pdf2train.core.table.llm_config import LLMConfig
from pdf2train.core.table.knowledge_base import KnowledgeBase

from pdf2train.api.schema.retrieval_schema import RetrievalSettings, RetrievalMode, RerankConfig, HybridConfig


ROOT_DIRECTORY = Path(__file__).parent.parent.parent.parent.parent
ENV_PATH = str(ROOT_DIRECTORY / ".env")
sql_config = core_config.sql_config
environment = dotenv_values(ENV_PATH)
DEEPSEEK_API_KEY = environment.get("DEEPSEEK_API_KEY")
ALIYUN_API_KEY = environment.get("ALIYUN_API_KEY")

async def check_and_upgrade_tables():
    """
    检查现有表结构并执行必要的字段新增操作
    """
    print("正在检查表结构变更...")
    engine = create_async_engine(sql_config.sql_url)

    async with engine.connect() as conn:
        # 定义需要检查的变更任务
        # 格式: (表名, 列名, SQL语句)
        migrations = [
            (
                "instruction_datum", 
                "chunk_index_description", 
                "ALTER TABLE instruction_datum ADD COLUMN chunk_index_description JSON DEFAULT '[]'::json"
            ),
            (
                "sys_llm_configs",
                "model_type",
                "ALTER TABLE sys_llm_configs ADD COLUMN model_type VARCHAR(50) DEFAULT 'llm'"
            ),
            (
                "pdf_document",
                "embedding_llm_config",
                "ALTER TABLE pdf_document ADD COLUMN embedding_llm_config VARCHAR(255) DEFAULT 'Aliyun-Embedding-V4'"
            ),
            (
                "pdf_document",
                "kb_id",
                "ALTER TABLE pdf_document ADD COLUMN kb_id INTEGER DEFAULT NULL REFERENCES knowledge_base(id)"
            ),
            (
                "pdf_document",
                "file_hash",
                "ALTER TABLE pdf_document ADD COLUMN file_hash VARCHAR(255) DEFAULT NULL"
            ),
            (
                "knowledge_base",
                "embedding_model_id",
                "ALTER TABLE knowledge_base ADD COLUMN embedding_model_id INTEGER REFERENCES sys_llm_configs(id)"
            ),
            # 未来如果有其他新增字段，可以继续加在这里
        ]

        for table, column, sql in migrations:
            try:
                # 检查列是否存在
                # 注意：PostgreSQL 的 information_schema 查询
                check_sql = text(f"""
                    SELECT 1 
                    FROM information_schema.columns 
                    WHERE table_name = :table_name AND column_name = :column_name
                """)
                
                result = await conn.execute(check_sql, {"table_name": table, "column_name": column})
                if result.scalar() is None:
                    print(f"⚠️ 检测到表 {table} 缺少列 {column}，正在添加...")
                    await conn.execute(text(sql))
                    await conn.commit() # 提交更改
                    print(f"✅ 列 {column} 添加成功。")
                else:
                    # print(f"表 {table} 已包含列 {column}，无需操作。")
                    pass
            except Exception as e:
                print(f"❌ 检查/更新表 {table} 失败: {e}")

    await engine.dispose()

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
            print("正在重新llm默认配置...")
            await init_default_data()
            await init_knowledge_base_data()
        else:
            print("保留现有表，仅创建缺失的表...")
            await create_all_tables()
            print("正在重新llm默认配置...")
            await init_default_data()
            # await init_knowledge_base_data()

    else:
        print("未检测到现有表，开始创建新表...")
        await create_all_tables()

async def init_knowledge_base_data():
    """
    初始化默认知识库数据
    """
    print("正在初始化默认知识库 (通用农业知识库)...")

    # 定义我们要引用的 Embedding 配置名称
    target_embedding_name = "Aliyun-Embedding-V4"
    embedding_config_id = None

    # === 1. 先查询 Embedding 配置的 ID ===
    llm_provider = None
    try:
        llm_provider = SqlProvider(model=LLMConfig)
        # 查询数据库中是否存在该配置
        configs: List[LLMConfig] = await llm_provider.get_record_by_condition({"name": target_embedding_name})
        
        if not configs:
            print(f"❌ 错误：未找到名称为 [{target_embedding_name}] 的 Embedding 配置，无法创建默认知识库。")
            return
        
        # 获取 ID (假设返回的是 ORM 对象列表)
        embedding_config_id = configs[0].id
        print(f"✅ 找到关联 Embedding 配置 ID: {embedding_config_id}")

    except Exception as e:
        print(f"❌ 查询 Embedding 配置失败: {e}")
        return

    # 1. 构造 Pydantic 对象
    default_settings = RetrievalSettings(
        top_k=5,
        score_threshold=0.4,
        mode=RetrievalMode.HYBRID,
        rerank=RerankConfig(
            enable=True,
            model_name="Aliyun-GTE-Rerank",  # 确保这个名字在 LLMConfig 里有
            top_n=20
        ),
        hybrid_params=HybridConfig(alpha=0.5)
    )

    # 2. 构造入库字典
    default_kb = {
        "name": "通用农业知识库",
        "description": "系统内置默认知识库",
        "avatar_url": "https://img.alicdn.com/imgextra/i4/O1CN01Z5PaLz1O7guX2l8j4_!!6000000001654-2-tps-200-200.png",
        "embedding_model_id": embedding_config_id,
        "vector_store_collection_name": "global_agriculture_pool",
        "_settings": default_settings.model_dump(), 
        "user_id": 1, 
        "is_public": True,
        "is_deleted": False
    }

    sql_provider = None
    try:
        sql_provider = SqlProvider(model=KnowledgeBase)
        
        # 检查是否存在
        existing = await sql_provider.get_record_by_condition({"name": default_kb["name"]})
        
        if not existing:
            await sql_provider.add_record(default_kb)
            print(f"✅ 默认知识库 [{default_kb['name']}] 初始化成功。")
        else:
            print(f"⚡ 知识库 [{default_kb['name']}] 已存在，跳过。")
            
    except Exception as e:
        print(f"❌ 初始化知识库数据失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if sql_provider: await sql_provider.close()


async def init_default_data():
    """
    初始化默认数据
    """
    print("正在初始化默认 LLM 配置 (DeepSeek-V3)...")
    
    default_configs = [
        # 1. 默认 LLM (DeepSeek)
        {
            "name": "DeepSeek-V3",
            "model_type": "llm",          # 明确指定类型
            "provider": "DeepSeek",
            "model_name": "deepseek-chat",
            "api_key": DEEPSEEK_API_KEY,
            "base_url": "https://api.deepseek.com",
            "is_default": True
        },
        # 2. 默认 Embedding (Aliyun)
        {
            "name": "Aliyun-Embedding-V4",
            "model_type": "embedding",    # 明确指定类型
            "provider": "Aliyun",
            "model_name": "text-embedding-v4",
            "api_key": ALIYUN_API_KEY,
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "is_default": True
        },
        # 3. 默认 Rerank (Aliyun)
        {
            "name": "Aliyun-GTE-Rerank",
            "model_type": "rerank",       # 明确指定类型
            "provider": "Aliyun",
            "model_name": "gte-rerank-v2",
            "api_key": ALIYUN_API_KEY,
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "is_default": True
        }
    ]
    
    print(default_configs)
    sql_provider = None
    try:
        # 初始化 Provider
        sql_provider = SqlProvider(model=LLMConfig)
        
        for config in default_configs:
            # 1. 检查是否存在 (通过 name 唯一索引判断)
            existing_records = await sql_provider.get_record_by_condition({"name": config["name"]})
            
            if not existing_records:
                # 2. 不存在则写入
                await sql_provider.add_record(config)
                print(f"✅ 默认配置 [{config['name']}] ({config['model_type']}) 写入成功。")
            else:
                # 3. 已存在则跳过
                print(f"⚡ 配置 [{config['name']}] 已存在，跳过初始化。")
        print("✅ 默认 LLM 配置写入成功。")
        
    except Exception as e:
        print(f"❌ 初始化默认数据失败: {e}")
    finally:
        if sql_provider:
            await sql_provider.close()


async def init_database(auto_choice: str = None):
    """初始化数据库：创建表和默认用户"""
    print("开始初始化数据库...")
    
    try:
        # 1. 检查并创建表
        await create_tables_with_check(auto_choice=auto_choice)
        
        # 2. 检查现有表是否缺少字段
        await check_and_upgrade_tables()
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