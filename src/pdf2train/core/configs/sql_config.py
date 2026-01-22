#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/12 12:06
@Author  : weiyutao
@File    : postgresql_config.py
"""
import urllib.parse

from .base import BaseConfig, Field

class SqlConfig(BaseConfig):
    host: str = "postgres" 
    port: str = "5432"  # 也可以兼容 int
    username: str = "admin"
    password: str = "password123"
    database: str = "file_metadata"
    database_type: str = "postgres"
    
    @property
    def sql_url(self) -> str:
        """
        根据数据库类型返回对应的异步连接URL
        """
        # 统一转换为小写并去空格，防止配置手误
        db_type = self.database_type.lower().strip()

        if db_type in ['postgresql', 'postgres']:
            return self.postgres_url
        elif db_type == 'mysql':
            return self.mysql_url
        elif db_type == 'sqlite':
            # SQLite 异步连接示例: sqlite+aiosqlite:///./test.db
            return f"sqlite+aiosqlite:///{self.database}"
        else:
            raise ValueError(f"不支持的数据库类型: {self.database_type}")

    @property
    def postgres_url(self) -> str:
        encoded_password = urllib.parse.quote_plus(self.password)
        # 确保 port 是字符串，防止 int 报错（虽然 f-string 通常没问题，但为了稳健）
        port_str = str(self.port)
        
        if not all([self.host, port_str, self.username, self.password, self.database]):
            raise ValueError("PostgreSQL连接缺少必要参数")
            
        return f"postgresql+asyncpg://{self.username}:{encoded_password}@{self.host}:{port_str}/{self.database}"

    @property
    def mysql_url(self) -> str:
        encoded_password = urllib.parse.quote_plus(self.password)
        port_str = str(self.port)
        
        if not all([self.host, port_str, self.username, self.password, self.database]):
            raise ValueError("MySQL连接缺少必要参数")
            
        # 注意：MySQL 这里的 charset=utf8mb4 很重要，防止中文乱码
        return f"mysql+aiomysql://{self.username}:{encoded_password}@{self.host}:{port_str}/{self.database}?charset=utf8mb4"