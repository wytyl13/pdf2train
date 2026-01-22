#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/21 08:36
@Author  : weiyutao
@File    : pipline_task_router.py
"""

from fastapi import APIRouter, Depends, Query
from pdf2train.api.schema.pipeline_task_schema import *
from pdf2train.core.manager.pipeline_task_manager import PipelineTaskManager

router = APIRouter(prefix="/api/pipeline", tags=["Pipeline Task"])


