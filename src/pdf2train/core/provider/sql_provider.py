#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2024/12/24 11:35
@Author  : weiyutao
@File    : sql_provider.py
"""
import traceback
from pydantic import BaseModel, model_validator, ValidationError
from typing import (
    AsyncGenerator,
    AsyncIterator,
    Dict,
    Iterator,
    Optional,
    Tuple,
    Union,
    overload,
    Generic,
    TypeVar,
    Any,
    Type,
    List
)
from sqlalchemy import create_engine, text, select, func
from sqlalchemy.orm import sessionmaker, DeclarativeBase, selectinload
from sqlalchemy.ext.declarative import declarative_base
import numpy as np
from contextlib import contextmanager
import traceback
import urllib.parse
from sqlalchemy import and_
from datetime import datetime, date
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession, AsyncEngine
from contextlib import asynccontextmanager
import asyncio
import threading
import asyncio
        
from pdf2train.core.configs.sql_config import SqlConfig
from .base_provider import BaseProvider
from pdf2train.core.config import core_config
# 定义基类
class Base(DeclarativeBase):
    pass

# 定义泛型类型变量
ModelType = TypeVar("ModelType", bound=Base)


_GLOBAL_ENGINE: Optional[AsyncEngine] = None


class SqlProvider(
    BaseProvider,
    Generic[ModelType]
):
    def __init__(
        self, 
        model: Type[ModelType] = None,
        sql_config: Optional[SqlConfig] = None,
        session_factory = None
    ) -> None:
        super().__init__()
        self._init_param(sql_config, model, session_factory)
        self._sync_connection = None
    
    def _init_param(self, sql_config: SqlConfig, model : Type[ModelType] = None, session_factory = None):
        self.sql_config = sql_config if sql_config else core_config.sql_config
        
        # 设置数据库类型
        if self.sql_config and hasattr(self.sql_config, 'database_type'):
            self.database_type = self.sql_config.database_type.lower()
        else:
            self.database_type = "mysql"  # 默认值，保证向后兼容
        
        # 标准化postgresql类型名称
        self.database_type = "postgresql" if self.database_type == "postgres" else self.database_type
        
        # 验证数据库类型
        if self.database_type not in ["mysql", "postgresql", "postgres"]:
            raise ValueError(f"Unsupported database type: {self.database_type}. Supported types: mysql, postgresql")
        
        # if self.sql_config is None and self.data is None:
        #     raise ValueError("config config_path and data must not be null!")
        if session_factory:
            self.sql_connection = session_factory
        else:
            self.sql_connection = self.get_sql_connection()
        self.model = model
        if self.model is None:
            raise ValueError("model must not be null!")

    
    async def close(self):
        """显式关闭数据库连接"""
        pass
        # if hasattr(self, '_engine') and self._engine:
        #     await self._engine.dispose()
        #     self._engine = None
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()
    
    
    
    def get_sql_connection(self):
        # 引入全局变量
        global _GLOBAL_ENGINE

        try:
            sql_info = self.sql_config
        except Exception as e:
            raise ValueError(f"fail to init the sql connect information!\n{self.sql_config}") from e
        
        database_url = sql_info.sql_url
        
        try:
            # 1. 检查全局 Engine 是否已存在
            if _GLOBAL_ENGINE is None:
                # 只有当它是 None 时，才创建新的连接池
                print(f"🔌 [SqlProvider] 初始化全局数据库连接池: {database_url}")
                _GLOBAL_ENGINE = create_async_engine(
                    database_url, 
                    pool_size=20,          # 连接池保持 20 个连接
                    max_overflow=10,       # 允许临时增加 10 个
                    pool_pre_ping=True,    # 自动检测断连
                    pool_recycle=3600      # 1小时回收
                )
            
            # 2. 使用全局 Engine 创建 Session 工厂
            # 注意：bind 必须指向全局唯一的 _GLOBAL_ENGINE
            SessionLocal = async_sessionmaker(
                bind=_GLOBAL_ENGINE, 
                expire_on_commit=False
            )
            return SessionLocal
            
        except Exception as e:
            raise ValueError("fail to create the sql connector engine!") from e
    
    
    def set_model(self, model: Type[ModelType] = None):
        """reset model"""
        if model is None:
            raise ValueError('model must not be null!')
        self.model = model
    
    
    @asynccontextmanager
    async def get_db_session(self):
        """提供数据库会话的上下文管理器"""
        if hasattr(self, 'session') and self.session is not None:
            # 直接 yield 这个 session，不负责 commit/close (由外部控制)
            yield self.session
            return
        
        if not self.sql_connection:
            raise ValueError("Database connection not initialized")
        
        # 统一异步处理，不区分数据库类型
        async with self.sql_connection() as session:
            try:
                yield session
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
    
    
    @contextmanager
    def get_sync_db_session(self):
        """提供同步数据库会话的上下文管理器"""
        if not hasattr(self, '_sync_connection') or self._sync_connection is None:
            self._sync_connection = self.get_sync_sql_connection()
        
        session = self._sync_connection()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()
    
    
    def get_sync_sql_connection(self):
        """创建同步数据库连接"""
        try:
            sql_info = self.sql_config
            username = sql_info.username
            database = sql_info.database
            password = sql_info.password
            host = sql_info.host
            port = sql_info.port
        except Exception as e:
            raise ValueError(f"fail to init the sql connect information!\n{self.sql_config}") from e
        
        encoded_password = urllib.parse.quote_plus(password)
        
        # 根据数据库类型构建同步连接字符串
        if self.database_type == "mysql":
            # MySQL 同步驱动
            database_url = f"mysql+pymysql://{username}:{encoded_password}@{host}:{port}/{database}"
        elif self.database_type == "postgresql":
            # PostgreSQL 同步驱动
            database_url = f"postgresql+psycopg2://{username}:{encoded_password}@{host}:{port}/{database}"
        
        try:
            # 创建同步引擎
            sync_engine = create_engine(
                database_url, 
                pool_size=10, 
                max_overflow=20,
                pool_pre_ping=True
            )
            SessionLocal = sessionmaker(bind=sync_engine)
            return SessionLocal
        except Exception as e:
            raise ValueError("fail to create the sync sql connector engine!") from e
    
    async def add_record(self, data: Dict[str, Any]) -> int:
        """添加记录"""
        async with self.get_db_session() as session:
            try:
                record = self.model(**data)
                session.add(record)
                await session.flush()  # 刷新以获取ID
                record_id = record.id
                return record_id
            except Exception as e:
                error_info = f"Failed to add record: {e}"
                self.logger.error(error_info)
                self.logger.error(traceback.print_exc())
                raise ValueError(error_info) from e
    
    
    def add_record_sync(self, data: Dict[str, Any]) -> int:
        """真正的同步添加记录方法"""
        with self.get_sync_db_session() as session:
            try:
                record = self.model(**data)
                session.add(record)
                session.flush()  # 刷新以获取ID
                record_id = record.id
                return record_id
            except Exception as e:
                error_info = f"Failed to add record sync: {e}"
                self.logger.error(error_info)
                raise ValueError(error_info) from e

    # def bulk_insert_sync(self, data_list: List[Dict[str, Any]]) -> int:
    #     """同步版本的批量插入方法"""
    #     def run_in_new_loop():
    #         try:
    #             loop = asyncio.new_event_loop()
    #             asyncio.set_event_loop(loop)
                
    #             try:
    #                 result = loop.run_until_complete(self.bulk_insert_with_update(data_list))
    #                 return result
    #             finally:
    #                 loop.close()
                    
    #         except Exception as e:
    #             error_info = f"Failed to bulk insert sync: {e}"
    #             self.logger.error(error_info)
    #             raise ValueError(error_info) from e
        
    #     try:
    #         asyncio.get_running_loop()
            
    #         import concurrent.futures
    #         with concurrent.futures.ThreadPoolExecutor() as executor:
    #             future = executor.submit(run_in_new_loop)
    #             return future.result()
                
    #     except RuntimeError:
    #         return run_in_new_loop()
    
    
    
    async def bulk_insert_with_update(self, data_list: List[Dict[str, Any]]) -> int:
        """批量插入，遇到重复数据时覆盖旧数据"""
        if not data_list:
            return 0
        
        try:
            from sqlalchemy import text
            
            table_name = self.model.__tablename__
            sample_data = data_list[0]
            columns = [col for col in sample_data.keys() if col != 'id']
            columns_str = ', '.join(columns)
            
            success_count = 0
            
            async with self.get_db_session() as session:
                for data in data_list:
                    try:
                        clean_data = {k: v for k, v in data.items() if k != 'id'}
                        
                        # 构建单条插入SQL（使用新语法）
                        placeholders = ', '.join([f':{col}' for col in columns])
                        
                        # 兼容不同数据库的写法
                        if self.database_type == "mysql":
                            # MySQL 语法
                            updates = ', '.join([f'{col} = VALUES({col})' for col in columns])
                            sql = f"""
                            INSERT INTO {table_name} ({columns_str})
                            VALUES ({placeholders})
                            ON DUPLICATE KEY UPDATE {updates}
                            """
                        elif self.database_type == "postgresql":
                            # PostgreSQL 语法 (需要指定冲突字段，假设是主键 id)
                            updates_pg = ', '.join([f'{col} = EXCLUDED.{col}' for col in columns])
                            sql = f"""
                            INSERT INTO {table_name} ({columns_str})
                            VALUES ({placeholders})
                            ON CONFLICT (id) DO UPDATE SET {updates_pg}
                            """
                        
                        await session.execute(text(sql), clean_data)
                        success_count += 1
                        
                    except Exception as e:
                        self.logger.error(f"插入失败: {e}, 数据: {clean_data}")
                        continue
                
                await session.commit()
            
            self.logger.info(f"批量插入/更新完成: {success_count}/{len(data_list)} 条成功")
            return success_count
            
        except Exception as e:
            self.logger.error(f"批量插入/更新失败: {e}")
            return 0
    
    
    async def bulk_insert_with_update_bake(self, data_list: List[Dict[str, Any]]) -> int:
        """批量插入，遇到重复数据时覆盖旧数据"""
        if not data_list:
            return 0
        
        try:
            from sqlalchemy import text
            
            # 获取表名
            table_name = self.model.__tablename__
            
            # 构建字段列表（排除自增主键id）
            sample_data = data_list[0]
            columns = [col for col in sample_data.keys() if col != 'id']
            columns_str = ', '.join(columns)
            
            # 构建VALUES占位符
            values_placeholder = ', '.join([f':{col}' for col in columns])
            
            # 根据数据库类型构建不同的SQL
            if self.database_type == "mysql":
                # MySQL 语法
                update_assignments = []
                for col in columns:
                    update_assignments.append(f'{col} = VALUES({col})')
                update_str = ', '.join(update_assignments)
                
                sql = f"""
                INSERT INTO {table_name} ({columns_str})
                VALUES ({values_placeholder})
                ON DUPLICATE KEY UPDATE {update_str}
                """
            elif self.database_type == "postgresql":
                # PostgreSQL 语法
                update_assignments = []
                for col in columns:
                    update_assignments.append(f'{col} = EXCLUDED.{col}')
                update_str = ', '.join(update_assignments)
                
                sql = f"""
                INSERT INTO {table_name} ({columns_str})
                VALUES ({values_placeholder})
                ON CONFLICT (id) DO UPDATE SET {update_str}
                """
            
            success_count = 0
            async with self.get_db_session() as session:
                for data in data_list:
                    try:
                        # 移除id字段（如果存在）
                        clean_data = {k: v for k, v in data.items() if k != 'id'}
                        await session.execute(text(sql), clean_data)
                        success_count += 1
                    except Exception as e:
                        self.logger.error(f"插入失败: {e}")
                        continue
                
                await session.commit()
            
            self.logger.info(f"批量插入/更新完成: {success_count}/{len(data_list)} 条成功")
            return success_count
            
        except Exception as e:
            self.logger.error(f"批量插入/更新失败: {e}")
            return 0
    
    
    async def batch_create(self, data_list: List[Dict[str, Any]]) -> int:
        """
        [新增] 高性能纯批量插入 (Pure Batch Insert)
        特点：一次网络交互写入所有数据，速度最快。
        注意：如果有主键冲突会报错，适合全新数据的导入。
        """
        if not data_list:
            return 0
            
        # 引入 SQLAlchemy 的 insert 对象
        from sqlalchemy import insert
        
        async with self.get_db_session() as session:
            try:
                # 核心黑魔法：直接把 list 传给 execute，SQLAlchemy 会自动优化为 batch 操作
                # 注意：这里不需要写 raw sql，直接用 ORM 模型
                stmt = insert(self.model)
                
                result = await session.execute(stmt, data_list)
                await session.commit()
                
                return len(data_list)
            except Exception as e:
                await session.rollback()
                self.logger.error(f"批量插入失败: {e}")
                raise e
    
    
    async def delete_record(self, record_id: int, hard_delete: bool = False) -> bool:
        """软删除记录"""
        async with self.get_db_session() as session:
            try:
                if not hard_delete:
                    # 软删除：只查询未删除的记录，设置deleted=1
                    stmt = select(self.model).where(
                        self.model.id == record_id,
                        self.model.deleted == 0
                    )
                    result = await session.execute(stmt)
                    record = result.scalar_one_or_none()
                    if record:
                        record.deleted = 1
                        await session.commit()
                        return True
                    return False
                else:
                    # 硬删除：直接物理删除记录
                    stmt = select(self.model).where(self.model.id == record_id)
                    result = await session.execute(stmt)
                    record = result.scalar_one_or_none()
                    if record:
                        await session.delete(record)
                        await session.commit()
                        return True
                    return False
            except Exception as e:
                error_info = f"Failed to delete record: {record_id}"
                self.logger.error(error_info)
                raise ValueError(error_info) from e
    
    
    async def update_record(self, record_id: int, data: Dict[str, Any]) -> bool:
        """更新记录"""
        async with self.get_db_session() as session:
            try:
                stmt = select(self.model).where(self.model.id == record_id)
                result = await session.execute(stmt)
                record = result.scalar_one_or_none()
                if record:
                    for key, value in data.items():
                        if hasattr(record, key):
                            setattr(record, key, value)
                    await session.commit()
                    return True
                self.logger.warning(f"更新失败: 未找到ID为 {record_id} 的记录")
                return False
            except Exception as e:
                error_msg = traceback.format_exc()
                self.logger.error(f"Failed to update record {record_id}.\nData: {data}\nError: {error_msg}")
                raise e


    async def update_record_enhanced(self, record_id: int, data: Dict[str, Any], return_updated: bool = True) -> Optional[Dict[str, Any]]:
        """
        增强版更新记录函数
        
        Args:
            record_id (int): 要更新的记录ID
            data (Dict[str, Any]): 包含要更新字段的字典
            return_updated (bool): 是否返回更新后的记录，默认为True
            
        Returns:
            Optional[Dict[str, Any]]: 如果return_updated为True，返回更新后的记录字典；否则返回None
            
        Raises:
            ValueError: 当记录不存在、数据为空或更新失败时抛出
        """
        async with self.get_db_session() as session:
            try:
                if not data:
                    raise ValueError("更新数据不能为空")
                
                # 查询要更新的记录
                stmt = select(self.model).where(
                    self.model.id == record_id,
                    self.model.deleted == False
                )
                result = await session.execute(stmt)
                record = result.scalar_one_or_none()
                
                if not record:
                    raise ValueError(f"ID为 {record_id} 的记录不存在或已被删除")
                
                # 过滤掉不存在的字段
                valid_data = {}
                invalid_fields = []
                
                for key, value in data.items():
                    if hasattr(self.model, key):
                        # 跳过主键字段
                        if key != 'id':
                            valid_data[key] = value
                    else:
                        invalid_fields.append(key)
                
                if invalid_fields:
                    self.logger.warning(f"以下字段在模型中不存在，将被忽略: {invalid_fields}")
                
                if not valid_data:
                    raise ValueError("没有有效的字段需要更新")
                
                # 执行更新操作
                for key, value in valid_data.items():
                    setattr(record, key, value)
                
                # 如果需要返回更新后的记录
                if return_updated:
                    await session.commit()
                    await session.refresh(record)
                    
                    return {
                        key: value for key, value in record.__dict__.items() 
                        if key != '_sa_instance_state'
                    }
                
                return None
                
            except Exception as e:
                await session.rollback()
                error_info = f"更新记录失败 ID: {record_id}, 数据: {data}, 错误: {str(e)}"
                self.logger.error(error_info)
                raise ValueError(error_info) from e


    async def upsert_record_by_unique_field(
        self, 
        unique_field: Union[str, List[str]] = None, 
        data: Dict[str, Any] = None,
        db_model: Type[Base] = None
    ) -> Dict[str, Any]:

        db_model = self.model if db_model is None else db_model
        """
        根据唯一字段进行记录的更新或插入
        
        Args:
            unique_field (str): 用于判断记录唯一性的字段名
            data (Dict[str, Any]): 要插入或更新的数据字典
            db_model (Type[Base]): 数据库模型类
        
        Returns:
            Dict[str, Any]: 插入或更新后的记录
        """
        
        def convert_numpy_types(value):
            """转换numpy数据类型为Python原生类型"""
            if isinstance(value, np.integer):
                return int(value)
            elif isinstance(value, np.floating):
                return float(value)
            elif isinstance(value, np.ndarray):
                return value.tolist()
            elif isinstance(value, np.bool_):
                return bool(value)
            return value
        
        
        async with self.get_db_session() as session:
            try:
                
                if not data:
                    raise ValueError("Empty data dictionary provided")
                
                # 转换数据类型
                converted_data = {
                    key: convert_numpy_types(value)
                    for key, value in data.items()
                }

                # 将单个字段转换为列表，统一处理
                unique_fields = [unique_field] if isinstance(unique_field, str) else unique_field
                
                # 检查唯一字段是否存在于模型中
                for field in unique_fields:
                    if not hasattr(db_model, field):
                        raise ValueError(f"Unique field {field} not found in model")
                
                # 构建唯一键的查询条件
                filter_conditions = []
                for field in unique_fields:
                    field_value = converted_data.get(field)
                    if field_value is None:
                        raise ValueError(f"Unique field {field} value is None")
                    filter_conditions.append(getattr(db_model, field) == field_value)
                
                # 添加未删除条件
                filter_conditions.append(db_model.deleted == False)
                
                # 查询是否存在记录
                stmt = select(db_model).where(and_(*filter_conditions))
                result = await session.execute(stmt)
                existing_record = result.scalar_one_or_none()
                
                # 构建要更新的数据字典
                valid_data = {
                    key: value 
                    for key, value in converted_data.items() 
                    if hasattr(db_model, key) and key != 'id'  # 排除id和不存在的字段
                }
                if not valid_data:
                    raise ValueError("No valid fields to update")
                
                # 如果记录已存在，更新记录
                if existing_record:
                    for key, value in valid_data.items():
                        setattr(existing_record, key, value)
                    record = existing_record
                
                # 如果记录不存在，创建新记录
                else:
                    # 移除可能的id字段，防止主键冲突
                    record = db_model(**valid_data)
                    session.add(record)
                
                # 提交事务
                await session.commit()
                await session.refresh(record)
                
                # 转换为字典返回
                result = {}
                for key in valid_data.keys():
                    value = getattr(record, key)
                    # 处理SQLAlchemy对象关系
                    if hasattr(value, '__table__'):
                        continue  # 跳过关联对象
                    result[key] = value
                
                return result
            except Exception as e:
                await session.rollback()
                if isinstance(unique_field, str):
                    error_info = f"Failed to upsert record with {unique_field}={data.get(unique_field)}"
                else:
                    unique_values = {field: data.get(field) for field in unique_field}
                    error_info = f"Failed to upsert record with unique fields: {unique_values}"
                self.logger.error(f"{error_info}. Error: {str(e)}")
                raise ValueError(error_info) from e
    

    async def get_record_by_id(self, record_id: int) -> Optional[Dict[str, Any]]:
        """根据ID查询记录"""
        async with self.get_db_session() as session:
            try:
                stmt = select(self.model).where(
                    self.model.id == record_id,
                    # self.model.deleted == False
                )
                result = await session.execute(stmt)
                record = result.scalar_one_or_none()
                return {key: value for key, value in record.__dict__.items() 
                        if key != '_sa_instance_state'} if record else None
            except Exception as e:
                error_info = f"Failed to get record by id: {record_id}"
                self.logger.error(error_info)
                raise ValueError(error_info) from e
    
    
    async def get_record_by_condition_bake(
        self, 
        condition: Optional[Dict[str, Any]],
        fields: Optional[List[str]] = None,
        exclude_fields: Optional[List[str]] = None,
        date_range: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        async with self.get_db_session() as session:
            try:
                
                # 获取模型的所有字段
                all_fields = [column.key for column in self.model.__table__.columns]
                
                if fields:
                    # 如果指定了字段，只查询指定字段
                    query_fields = fields
                else:
                    query_fields = all_fields
                
                # 排除不需要的字段
                if exclude_fields:
                    query_fields = [f for f in query_fields if f not in exclude_fields]
                    
                # 构建查询条件
                if len(query_fields) == len(all_fields):
                    stmt = select(self.model)
                else:
                    stmt = select(*[getattr(self.model, field) for field in query_fields])
                
                # 添加未删除条件
                stmt = stmt.where(self.model.deleted == False)

                # Apply filters based on the provided condition
                if condition:
                    for key, value in condition.items():
                        # Assuming that keys in condition match the model's attributes
                        stmt = stmt.where(getattr(self.model, key) == value)

                # Apply date range filter
                if date_range:
                    date_field = date_range.get('date_field')
                    start_date_str = date_range.get('start_date')
                    end_date_str = date_range.get('end_date')

                    if not date_field:
                        raise ValueError("date_field must be specified for date range filtering")

                    # Convert string dates to datetime objects
                    try:
                        if start_date_str:
                            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
                            stmt = stmt.where(getattr(self.model, date_field) >= start_date)
                        
                        if end_date_str:
                            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
                            stmt = stmt.where(getattr(self.model, date_field) <= end_date)
                    except ValueError as e:
                        raise ValueError(f"Invalid date format. Use YYYY-MM-DD. {str(e)}")


                # 执行查询
                result = await session.execute(stmt)
                records = result.fetchall()

                # 处理查询结果
                if not records:
                    return []
                
                # 返回查询结果
                if len(query_fields) == len(all_fields):
                    return [
                        {key: value for key, value in record[0].__dict__.items() 
                         if key != '_sa_instance_state'}
                        for record in records
                    ]
                else:
                    return [dict(zip(query_fields, record)) for record in records]
                    
            except Exception as e:
                error_info = f"Failed to get records by condition: {condition}"
                self.logger.error(error_info)
                raise ValueError(error_info) from e
    
    
    async def get_record_by_condition(
        self, 
        condition: Optional[Dict[str, Any]] = None,
        fields: Optional[List[str]] = None,
        exclude_fields: Optional[List[str]] = None,
        date_range: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """
        增强版条件查询函数 - 支持精确到秒的时间查询
        
        Args:
            condition: 查询条件字典 {'device_id': 'DEV001', 'state': '呼吸暂停'}
            fields: 指定返回字段列表 ['timestamp', 'state', 'heart_bpm']
            exclude_fields: 排除字段列表 ['id', 'create_time']
            date_range: 日期范围查询
                {
                    'date_field': 'timestamp',  # 日期字段名
                    'start_date': '2025-06-27 15:30:45',  # 开始时间
                    'end_date': '2025-06-27 16:30:45'     # 结束时间
                }
        
        支持的时间格式：
            - '2025-06-27 15:30:45' (精确到秒)
            - '2025-06-27 15:30' (精确到分钟)
            - '2025-06-27' (整天范围)
            - '1751011266.382772' (时间戳)
        
        Returns:
            List[Dict]: 查询结果列表
        """
        async with self.get_db_session() as session:
            try:
                # 获取模型的所有字段
                all_fields = [column.key for column in self.model.__table__.columns]
                
                if fields:
                    # 如果指定了字段，只查询指定字段
                    query_fields = fields
                else:
                    query_fields = all_fields
                
                # 排除不需要的字段
                if exclude_fields:
                    query_fields = [f for f in query_fields if f not in exclude_fields]
                    
                # 构建查询条件 - 使用 select() 而不是 session.query()
                if len(query_fields) == len(all_fields):
                    # 如果查询所有字段，直接使用模型
                    stmt = select(self.model)
                else:
                    # 如果只查询部分字段，使用字段列表
                    stmt = select(*[getattr(self.model, field) for field in query_fields])

                # 应用基础查询条件
                if condition:
                    for key, value in condition.items():
                        if key == 'deleted' and isinstance(value, bool):
                            value = 1 if value else 0
                        # 支持范围查询
                        if isinstance(value, dict) and ('min' in value or 'max' in value):
                            field_attr = getattr(self.model, key)
                            if 'min' in value:
                                stmt = stmt.where(field_attr >= value['min'])
                            if 'max' in value:
                                stmt = stmt.where(field_attr <= value['max'])
                        # 支持列表查询 (IN 操作)
                        elif isinstance(value, (list, tuple)):
                            stmt = stmt.where(getattr(self.model, key).in_(value))
                        # 普通等值查询
                        else:
                            stmt = stmt.where(getattr(self.model, key) == value)

                # 增强版日期范围过滤
                if date_range:
                    date_field = date_range.get('date_field')
                    start_date_str = date_range.get('start_date')
                    end_date_str = date_range.get('end_date')

                    if not date_field:
                        raise ValueError("date_field must be specified for date range filtering")

                    try:
                        if start_date_str:
                            start_date = self._parse_datetime_unified(start_date_str, is_end_date=False)
                            stmt = stmt.where(getattr(self.model, date_field) >= start_date)
                        
                        if end_date_str:
                            end_date = self._parse_datetime_unified(end_date_str, is_end_date=True)
                            stmt = stmt.where(getattr(self.model, date_field) <= end_date)
                    except ValueError as e:
                        raise ValueError(f"Invalid date format. {str(e)}")

                # 执行异步查询
                result = await session.execute(stmt)
                
                return result.scalars().all()

                """
                records = result.fetchall()

                # 处理查询结果
                if not records:
                    return []
                
                # 返回查询结果
                if len(query_fields) == len(all_fields):
                    # 如果查询的是完整模型，转换为字典
                    return [
                        {key: value for key, value in record[0].__dict__.items() 
                         if key != '_sa_instance_state'}
                        for record in records
                    ]
                else:
                    # 如果查询的是部分字段
                    return [dict(zip(query_fields, record)) for record in records]
                """
                
            except Exception as e:
                import traceback
                error_info = f"Failed to get records by condition: {condition}, \n {traceback.format_exc()}"
                self.logger.error(error_info)
                raise ValueError(f"{error_info}") from e


    async def get_records_paginated(
        self, 
        page: int, 
        page_size: int,
        condition: Optional[Dict[str, Any]] = None, # 用于简单 K=V 查询
        filters: Optional[List[Any]] = None,        # 用于接收复杂的 SQLAlchemy 表达式
        fields: Optional[List[str]] = None,
        exclude_fields: Optional[List[str]] = None,
        date_range: Optional[Dict[str, str]] = None,
        order_by: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        [新增] 专用的分页查询方法
        返回: {'items': [...], 'total': 100, 'page': 1, 'size': 10}
        """
        async with self.get_db_session() as session:
            try:
                # 1. 复用之前的逻辑构建 stmt (建议将构建逻辑提取为私有方法 _build_stmt，这里为了方便直接复制核心逻辑)
                # -------------------------------------------------------------------------
                all_fields = [column.key for column in self.model.__table__.columns]
                if fields:
                    query_fields = fields
                else:
                    query_fields = all_fields
                if exclude_fields:
                    query_fields = [f for f in query_fields if f not in exclude_fields]

                # 构建 Select 语句
                if len(query_fields) == len(all_fields):
                    stmt = select(self.model)
                else:
                    stmt = select(*[getattr(self.model, field) for field in query_fields])

                # 应用条件
                if condition:
                    for key, value in condition.items():
                        if key == 'deleted' and isinstance(value, bool):
                            value = 1 if value else 0
                        
                        if isinstance(value, dict) and ('min' in value or 'max' in value):
                            field_attr = getattr(self.model, key)
                            if 'min' in value: stmt = stmt.where(field_attr >= value['min'])
                            if 'max' in value: stmt = stmt.where(field_attr <= value['max'])
                        elif isinstance(value, (list, tuple)):
                            stmt = stmt.where(getattr(self.model, key).in_(value))
                        else:
                            stmt = stmt.where(getattr(self.model, key) == value)
                
                # 应用复杂的 SQLAlchemy 表达式 (filters)
                if filters:
                    for expr in filters:
                        stmt = stmt.where(expr)
                
                # 应用时间范围
                if date_range:
                    # ... (这里保留你原本的时间范围逻辑) ...
                    pass
                
                # -------------------------------------------------------------------------

                # 2. [核心] 计算总数 (Total Count)
                # 使用 subquery 保证统计的是过滤后的数量
                count_stmt = select(func.count()).select_from(stmt.subquery())
                total_result = await session.execute(count_stmt)
                total = total_result.scalar()

                # 3. [核心] 应用分页 (Limit / Offset)
                if page is not None and page_size is not None:
                    if page < 1: page = 1
                    offset = (page - 1) * page_size
                # 加上排序，防止分页数据乱序 (建议按 ID 或创建时间倒序)
                # --- [修改] 排序逻辑 ---
                    if order_by is not None:
                        # 如果传入了自定义排序，直接使用
                        stmt = stmt.order_by(order_by)
                    elif hasattr(self.model, 'create_time'):
                        # 默认逻辑
                        stmt = stmt.order_by(self.model.create_time.desc())
                    elif hasattr(self.model, 'id'):
                        # 默认逻辑
                        stmt = stmt.order_by(self.model.id.desc())
                        
                    stmt = stmt.offset(offset).limit(page_size)
                else:
                    if order_by is not None:
                        stmt = stmt.order_by(order_by)
                    elif hasattr(self.model, 'create_time'):
                        stmt = stmt.order_by(self.model.create_time.desc())
                    elif hasattr(self.model, 'id'):
                        stmt = stmt.order_by(self.model.id.desc())

                # 4. 执行查询
                result = await session.execute(stmt)
                records = result.scalars().all()
                
                """
                records = result.fetchall()
                # 5. 格式化数据
                data_list = []
                if len(query_fields) == len(all_fields):
                     data_list = [
                        {key: value for key, value in record[0].__dict__.items() 
                         if key != '_sa_instance_state'}
                        for record in records
                    ]
                else:
                    data_list = [dict(zip(query_fields, record)) for record in records]
                """

                # 6. 返回分页结构
                return {
                    "items": records,
                    "total": total,
                    "page": page,
                    "size": page_size
                }
            except Exception as e:
                self.logger.error(f"分页查询异常: {str(e)}")
                raise e
    
    
    async def get_with_relations(
        self, 
        record_id: int, 
        relations: List[str]
    ) -> Optional[ModelType]:
        """
        [修改版] 查询并返回"游离态"对象，解决 Session 关闭后的访问报错问题
        返回单个实例化对象
        """
        async with self.get_db_session() as session:
            try:
                # 1. 基础查询
                # 注意：如果你之前报错 'no attribute deleted'，请确保这里注释掉了 deleted 检查，或者 Model 里加了字段
                stmt = select(self.model).where(self.model.id == record_id)
                
                # 2. 动态添加加载选项 (Eager Loading)
                for relation_name in relations:
                    if hasattr(self.model, relation_name):
                        stmt = stmt.options(selectinload(getattr(self.model, relation_name)))
                
                # 3. 执行查询
                result = await session.execute(stmt)
                record = result.scalar_one_or_none()
                
                # === [核心修改] 剥离对象 (Expunge) ===
                if record:
                    # 这句话的意思是：切断 record 与 session 的脐带。
                    # 此时 record 变成了一个普通的 Python 对象，数据都在内存里。
                    # 即使 session 关闭了，你依然可以随意访问 doc.bucket_name, doc.cover 等属性。
                    session.expunge(record)
                
                return record
                
            except Exception as e:
                self.logger.error(f"Failed to get record with relations: {str(e)}")
                raise e
    
    
    async def get_group_counts(self, group_by_field: str) -> dict:
        """
        统计某个字段的分组数量
        返回示例: {0: 5, 100: 3, -1: 1}  (状态码: 数量)
        """
        async with self.get_db_session() as session:
            try:
                # 相当于: SELECT status, COUNT(*) FROM table GROUP BY status
                stmt = select(getattr(self.model, group_by_field), func.count())\
                       .group_by(getattr(self.model, group_by_field))
                
                result = await session.execute(stmt)
                # 将结果转换为字典
                return dict(result.fetchall())
            except Exception as e:
                self.logger.error(f"分组统计失败: {str(e)}")
                raise e
    
        
    def _parse_datetime_unified(self, datetime_str: str, is_end_date: bool = False) -> datetime:
        """
        统一的日期时间解析方法，支持多种格式
        
        支持的格式：
        - '2025-06-27' → 2025-06-27 00:00:00 (开始) 或 2025-06-27 23:59:59 (结束)
        - '2025-06-27 15:30:45' → 2025-06-27 15:30:45
        - '2025-06-27 15:30' → 2025-06-27 15:30:00
        - '1751011266.382772' → 时间戳转换
        - '1751011266' → 整数时间戳转换
        
        Args:
            datetime_str: 时间字符串
            is_end_date: 是否为结束时间（影响只有日期时的处理）
            
        Returns:
            datetime: 解析后的datetime对象
        """
        # 尝试解析时间戳（浮点数）
        try:
            timestamp = float(datetime_str)
            return datetime.fromtimestamp(timestamp)
        except ValueError:
            pass
        
        # 尝试解析整数时间戳
        try:
            timestamp = int(datetime_str)
            return datetime.fromtimestamp(timestamp)
        except ValueError:
            pass
        
        # 定义支持的日期格式（按精确度排序）
        formats = [
            '%Y-%m-%d %H:%M:%S.%f',  # 2025-06-27 15:30:45.123456
            '%Y-%m-%d %H:%M:%S',     # 2025-06-27 15:30:45
            '%Y-%m-%d %H:%M',        # 2025-06-27 15:30
            '%Y-%m-%d',              # 2025-06-27
            '%Y/%m/%d %H:%M:%S',     # 2025/06/27 15:30:45
            '%Y/%m/%d %H:%M',        # 2025/06/27 15:30
            '%Y/%m/%d',              # 2025/06/27
        ]
        
        for fmt in formats:
            try:
                parsed_date = datetime.strptime(datetime_str, fmt)
                
                # 如果只有日期，需要特殊处理
                if fmt in ['%Y-%m-%d', '%Y/%m/%d']:
                    if is_end_date:
                        # 结束日期：设置为当天的23:59:59.999999
                        parsed_date = parsed_date.replace(hour=23, minute=59, second=59, microsecond=999999)
                    # 开始日期：保持00:00:00（默认）
                
                return parsed_date
                
            except ValueError:
                continue
        
        # 所有格式都失败
        raise ValueError(
            f"Unsupported datetime format: '{datetime_str}'. "
            f"Supported formats: 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM', 'YYYY-MM-DD HH:MM:SS', "
            f"'YYYY-MM-DD HH:MM:SS.fff', 'YYYY/MM/DD...', or timestamp"
        )

    
    def get_field_names_and_descriptions(self) -> Dict[str, str]:
        field_info = {}
        # 获取模型的所有字段
        for column in self.model.__table__.columns:
            # 假设中文描述存储在列的 doc 属性中
            # 如果没有中文描述，可以使用其他方法来获取
            field_info[column.name] = column.comment  if column.comment else "无描述"
        return field_info
    
    
    async def update_deep_health_advice_by_id(self, record_id: int, new_health_advice) -> Optional[Dict[str, Any]]:
        async with self.get_db_session() as session:
            try:
                # 查询要更新的记录
                stmt = select(self.model).where(
                    self.model.id == record_id, 
                    self.model.deleted == False
                )
                result = await session.execute(stmt)
                record = result.scalar_one_or_none()
                
                if record is None:
                    error_info = f"Record with ID {record_id} not found."
                    self.logger.error(error_info)
                    raise ValueError(error_info)

                # 更新字段
                record.deep_health_advice = new_health_advice
                
                # 提交更改
                await session.commit()
                
                # 返回更新后的记录（可选）
                return {key: value for key, value in record.__dict__.items() if key != '_sa_instance_state'}
            
            except Exception as e:
                error_info = f"Failed to update health advice for record ID {record_id}: {str(e)}"
                self.logger.error(error_info)
                raise ValueError(error_info) from e
    
    
    async def delete_records_by_condition(self, condition: Dict[str, Any]) -> int:
        """
        按照指定条件硬删除多条记录（永久从数据库中删除）
        
        Args:
            condition (Dict[str, Any]): 删除条件，格式为 {字段名: 值}
        
        Returns:
            int: 成功删除的记录数量
        """
        async with self.get_db_session() as session:
            try:
                # 构建查询条件
                stmt = select(self.model)
                
                # 添加条件过滤，忽略不存在的字段
                valid_conditions = {}
                for key, value in condition.items():
                    if hasattr(self.model, key):
                        stmt = stmt.where(getattr(self.model, key) == value)
                        valid_conditions[key] = value
                    else:
                        self.logger.warning(f"Field '{key}' not found in model, ignoring this condition")
                
                if not valid_conditions:
                    self.logger.warning("No valid conditions found, no records will be deleted")
                    return 0
                    
                # 获取要删除的记录
                result = await session.execute(stmt)
                records_to_delete = result.scalars().all()
                count_to_delete = len(records_to_delete)
                
                # 执行硬删除操作
                for record in records_to_delete:
                    await session.delete(record)
                
                await session.commit()
                return count_to_delete
                
            except Exception as e:
                error_info = f"Failed to delete records by condition: {condition}"
                self.logger.error(error_info)
                self.logger.error(traceback.format_exc())
                raise ValueError(error_info) from e
    
    
    async def exec_sql(self, query: Optional[str] = None):
        """query words check data"""
        async with self.get_db_session() as session:
            try:
                result = await session.execute(text(query))
                records = result.fetchall()
                await session.commit()
                
                if records is not None:
                    # 将记录转换为 NumPy 数组
                    numpy_array = np.array(records)
                    return numpy_array
                return records
            except Exception as e:
                await session.rollback()        
                error_info = f"Failed to execute SQL query: {query}!"
                self.logger.error(f"{traceback.format_exc()}\n{error_info}")
                raise ValueError(error_info) from e
