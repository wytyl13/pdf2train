#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/19 10:42
@Author  : weiyutao
@File    : mardown_optimizer.py
"""


import re
from dataclasses import dataclass
from typing import List, Optional, Tuple
from difflib import SequenceMatcher
import os

# ==========================================
# 数据结构定义
# ==========================================
@dataclass
class Section:
    """章节对象：包含标题、层级和正文内容"""
    title: str          # 清洗后的标题文本 (无 #，无多余空格)
    level: int          # 1=H1, 2=H2..., 0=非标题文本
    content: List[str]  # 正文行
    raw_header: str     # 原始标题行 (用于回写)

# ==========================================
# 核心处理类
# ==========================================
class MarkdownOptimizer:
    def __init__(self):
        # --- 1. 核心标记识别 (增强了对空格的容忍度) ---
        
        # 匹配：第一章 (强制 H1，权重最高)
        self.RE_CHAPTER_START = re.compile(r'^#*\s*第[一二三四五六七八九十0-9]+章')

        # 匹配：目录 (支持 "目 录", "目  录", "Content" 等)
        # \s* 允许中间出现任意数量的空格
        self.RE_TOC_TITLE = re.compile(r'^#*\s*(目\s*录|目\s*次|Table\s*of\s*Contents)\s*$', re.IGNORECASE)
        
        # 匹配：前言/序言 (支持 "前 言") - 用于强制提权，防止被漏掉
        self.RE_PREFACE_TITLE = re.compile(r'^#*\s*(前\s*言|序\s*言|绪\s*论|引\s*言|内\s*容\s*简\s*介)\s*$', re.IGNORECASE)

        # --- 2. 标题层级识别 ---
        # 匹配：1. / 1.1 / 1.1.1 (数字开头，中间有点，后面跟中文)
        self.RE_NUM_HEAD = re.compile(r'^#*\s*(\d+(\.\d+)*)([ .、\t]*)?([\u4e00-\u9fa5].*)')
        
        # --- 3. 辅助识别 ---
        # 匹配：行尾页码 (用于判定这行是不是目录项)
        # 逻辑：行尾是数字，且数字前可能有 . 或 空格
        self.RE_PAGE_NUMBER = re.compile(r'[\. …\s\t]+(\d+)$')

    def process(self, content: str, remove_titles: List[str] = None, remove_orphan_text: bool = False) -> str:
        """
        主处理管道
        :param content: 原始 Markdown 文本
        :param remove_titles: 需要移除的章节标题列表 (支持模糊匹配)
        :param remove_orphan_text: 是否移除文档头部的无标题内容 (通常是乱码)
        """
        if not content: return ""
        lines = content.split('\n')
        
        # 1. Normalize: 行级清洗、提权、目录区域封锁
        normalized_lines = self._normalize_lines(lines)
        
        # 2. Sectionize: 将行聚类为章节块
        sections = self._segment_into_sections(normalized_lines)
        
        # 3. Filter: 根据条件移除特定章节
        final_sections = self._filter_sections(sections, remove_titles, remove_orphan_text)
        
        # 4. Reassemble: 重组回字符串
        reassembled_content = self._reassemble(final_sections)

        # 步骤 5: 后处理 (Post-process)
        final_result = self._post_process_layout(reassembled_content)

        return final_result

    def _post_process_layout(self, content: str) -> str:
        """
        仅当 </table> 后面紧跟着标题标记 (#) 时，才强制插入换行。
        """
        new_content = re.sub(r'(</table>)[ \t]*\n?', r'\1\n\n', content, flags=re.IGNORECASE)
        return new_content
    
    


    # =======================================================
    # 阶段一：标准化 (Normalize)
    # =======================================================
    def _normalize_lines(self, lines: List[str]) -> List[str]:
        normalized = []
        
        # A. 全局扫描：确定目录区间 & 是否有第一章
        # toc_range 是关键，它决定了哪些内容要被“钝化”为列表
        toc_range = self._find_toc_range(lines)
        
        # 决定全局偏移：如果有"第一章"，则 "1." 是 H2；否则 "1." 是 H1
        has_chapter_h1 = self._check_has_chapter(lines)
        base_offset = 1 if has_chapter_h1 else 0
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped: 
                continue # 暂时忽略空行
            
            # 基础清洗：去掉原有的 #，回到纯文本状态分析
            clean_text = re.sub(r'^#+\s*', '', stripped)
            
            # --- 场景 1: 处于目录区间内 (TOC Zone) ---
            # 规则：除了目录标题本身，区间内的一切都变为列表！
            # 这样目录里的 "前言"、"1.1 xxx" 就不会变成正文标题了
            if toc_range and toc_range[0] <= i < toc_range[1]:
                # 如果是目录区间的起始行，保留为 H1，并标准化名称
                if i == toc_range[0]:
                    normalized.append("# 目录")
                else:
                    # 即使目录里写了 "前言" 或 "1.1 xxx"，只要在目录区间里，它就是个列表项
                    # 同时清洗掉行尾的页码
                    text_no_page = self.RE_PAGE_NUMBER.sub('', clean_text).strip()
                    normalized.append(f"- {text_no_page}")
                continue

            # --- 场景 2: 处于普通区域 ---
            
            # A. 强制提权逻辑 (Fix: 解决前言没被识别的问题)
            # 遇到单独一行的 "前 言"，即使没有 #，也强制变成 "# 前言"
            if self.RE_PREFACE_TITLE.match(clean_text) and not self._has_page_num(line):
                # 统一去空格： "# 前 言" -> "# 前言"
                title_unified = re.sub(r'\s+', '', clean_text)
                normalized.append(f"# {title_unified}")
                continue
            
            # B. 真正的章节 (第一章)
            if self.RE_CHAPTER_START.match(clean_text) and not self._has_page_num(line):
                normalized.append(f"# {clean_text}")
                continue

            # C. 数字标题 (1. / 1.1)
            match_num = self.RE_NUM_HEAD.match(clean_text)
            if match_num:
                # 排除长句子 (防止 "1. 我们要注意..." 被误判)
                if self._is_sentence(clean_text):
                    normalized.append(clean_text)
                    continue
                
                num_part = match_num.group(1)
                text_part = match_num.group(4)
                
                # 计算层级
                depth = num_part.count('.') + 1
                final_level = depth + base_offset
                final_level = min(final_level, 6)
                
                normalized.append(f"{'#' * final_level} {num_part} {text_part}")
                continue
            
            # D. 参考文献 (作为 H1)
            if "参考文献" in clean_text and len(clean_text) < 10:
                normalized.append("# 参考文献")
                continue
                
            # E. 普通文本
            normalized.append(clean_text)
            
        return normalized

    # =======================================================
    # 阶段二：分块 (Sectionize)
    # =======================================================
    def _segment_into_sections(self, lines: List[str]) -> List[Section]:
        sections = []
        # level=0 表示这部分内容不属于任何 H1 标题 (通常是文档开头的垃圾)
        current_section = Section(title=None, level=0, content=[], raw_header=None)
        
        for line in lines:
            is_h1 = line.startswith("# ")
            if is_h1:
                # 归档上一个章节
                if current_section.title is not None or current_section.content:
                    sections.append(current_section)
                
                # 开启新章节
                title_text = line[2:].strip()
                current_section = Section(title=title_text, level=1, content=[], raw_header=line)
            else:
                # 归入当前章节正文
                current_section.content.append(line)
        
        # 添加最后一个章节
        sections.append(current_section)
        return sections

    # =======================================================
    # 阶段三：过滤 (Filter)
    # =======================================================
    def _filter_sections(self, sections: List[Section], remove_titles: List[str], remove_orphan: bool) -> List[Section]:
        filtered = []
        for sec in sections:
            # 1. 移除头部 orphan (无标题内容)
            if sec.level == 0:
                if remove_orphan:
                    print("[Filter] 移除了头部无标题内容")
                    continue
                else: 
                    filtered.append(sec)
                    continue
            
            # 2. 移除指定标题 (支持模糊匹配)
            should_remove = False
            if remove_titles and sec.title:
                for target in remove_titles:
                    # 使用模糊匹配，阈值 0.7 防止误删
                    if self._is_similar(sec.title, target, threshold=0.7):
                        should_remove = True
                        print(f"[Filter] 移除了章节: '{sec.title}' (匹配目标: '{target}')")
                        break
            
            if not should_remove:
                filtered.append(sec)
        return filtered

    # =======================================================
    # 辅助工具方法
    # =======================================================
    def _find_toc_range(self, lines: List[str]) -> Optional[Tuple[int, int]]:
        """
        定位目录的 [开始行, 结束行)
        开始：匹配到 '目 录'
        结束：匹配到下一个 '强H1' (如第一章、前言) 且无页码
        """
        start_idx = -1
        end_idx = float('inf')
        
        # 1. 找起点
        for i, line in enumerate(lines):
            clean = re.sub(r'^#+\s*', '', line.strip())
            # 使用增强正则 (允许空格)
            if self.RE_TOC_TITLE.match(clean):
                start_idx = i
                break
        
        if start_idx == -1: return None
            
        # 2. 找终点 (从目录下一行开始)
        for i in range(start_idx + 1, len(lines)):
            clean = re.sub(r'^#+\s*', '', lines[i].strip())
            
            # 只有遇到 "第一章" 或 "前言" 且行尾没有页码，才算目录真正结束
            # 注意：很多目录里也有"第一章... 1"，必须排除带页码的行
            is_strong_header = self.RE_CHAPTER_START.match(clean) or self.RE_PREFACE_TITLE.match(clean)
            
            if is_strong_header and not self._has_page_num(lines[i]):
                end_idx = i
                break
        
        if end_idx == float('inf'): end_idx = len(lines)
        return (start_idx, end_idx)

    def _is_similar(self, text_a: str, text_b: str, threshold: float = 0.7) -> bool:
        """模糊匹配：去除空格后计算相似度"""
        if not text_a or not text_b: return False
        clean_a = re.sub(r'\s+', '', text_a)
        clean_b = re.sub(r'\s+', '', text_b)
        # 包含检测 (兜底)
        if clean_b in clean_a or clean_a in clean_b: return True
        return SequenceMatcher(None, clean_a, clean_b).ratio() >= threshold

    def _reassemble(self, sections: List[Section]) -> str:
        output = []
        for sec in sections:
            if sec.level > 0:
                output.append(f"\n{sec.raw_header}")
            output.extend(sec.content)
        return '\n'.join(output).strip()

    def _check_has_chapter(self, lines: List[str]) -> bool:
        """全书扫描：看是否有'第一章'，决定 1.xxx 的层级"""
        for line in lines:
            clean = re.sub(r'^#+\s*', '', line.strip())
            # 必须排除带页码的行 (防止目录里的第一章干扰)
            if self.RE_CHAPTER_START.match(clean) and not self._has_page_num(line):
                return True
        return False

    def _has_page_num(self, text: str) -> bool:
        return bool(self.RE_PAGE_NUMBER.search(text))

    def _is_sentence(self, text: str) -> bool:
        if len(text) > 60: return True
        if text.endswith('。') or text.endswith('；'): return True
        return False




# ==========================================
# 执行入口
# ==========================================
if __name__ == "__main__":
    source_file = "../docs/农药学原理.md" 
    target_file = "../docs/target_fuzzy.md"
    
    # 简单的路径检查
    if not os.path.exists(source_file):
        print("未找到源文件，请检查路径")
        exit()

    with open(source_file, 'r', encoding='utf-8') as f:
        content = f.read()

    optimizer = MarkdownOptimizer()
    new_content = optimizer.process(
        content=content,
        # remove_titles=['目录', '参考文献'],
        # remove_orphan_text=True
    )

    with open(target_file, 'w', encoding='utf-8') as f:
        f.write(new_content)
        
    print(f"处理完成！输出文件: {target_file}")