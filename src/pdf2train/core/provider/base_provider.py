#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2024/12/24 11:36
@Author  : weiyutao
@File    : base_provider.py
"""

from abc import ABC, abstractmethod
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
)
import logging

class BaseProvider(ABC):
    def __init__(self, name: Optional[str] = None):
        """
        普通类的初始化，简单直接
        """
        self.logger = logging.getLogger(self.__class__.__name__)