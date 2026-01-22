#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/12 12:08
@Author  : weiyutao
@File    : minio_config.py
"""

from .base import BaseConfig, Field

class MinioConfig(BaseConfig):
    host: str = "minio:9000"
    username: str = "admin"
    password: str = "password123"
    
    @property
    def minio_port(self) -> int:
        """
        从 host 中提取端口。
        如果 host 是 "minio:9000" -> 返回 9000
        如果 host 是 "minio"      -> 返回默认值 9000
        """
        # 1. 先去掉可能存在的 http:// 或 https:// 前缀
        clean_host = self.host.split("://")[-1]

        # 2. 判断是否有冒号
        if ":" in clean_host:
            try:
                # 取冒号后面那部分转成 int
                return int(clean_host.split(":")[-1])
            except ValueError:
                # 防止出现 minio:abc 这种奇怪的配置
                return 9000
        
        # 3. 如果没写端口，默认返回 9000
        return 9000
    
    @property
    def minio_public_url(self) -> str:
        """
        """
        return f"http://localhost:{self.minio_port}"
