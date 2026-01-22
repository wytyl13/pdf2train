#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/27 16:19
@Author  : weiyutao
@File    : h1_context_assembler.py
"""

import json
from typing import List, Dict, Any, Tuple
from itertools import groupby

class H1ContextAssembler:
    def __init__(self):
        pass

    def process(self, raw_chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        主入口函数：接收原始 JSON 列表，返回处理好的 H1 聚合列表。
        """
        # 1. 预排序：必须确保按照 document_id 和 chunk_index 排序，
        # 否则 groupby 无法正确聚合相邻的同章节内容
        sorted_chunks = sorted(
            raw_chunks, 
            key=lambda x: (x.get('document_id', 0), x.get('chunk_index', 0))
        )

        # 2. 分组：按照 metadata['h1'] 进行聚合
        grouped_results = []
        
        # itertools.groupby 需要数据已排序，或者我们手动遍历。
        # 这里为了稳健，使用手动遍历逻辑，把属于同一个 H1 的装到一个桶里
        
        current_h1_buffer = []
        current_h1_title = None
        
        # 为了处理列表中的第一项，初始化逻辑
        if sorted_chunks:
            current_h1_title = sorted_chunks[0]['metadata'].get('h1', '')

        for chunk in sorted_chunks:
            chunk_h1 = chunk['metadata'].get('h1', '')
            
            if chunk_h1 != current_h1_title:
                # 发现新章节 -> 结算上一章节
                if current_h1_buffer:
                    processed_chapter = self._serialize_chapter(current_h1_title, current_h1_buffer)
                    grouped_results.append(processed_chapter)
                
                # 重置缓冲区
                current_h1_buffer = [chunk]
                current_h1_title = chunk_h1
            else:
                # 同一章节 -> 加入缓冲区
                current_h1_buffer.append(chunk)
        
        # 循环结束，结算最后一桶
        if current_h1_buffer:
            processed_chapter = self._serialize_chapter(current_h1_title, current_h1_buffer)
            grouped_results.append(processed_chapter)
        print("===================================== grouped_results =====================================")
        print(grouped_results)
        print("===================================== grouped_results =====================================")
        return grouped_results

    def _serialize_chapter(self, h1_title: str, chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        将属于同一个 H1 的 chunk 列表转换为 XML Prompt 文本
        """
        prompt_lines = []
        id_mapping = {} # 用于存储 短ID -> 原始UUID 的映射，方便后续溯源
        
        # 添加头部信息
        prompt_lines.append(f"Document Chapter: {h1_title}")
        prompt_lines.append("[Context Chunks]:")
        print("============================== current_h1_buffer ================================== ")
        print(chunks)
        print("============================== current_h1_buffer ================================== ")
        for index, chunk in enumerate(chunks):
            # 1. 生成短 ID (0, 1, 2...) 用于给大模型引用，节省 token
            short_id = str(index)
            original_uuid = chunk.get('id')
            id_mapping[short_id] = original_uuid
            
            # 2. 构建面包屑路径 (Breadcrumb)
            path_str = self._build_breadcrumb(chunk['metadata'])
            
            # 3. 提取内容并去除多余空白
            content = chunk.get('content', '').strip()
            
            # 4. 组装 XML 格式
            # 格式：<chunk id="0" path="H1 > H2...">Content</chunk>
            xml_block = f'<chunk id="{short_id}" path="{path_str}">\n{content}\n</chunk>'
            prompt_lines.append(xml_block)
            
        return {
            "h1_title": h1_title,
            "prompt_text": "\n\n".join(prompt_lines), # 生成最终发给 LLM 的文本
            "chunk_count": len(chunks),
            "id_mapping": id_mapping # 这是一个必须保存的字典，用来解码 LLM 的答案
        }

    def _build_breadcrumb(self, metadata: Dict[str, Any]) -> str:
        """
        根据 metadata 生成路径字符串: "H1 > H2 > H3"
        """
        levels = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']
        path_components = []
        
        for level in levels:
            title = metadata.get(level)
            if title and isinstance(title, str) and title.strip():
                path_components.append(title.strip())
        
        return " > ".join(path_components)