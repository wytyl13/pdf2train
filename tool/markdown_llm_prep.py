#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/19 15:19
@Author  : weiyutao
@File    : markdown_llm_prep.py
"""



import re
import json
from dataclasses import dataclass, asdict
from typing import List, Optional

@dataclass
class HeaderCandidate:
    id: int                 # 唯一ID，方便让LLM引用
    line_index: int         # 原始行号，用于回填
    original_text: str      # 原始内容
    next_line_preview: str  # 下一行内容的预览（辅助LLM判断上下文）

class MarkdownLLMPrep:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.lines = []
        self.candidates: List[HeaderCandidate] = []
        self._load_file()

    def _load_file(self):
        if not self.file_path:
            return
        with open(self.file_path, 'r', encoding='utf-8') as f:
            self.lines = f.readlines()

    def extract_candidates(self) -> List[dict]:
        """
        提取所有'疑似标题'。
        策略：
        1. 已经包含 # 的行
        2. 以数字开头 (1. / 1.1) 的行
        3. 以特定关键词开头 (第一章 / 前言) 的行
        4. 排除过长的句子（通常不是标题）
        """
        self.candidates = []
        candidate_id = 0

        # 正则：匹配数字开头 (1. 或 1.1)
        re_num_start = re.compile(r'^\s*(\d+(\.\d+)*)\s*')
        # 正则：匹配中文章节 (第一章)
        re_cn_chapter = re.compile(r'^\s*(第[一二三四五六七八九十0-9]+章|前言|目录|参考文献)')

        for i, line in enumerate(self.lines):
            stripped = line.strip()
            if not stripped:
                continue

            is_candidate = False
            
            # 1. 已经是Markdown标题
            if stripped.startswith('#'):
                is_candidate = True
            
            # 2. 数字开头 (且长度小于50，防止把正文列表算进去)
            elif re_num_start.match(stripped) and len(stripped) < 50:
                is_candidate = True
            
            # 3. 中文章节开头
            elif re_cn_chapter.match(stripped):
                is_candidate = True

            # 4. 排除标点结尾的句子 (句号/分号结尾通常是正文)
            if stripped.endswith('。') or stripped.endswith('；'):
                is_candidate = False

            if is_candidate:
                # 获取下一行非空内容作为上下文
                next_text = ""
                for j in range(i + 1, len(self.lines)):
                    if self.lines[j].strip():
                        next_text = self.lines[j].strip()[:50] # 只取前50字预览
                        break
                
                candidate = HeaderCandidate(
                    id=candidate_id,
                    line_index=i,
                    original_text=stripped,
                    next_line_preview=next_text
                )
                self.candidates.append(candidate)
                candidate_id += 1

        # 返回字典列表，方便直接转JSON
        return [asdict(c) for c in self.candidates]

    def generate_prompt_content(self) -> str:
        """
        生成发送给大模型的 Prompt 数据部分
        """
        data = self.extract_candidates()
        return json.dumps(data, ensure_ascii=False, indent=2)

    def apply_llm_result(self, llm_json_response: str, output_path: str):
        """
        (后续步骤) 将大模型返回的 JSON 结果应用回 Markdown 文件
        llm_json_response 结构预期:
        [
            {"id": 0, "is_header": true, "level": 1, "clean_text": "第一章 绪论"},
            {"id": 1, "is_header": false, "reason": "这是列表项"}
        ]
        """
        try:
            updates = json.loads(llm_json_response)
        except json.JSONDecodeError:
            print("解析 LLM 返回的 JSON 失败，请检查格式")
            return

        # 创建查找表 {id: update_info}
        update_map = {item['id']: item for item in updates}
        
        new_lines = self.lines.copy()
        
        for candidate in self.candidates:
            if candidate.id in update_map:
                res = update_map[candidate.id]
                
                # 如果 LLM 认为这是标题
                if res.get('is_header', False):
                    level = res.get('level', 1)
                    text = res.get('clean_text', candidate.original_text)
                    # 替换原行
                    new_lines[candidate.line_index] = f"{'#' * level} {text}\n"
                
                # 如果 LLM 认为这不是标题 (是正文或列表)
                else:
                    # 去掉可能存在的 # 号，还原为纯文本
                    clean_content = re.sub(r'^#+\s*', '', candidate.original_text)
                    new_lines[candidate.line_index] = f"{clean_content}\n"

        with open(output_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        print(f"已生成优化后的文件：{output_path}")

# ==========================================
# 使用示例
# ==========================================
if __name__ == "__main__":
    # 1. 初始化
    prepper = MarkdownLLMPrep("your_messy_ocr_file.md")
    
    # 2. 获取要发给 ChatGPT/DeepSeek 的数据
    prompt_data = prepper.generate_prompt_content()
    
    print("请复制以下内容发送给大模型：")
    print("-" * 30)
    print(prompt_data)
    print("-" * 30)