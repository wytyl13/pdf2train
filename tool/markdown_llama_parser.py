#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/21 12:39
@Author  : weiyutao
@File    : markdown_llama_parser.py
"""

import json
from typing import List, Dict
from llama_index.core import Document
from llama_index.core.node_parser import MarkdownNodeParser

class MarkdownLlamaParser:
    def __init__(self):
        # 初始化 LlamaIndex 解析器
        self.parser = MarkdownNodeParser()

    def parse(self, md_content: str, file_name: str) -> str:
        """
        读取 MD 内容，输出扁平化的 JSON 列表
        包含：文件名、各级标题、内容、内容长度
        """
        # 1. 封装 Document 对象
        # LlamaIndex 的 Document 对象支持传入 extra_info (即 metadata)
        # 我们先把 file_name 塞进去，方便后续统一提取
        document = Document(
            text=md_content, 
            metadata={"file_name": file_name}
        )

        # 2. 解析获取节点 (Nodes)
        nodes = self.parser.get_nodes_from_documents([document])
        output_data = []

        # 3. 遍历并提取
        for node in nodes:
            content = node.text.strip()
            if not content: continue 

            meta = node.metadata
            
            # --- 核心适配逻辑：解析 header_path ---
            # 你的 metadata 样例：{'file_name': '...', 'header_path': '/第二章 农药类型/3 除草剂/'}
            header_path_str = meta.get("header_path", "")
            
            # 1. 去除首尾的斜杠，然后按斜杠分割
            # 例如 "/A/B/" -> ["A", "B"]
            headers = [h for h in header_path_str.strip('/').split('/') if h]
            
            # 2. 构造数据字典
            item = {
                "file_name": meta.get("file_name", file_name),
                
                # 安全地按索引取值，如果列表不够长，就填空字符串
                "一级标题": headers[0] if len(headers) > 0 else "",
                "二级标题": headers[1] if len(headers) > 1 else "",
                "三级标题": headers[2] if len(headers) > 2 else "",
                "四级标题": headers[3] if len(headers) > 3 else "",
                "五级标题": headers[4] if len(headers) > 4 else "",
                
                "内容": content,
                "content_length": len(content)
            }
            
            output_data.append(item)

        return json.dumps(output_data, ensure_ascii=False, indent=2)

# ================= 使用示例 =================
if __name__ == "__main__":
    # 模拟数据
    md_file_path = "/work/ai/pdf2train/docs/农药学原理.md"
    
    with open(md_file_path, 'r', encoding='utf-8') as f:
        lines = f.read()
    mock_filename = "农药学原理"
    mock_content = lines

    parser = MarkdownLlamaParser()
    result = parser.parse(mock_content, mock_filename)
    
    print(result)