#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/21 13:19
@Author  : weiyutao
@File    : markdown_parser.py
"""


import re
from typing import Dict, List, Tuple, Dict
from llama_index.core.schema import TextNode, Document
from llama_index.core.node_parser import NodeParser, SentenceSplitter
from llama_index.core.bridge.pydantic import Field, PrivateAttr
import os


class HybridMarkdownParser(NodeParser):
    """
    自定义严格 Markdown 解析器。
    逻辑：遇到任何层级的标题 (#) 就强制切分，绝不合并。
    """
    text_splitter: SentenceSplitter = Field(
        description="用于长文本二次切分的切分器"
    )
    min_content_length: int = Field(default=50, description="过滤阈值，低于此长度的内容将被丢弃")
    _image_pattern: re.Pattern = PrivateAttr()
    
    def __init__(self, chunk_size: int = 900, chunk_overlap: int = 90, min_content_length: int = 90, **kwargs):
        text_splitter = SentenceSplitter(
            chunk_size=chunk_size, 
            chunk_overlap=chunk_overlap
        )
        super().__init__(text_splitter=text_splitter, min_content_length=min_content_length, **kwargs)
        # 初始化一个从句切分器，用于处理长文本
        self._header_pattern = re.compile(r'^(#{1,6})\s+(.*)')
        self._image_pattern = re.compile(r'!\[(.*?)\]\((.*?)\)')
    
    def _parse_nodes(self, nodes: List[Document], show_progress: bool = False, **kwargs) -> List[TextNode]:
        # LlamaIndex 内部接口要求实现 _parse_nodes
        # 但我们这里简化逻辑，主要对外提供 get_nodes_from_documents
        return self.get_nodes_from_documents(nodes)

    def get_nodes_from_documents(self, documents: List[Document]) -> List[TextNode]:
        all_nodes = []
        for doc in documents:
            nodes = self._parse_single_doc(doc)
            all_nodes.extend(nodes)
        return all_nodes

    def _split_text_keep_lines(self, text: str) -> List[str]:
        """
        替代 SentenceSplitter。
        逻辑：按行累加，不超过 chunk_size；切分时保留末尾做 overlap。
        """
        # 从 text_splitter 获取配置，保持参数一致
        limit = self.text_splitter.chunk_size
        overlap = self.text_splitter.chunk_overlap
        
        lines = text.split('\n')
        chunks = []
        current_buf = []
        current_len = 0
        
        for line in lines:
            line = line.strip()
            if not line: continue
            
            line_len = len(line)
            
            # 如果加上这一行会超标，且缓存区不为空 -> 切分
            if current_len + line_len > limit and current_buf:
                # 1. 保存当前块
                chunks.append("\n".join(current_buf))
                
                # 2. 处理 Overlap (回退逻辑)
                # 从当前缓存的末尾往回找，凑够 overlap 长度保留下来
                overlap_buf = []
                overlap_len = 0
                for old_line in reversed(current_buf):
                    if overlap_len + len(old_line) > overlap:
                        break
                    overlap_buf.insert(0, old_line) # 插到头部恢复顺序
                    overlap_len += len(old_line)
                
                # 3. 重置缓存：Overlap内容 + 当前新行
                current_buf = overlap_buf
                current_buf.append(line)
                current_len = overlap_len + line_len
            else:
                # 没超标，直接加
                current_buf.append(line)
                current_len += line_len
                
        # 处理最后剩下的
        if current_buf:
            chunks.append("\n".join(current_buf))
            
        return chunks

    def _parse_single_doc(self, doc: Document) -> List[TextNode]:
        text = doc.text
        filename = doc.metadata.get("file_name", "unknown")
        
        lines = text.split('\n')
        
        nodes = []
        current_headers = {1: "", 2: "", 3: "", 4: "", 5: "", 6: ""}
        current_content = []

        # --- 核心逻辑：创建节点（含二次切分）---
        def create_nodes_from_buffer():
            content_text = "\n".join(current_content).strip()
            if not content_text: return

            # 过滤参考文献逻辑
            # 1. 获取当前 H1 标题
            h1_title = current_headers.get(1, "")
            
            # 2. 预处理：去除所有空白字符（空格、制表符等）并转为小写
            clean_h1 = re.sub(r'\s+', '', h1_title).lower()
            
            
            # 3.1 第一道防线：标题黑名单 (增加 '目录', 'contents')
            if re.match(r'^(?:(?:主要)?(?:[参參]考(?:文献|文件|资料)|引用文献)|目录|references?|bibliography)$', clean_h1):
                return


            # 3.2 第三道防线：目录格式检测、冒牌前言检测 (内容里有多行符合目录格式)
            preview_lines = [l.strip() for l in content_text.split('\n') if l.strip()][:15] # 采样前15行
            
            # [正则定义] 
            # 1. 旧版：匹配 "... 123" 或 "   123" 结尾
            toc_line_pattern = re.compile(r'[\.…·\s]\s*\d+\s*(?:[^\u4e00-\u9fa5a-zA-Z0-9])?$')
            # 2. [新增] 括号页码：匹配 "(123)" 或 "（123）"
            bracket_page_pattern = re.compile(r'[（\(]\s*\d+\s*[）\)]')
            
            is_preface_title = re.match(r'^(?:前言|preface|序言)$', clean_h1)

            match_count = 0
            processed_count = 0
            for line in preview_lines:
                processed_count += 1
            
                # 判定当前行是否为目录行
                if len(line) > 3 and toc_line_pattern.search(line):
                    match_count += 1

                # --- 实时阻断检查 ---
                
                # 规则1: 冒牌前言检测 (最严格)
                if is_preface_title and match_count >= 2:
                    return

                # 规则2: 高密度目录检测
                if processed_count >= 3:
                    current_density = match_count / processed_count
                    if current_density > 0.6:
                        return

            # ================= [新增逻辑开始：存活文本的深度清洗] =================
            # 到了这一步，说明这块文本被认为是“正文”（如前言、综述）。
            # 但里面可能夹杂了“一、构造异构 (194) ...” 这种内联目录，需要精准剔除。

            # 定义 TOC 关键词（开头是数字编号、章节、习题等）
            toc_start_keywords = re.compile(r'^(?:第[一二三四五六七八九十\d]+[章节篇]|习题|小结|复习|思考|附录|参考文献|References|Contents|Chapter|Section|\d+\.\d+|[一二三四五六七八九十]、)')
            
            raw_lines = content_text.split('\n')
            clean_lines = []
            
            for line in raw_lines:
                stripped = line.strip()
                if not stripped:
                    clean_lines.append(line)
                    continue

                # --- 清洗策略 A: 内联多项目录 (High Confidence) ---
                # 特征：一行内出现 >= 2 个 "(数字)"
                # 例子: "一、构造异构 (194) 二、立体异构 (194)"
                if len(bracket_page_pattern.findall(stripped)) >= 2:
                    continue

                # --- 清洗策略 B: 括号页码结尾 (Medium Confidence) ---
                # 特征：以 "(数字)" 结尾，并且具备目录特征（关键词开头 或 含虚线）
                # 例子: "7.1 异构体的分类... (194)" 或 "习题...... (190)"
                if bracket_page_pattern.search(stripped[-10:]): # 仅检查末尾10个字符是否有括号数字
                    # 如果包含虚线 "..." 或 "…"
                    if re.search(r'(\.{3,}|…{2,})', stripped):
                        continue
                    # 或者 如果以目录关键词开头
                    if toc_start_keywords.match(stripped):
                        continue
                
                # --- 清洗策略 C: 传统无括号目录 (Legacy) ---
                # 特征：利用之前的 toc_line_pattern 清除 "...... 123"
                if len(stripped) > 3 and toc_line_pattern.search(stripped):
                    # 双重确认：包含虚线 或 关键词，防止误删 "In 1999" 这种年份结尾的句子
                    if re.search(r'(\.{3,}|…{2,})', stripped) or toc_start_keywords.match(stripped):
                        continue

                clean_lines.append(line)

            # 重组清洗后的文本
            content_text = "\n".join(clean_lines).strip()
            
            # 如果洗完发现空了，直接返回
            if not content_text: return
            # ================= [新增逻辑结束] =================


            # === 1. 智能提取图片与图注 ===
            extracted_images, cleaned_text = self._extract_images_smart(content_text)
            
            # --- 过滤逻辑 3: 字数阈值控制 ---
            # 规则：如果纯文本长度 < 30，且没有图片，则视为无意义，丢弃。
            if len(cleaned_text) < self.min_content_length and not extracted_images:
                return
            

            # 4 构造基础元数据
            base_meta = {
                "filename": filename,
                "h1": current_headers[1],
                "h2": current_headers[2],
                "h3": current_headers[3],
                "h4": current_headers[4],
                "h5": current_headers[5],
                "images": extracted_images
            }

            # === 策略分支 ===
            
            # 情况 A: 文本很短，直接生成一个节点
            if len(cleaned_text) <= self.text_splitter.chunk_size:
                base_meta["length"] = len(cleaned_text)
                base_meta["is_chunked"] = False # 标记：这是原生块
                node = TextNode(text=cleaned_text, metadata=base_meta)
                nodes.append(node)
                
            # 情况 B: 文本太长，进行二次切分 (Chunking)
            else:
                # split_text 会按句子智能切分，并处理重叠(overlap)
                # sub_chunks = self.text_splitter.split_text(cleaned_text)
                sub_chunks = self._split_text_keep_lines(cleaned_text)
                
                for i, chunk_text in enumerate(sub_chunks):
                    # 复制一份元数据，防止引用污染
                    chunk_meta = base_meta.copy()
                    chunk_meta["length"] = len(chunk_text)
                    chunk_meta["is_chunked"] = True
                    chunk_meta["chunk_index"] = i  # 记录切分顺序
                    
                    # 创建子节点
                    node = TextNode(text=chunk_text, metadata=chunk_meta)
                    nodes.append(node)

        # --- 逐行扫描 (与之前逻辑一致) ---
        for line in lines:
            stripped = line.strip()
            match = self._header_pattern.match(stripped)
            
            if match:
                if current_content:
                    create_nodes_from_buffer()
                    current_content = []

                hashes, title_text = match.groups()
                level = len(hashes)
                current_headers[level] = title_text.strip()
                for l in range(level + 1, 7): current_headers[l] = ""
            else:
                current_content.append(line)
        
        if current_content:
            create_nodes_from_buffer()
            
        return nodes

    def _extract_images_smart(self, text: str) -> Tuple[List[Dict], str]:
        """
        智能提取图片逻辑：
        1. 找到所有图片标签。
        2. 如果标签内无描述，尝试读取标签后的下一行作为描述。
        3. 清洗文本（可选）。
        """
        extracted_images = []
        
        # 使用 finditer 以便获取位置信息，从而查看"下一行"
        matches = list(self._image_pattern.finditer(text))
        
        # 我们需要从后往前处理，或者构建一个新的文本，这里为了简单，我们先提取信息，最后统一做一次正则替换
        # 但考虑到要提取"下一行"的文本作为caption，且随后可能要删除它，我们采用逐段构建新文本的方式更稳妥？
        # 鉴于正则替换的复杂性，这里采用两步走：
        # Step A: 扫描提取信息
        # Step B: 如果需要删除标签，则全文替换标签为空；如果下一行被认定为图注，也尝试将其从正文中剔除(可选，比较激进)
        
        # 为了不破坏原文结构，我们先只做提取，最后只删除图片标签 ![]()，保留图注文字在正文中(或者删除)
        # 您的需求通常是图注既然提取了，正文里留着也没事，或者删掉更好。
        # 这里为了稳妥，我们只提取信息，并删除图片标签 ![]()，图注文字保留在正文中（因为它也是文本的一部分）。
        
        for match in matches:
            alt_text = match.group(1)
            rel_path = match.group(2)
            
            description = alt_text
            
            # --- 智能图注提取逻辑 ---
            # 如果 Alt Text 为空，尝试向后看
            if not description:
                # 截取当前匹配位置之后的文本
                post_text = text[match.end():]
                # 寻找紧邻的非空行
                # 匹配模式：先吃掉可能的换行和空格，然后捕获一行文本
                caption_match = re.match(r'\s*\n\s*([^\n]+)', post_text)
                
                if caption_match:
                    potential_caption = caption_match.group(1).strip()
                    # 启发式规则：图注通常比较短（例如 < 50 字符），且不是标题(#)或图片(!)
                    if 0 < len(potential_caption) < 50 and not potential_caption.startswith(('!', '#')):
                        description = potential_caption

            extracted_images.append({
                "path": rel_path,
                "description": description 
            })

        cleaned_text = text
        # 只删除 ![]() 标签本身
        cleaned_text = self._image_pattern.sub("", text).strip()
        # 清理多余空行
        cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)
        
        return extracted_images, cleaned_text

# ================= 验证代码 =================
if __name__ == "__main__":
    md_file_path = "/work/ai/pdf2train/docs/农药学原理.md"
    
    with open(md_file_path, 'r', encoding='utf-8') as f:
        lines = f.read()
    mock_content = lines
    # 1. 创建 Document
    doc = Document(text=mock_content, metadata={"file_name": "农药学原理"})

    # 2. 使用我们自定义的解析器
    parser = HybridMarkdownParser(chunk_size=500, chunk_overlap=20)
    nodes = parser.get_nodes_from_documents([doc])

    # 3. 打印结果
    import json
    print(f"✅ 共提取到 {len(nodes)} 个节点：\n")
    
    for i, node in enumerate(nodes):
        print(f"--- Node {i+1} ---")
        print(f"H1: {node.metadata['h1']}")
        print(f"H2: {node.metadata['h2']}")
        print(f"H3: {node.metadata['h3']}")
        print(f"Length: {node.metadata['length']}")
        print(f"Chunked?: {node.metadata['is_chunked']}")
        print(f"Text: {node.text[:200]}...") # 只打印前20个字
        print(f"Images Metadata: {node.metadata.get('images')}")
        print("")