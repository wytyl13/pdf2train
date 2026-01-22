#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/12 12:11
@Author  : weiyutao
@File    : config.py
"""

import os
from pathlib import Path
from typing import Optional

from .configs import (
    LLMConfig, 
    MinioConfig,
    SqlConfig
)


class CoreConfig:
    llm: LLMConfig
    minio_config: MinioConfig
    sql_config: SqlConfig
    sql_config_test: SqlConfig
    
    def __init__(self):
        # 1. 解析 LLM 配置路径
        llm_path = self._resolve_config_path(
            filename="deepseek_config.yaml",
            env_key="LLM_CONFIG"
        )
        print(f"[PDF2Train] LLM Config Path: {llm_path}")
        
        # 2. 解析 Minio 配置路径
        minio_path = self._resolve_config_path(
            filename="minio_config.yaml",
            env_key="MINIO_CONFIG"
        )
        print(f"[PDF2Train] Minio Config Path: {minio_path}")
        
        # 3 解析 SQL 配置路径
        sql_path = self._resolve_config_path("postgresql_config.yaml", "POSTGRESQL_CONFIG")
        print(f"[PDF2Train] SQL Config Path: {sql_path}")

        # 4 解析 SQL TEST 配置路径
        sql_path_test = self._resolve_config_path("postgresql_config_test.yaml", "POSTGRESQL_CONFIG_TEST")
        print(f"[PDF2Train] SQL Config Path: {sql_path_test}")

        
        # 3. 加载配置 (如果找不到路径，from_yaml 会处理成默认值，或者这里传 None 也可以)
        self.llm = LLMConfig.from_yaml(llm_path) if llm_path else LLMConfig()
        self.minio_config = MinioConfig.from_yaml(minio_path) if minio_path else MinioConfig()
        self.sql_config = SqlConfig.from_yaml(sql_path) if sql_path else SqlConfig()
        self.sql_config_test = SqlConfig.from_yaml(sql_path_test) if sql_path_test else SqlConfig()

        if llm_path: print(f"[PDF2Train] Loaded LLM Config: {llm_path}")
        if minio_path: print(f"[PDF2Train] Loaded Minio Config: {minio_path}")
        if sql_path: print(f"[PDF2Train] Loaded PS Config: {sql_path}")
        if sql_path_test: print(f"[PDF2Train] Loaded PS TEST Config: {sql_path_test}")

    def _resolve_config_path(self, filename: str, env_key: str) -> Optional[str]:
        """
        按照 环境变量 -> 源码目录 -> CWD逐级向上 的顺序寻找配置文件
        返回找到的第一个绝对路径，未找到返回 None
        """
        
        # ---------------------------------------------------------
        # 优先级 1: 环境变量 (绝对路径)
        # ---------------------------------------------------------
        env_path = os.getenv(env_key)
        if env_path:
            p = Path(env_path).resolve()
            if p.exists() and p.is_file():
                return str(p)

        # ---------------------------------------------------------
        # 优先级 2: 源码目录 (开发模式)
        # 逻辑：config.py -> core -> wangeng -> configs/filename
        # ---------------------------------------------------------
        current_file = Path(__file__).resolve()
        # .parent = core, .parent.parent = wangeng
        source_root = current_file.parent.parent.parent.parent
        source_path = source_root / "configs" / filename
        
        if source_path.exists():
            return str(source_path)

        # ---------------------------------------------------------
        # 优先级 3: 用户运行目录逐级向上 (至多3级)
        # 逻辑：CWD -> .. -> ../.. -> ../../..
        # ---------------------------------------------------------
        cwd = Path.cwd()
        search_dirs = [cwd] + list(cwd.parents)[:3]
        for directory in search_dirs:
            check_path = directory / filename
            if check_path.exists():
                return str(check_path.resolve())
        return None
# 实例化
core_config = CoreConfig()  