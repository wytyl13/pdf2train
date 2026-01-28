#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/26 19:28
@Author  : weiyutao
@File    : export_utils.py
"""

import json
from io import BytesIO
from typing import List, Dict, Any

def list_to_jsonl_stream(data_list: List[Dict[str, Any]]) -> BytesIO:
    """
    [工具] 将字典列表转换为 JSONL 格式的二进制流
    该函数纯粹进行数据格式转换，没有任何业务逻辑
    """
    output = BytesIO()
    if not data_list:
        return output
        
    for item in data_list:
        # ensure_ascii=False
        line = json.dumps(item, ensure_ascii=False) + "\n"
        output.write(line.encode("utf-8"))
    
    output.seek(0)
    return output