#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/19 15:36
@Author  : weiyutao
@File    : md_header_extract.py
"""

import re
import json
from dataclasses import dataclass, asdict
from typing import List, Dict
import os
from agent.config.llm_config import LLMConfig
from openai import OpenAI
from pathlib import Path


ROOT_DIRECTORY = Path(__file__).parent.parent
LLM_CONFIG_PATH = str(ROOT_DIRECTORY / "config" / "yaml" / "deepseek_config.yaml")
llm_config = LLMConfig.from_file(LLM_CONFIG_PATH)


llm_client = OpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)



ANALYSIS_SYSTEM_PROMPT = """
你是一个文档结构分析师。请仔细阅读下面这份 Markdown 文档的片段，**推断其独特的标题层级体系**。

请务必关注以下多种可能，不要局限于纯数字：

1. **最高级 (H1) 判定**：
   - 它是 "第X章" 还是 "1. xxx"？
   - 或者是汉字编号 "一、"、"二、"？
   - 或者是纯名词标题（如 "实验方法"）？

2. **次级 (H2/H3) 判定**：
   - 如果 H1 是 "第X章"，那么 H2 是 "1. xxx" (数字加空格) 还是 "1.1 xxx" (带点)？
   - 如果 H1 是 "一、"，那么 H2 是 "（一）" 还是 "1."？

3. **混排逻辑**：
   - 比如："一、" (H1) -> "1." (H2) -> "(1)" (H3)
   - 比如："1 " (H1, 无点) -> "1.1" (H2) -> "1.1.1" (H3)

**请直接输出一段规则描述 (Adaptive Rules)，覆盖所有发现的层级特征。格式示例：**
"本文档采用混合编号结构。规则如下：
- ‘第X章’、前言、目录 -> Level 1
- ‘1 xxx’ (数字加空格，无点) -> Level 2
- ‘1.1 xxx’ (带点数字) -> Level 3
- ‘(1) xxx’ -> Level 4"
"""

# 2. 固定规则模板：宪法
FIXED_SYSTEM_TEMPLATE = """
你是一个专业的 Markdown 文档结构化专家。
你的任务是：分析传入的文本行，结合【全局层级规则】，精准判断其 Markdown 标题层级 (1-6)。

🚫 【通用负面清单 (绝对 Level 0)】：
1. 目录索引项：凡是行内包含连续虚线 (......) 或结尾是页码数字的，一律标记为 0。
2. 引用与长句：包含引号、感叹号的句子；或长度超过40字的段落 -> Level 0。
3. 孤立名词：列表中的枚举名词 (如 "敌敌畏")，如果没有序号引导 -> Level 0。

✅ 【通用强制标题 (绝对 H1)】：
- 关键词独占一行： "前言"、"目录"、"参考文献"、"摘要"。

📋 【本文档特定的层级规则 (由上文分析得出)】：
{adaptive_rules}

📏 【辅助逻辑 (Logic Guardrails)】：
1. **纯数字编号**：如果规则定义 "1 xxx" 为 H2，那么 "1.1" 通常为 H3 (递进)。
2. **汉字编号**：如果规则定义 "一、" 为 H1，那么 "（一）" 通常为 H2 或 H3。
3. **空格容错**："1.1" 和 "1. 1" 视为同级；"1" 和 "1 " 视为同级。

⚠️ 输出要求：
- 仅输出 JSON 对象 {{ "line_id": level }}。
- **重要：如果判定为 Level 0 (非标题)，请不要包含在输出 JSON 中。**
"""

# 3. 执行阶段用户提示词
USER_EXECUTION_TEMPLATE = """
请基于 System Prompt 中的规则，分析以下数据块：

{json_data}

仅输出 JSON 结果。
"""


# ================= 数据结构 =================

@dataclass
class HeaderTask:
    line_id: int
    text: str
    next_line: str

# ================= 核心 Pipeline 类 =================

class MarkdownHeaderPipeline:
    def __init__(self, file_path: str, client: OpenAI, model_name: str):
        self.file_path = file_path
        self.client = client
        self.model_name = model_name
        self.lines = []
        self.candidate_tasks: List[HeaderTask] = [] # 存储所有提取出来的疑似标题
        
        self._load_file()

    def _load_file(self):
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"文件不存在: {self.file_path}")
        with open(self.file_path, 'r', encoding='utf-8') as f:
            self.lines = f.readlines()
    
    def _is_obvious_toc(self, text: str) -> bool:
        """
        [Python 卫士]：硬逻辑判断是否为目录项 (增强版)
        """
        cleaned = text.strip()
        if len(cleaned) < 3: return False 
        
        # 1. 【强特征】检测长虚线/省略号 (目录的典型特征)
        # 匹配连续的 "." 或 "。" 或 "·" 超过2个
        # 很多 OCR 会把 "......" 识别为 ". . . ." 或 "。。。。"
        if re.search(r'(\.{2,}|。{2,}|·{2,})', cleaned):
            return True
            
        # 2. 【结尾特征】检测页码 (支持数字、页码范围、或者 OCR 错误的乱码)
        # 匹配: 结尾是数字 (如 " 12")
        if re.search(r'\s\d{1,4}\s*$', cleaned):
            return True
            
        # 匹配: 结尾是范围 (如 " 12-15")
        if re.search(r'\d+[-~]\d+\s*$', cleaned):
            return True

        return False

    def step1_extract_candidates(self) -> List[List[Dict]]:
        print("步骤1: 开始提取 H1 候选行...")
        self.candidate_tasks = [] # 重置
        results_dicts = [] # 临时存储字典格式，用于切分
        
        if not os.path.exists(self.file_path):
            print(f"❌ 文件不存在: {self.file_path}")
            return [] # 返回空列表或根据你的逻辑处理

        with open(self.file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # =======================================================
            # ⚠️ 核心过滤逻辑：严格只抓取以 "#" 开头的行
            # =======================================================
            if stripped.startswith('#'):
                
                # 获取下文预览 (用于辅助判断语义)
                next_line_content = ""
                for j in range(i + 1, min(i + 5, len(lines))):
                    next_l = lines[j].strip()
                    if next_l:
                        next_line_content = next_l[:100]
                        break
                
                # 1. 存入类成员变量 (Step4 要用)
                task_obj = HeaderTask(line_id=i, text=stripped, next_line=next_line_content)
                self.candidate_tasks.append(task_obj)

                # 2. 存入临时列表 (Step3 要用)
                item_dict = {
                    "line_id": i,      # 注意：列表索引即行号
                    "text": stripped,
                    "next_line": next_line_content
                }
                results_dicts.append(item_dict)

        # ================= 切分逻辑 (Batching) =================
        batch_size = 300
        # 使用列表推导式将 results 切分为多个子列表
        batched_results = [results_dicts[i:i + batch_size] for i in range(0, len(results_dicts), batch_size)]

        print(f"✅ 提取完成。共找到 {len(self.candidate_tasks)} 个标题候选。")
        print(f"📦 已切分为 {len(batched_results)} 个批次 (每批次约 {batch_size} 条)。")

        return batched_results

    def step2_analyze_style(self, sample_batch: List[Dict]) -> str:
        """
        步骤 2：分析文档风格，生成自适应规则
        """
        print("步骤2: 正在分析文档结构风格...")
        sample_json_str = json.dumps(sample_batch, ensure_ascii=False, indent=2)
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": f"请分析以下疑似标题序列：\n{sample_json_str}"}
                ],
                temperature=0.1
            )
            rules = response.choices[0].message.content
            print(f"--> 生成的规则: \n{rules.strip()}")
            return rules
        except Exception as e:
            print(f"分析风格失败: {e}，将使用默认空规则。")
            return "本文档无特殊规则，请遵循通用标准。"

    def step3_process_batches(self, adaptive_rules: str, batched_data: List[List[Dict]]) -> Dict[str, int]:
        """
        步骤 3：并发/循环处理所有批次，生成最终结果字典
        """
        # 组装最终 System Prompt
        final_system_prompt = FIXED_SYSTEM_TEMPLATE.replace("{adaptive_rules}", adaptive_rules)
        all_results: Dict[str, int] = {}
        total_batches = len(batched_data)
        print(f"步骤3: 开始处理 {total_batches} 个批次...")

        for index, batch_list in enumerate(batched_data):
            batch_json_str = json.dumps(batch_list, ensure_ascii=False)
            user_prompt = USER_EXECUTION_TEMPLATE.replace("{json_data}", batch_json_str)
            
            try:
                # 调用 LLM
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": final_system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=8192
                )
                
                content = response.choices[0].message.content
                # 清洗 Markdown 标记
                clean_text = content.replace("```json", "").replace("```", "").strip()
                
                batch_result = json.loads(clean_text)
                
                # 合并结果
                if isinstance(batch_result, dict):
                    str_key_result = {str(k): v for k, v in batch_result.items()}
                    all_results.update(str_key_result)
                
                print(f"  - [{index + 1}/{total_batches}] 批次完成，当前累计识别 {len(all_results)} 个有效标题。")
                
            except json.JSONDecodeError:
                print(f"  ❌ 批次 {index + 1} JSON解析失败，内容可能被截断。")
            except Exception as e:
                print(f"  ❌ 批次 {index + 1} API调用失败: {e}")

        return all_results

    def step4_apply_changes(self, confirmed_headers: Dict[str, int], output_path: str = None):
        """
        步骤 4：应用修改。核心逻辑：
        - 在 confirmed_headers 里且 level > 0 -> 加 #
        - 在 candidate_tasks 里但不在 confirmed_headers (或 level=0) -> 去 # (Level 0 逻辑)
        """
        if output_path is None:
            output_path = self.file_path

        modified_count = 0
        reverted_count = 0
        new_lines = self.lines.copy()
        
        # 1. 构建候选行集合 (用于判断哪些行被 LLM 判定为“非标题”)
        
        # 2. 遍历所有候选行
        for task in self.candidate_tasks:
            line_id = task.line_id
            line_id_str = str(line_id)
            original_line = new_lines[line_id]
            
            # ... 获取原始文本 ...
            clean_text = re.sub(r'^#+\s*', '', original_line).strip()

            # 判断逻辑
            # A. LLM 说是标题 (在字典里且 level > 0)
            is_confirmed = (line_id_str in confirmed_headers and confirmed_headers[line_id_str] > 0)

            # B. Python 正则复核 (防止目录漏网)
            is_toc = self._is_obvious_toc(clean_text)
            
            # 3. ⚖️【核心判决逻辑】
            # 只有当 LLM 说是标题 (True)，并且 Python 卫士说它不是目录 (False) 时
            # 这一行才能成为标题！
            if is_confirmed and not is_toc:
                # 是真标题 -> 根据 LLM 给的 level 重写 #
                level = confirmed_headers[line_id_str]
                new_lines[line_id] = f"{'#' * level} {clean_text}\n"
                # 如果原来的 level 和现在不一样，才算 modify
                if original_line != new_lines[line_id]:
                    modified_count += 1
                
            else:
                # 不是标题 -> 移除 # 号，还原为正文
                new_lines[line_id] = f"{clean_text}\n"
                modified_count += 1

        with open(output_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        
        print(f"步骤4: 文件写入完成。共修改了 {modified_count} 行。保存至: {output_path}")

    def run(self):
        """执行完整流水线"""
        print("=== 开始执行 Markdown 标题清洗流水线 ===")
        
        # 1. 提取
        batches = self.step1_extract_candidates()
        if not batches:
            print("❌ 未提取到任何 # 开头的行，脚本结束。")
            return

        # 2. 分析规则 (使用前 50 个作为样本)
        adaptive_rules = self.step2_analyze_style(batches[0])
        
        # 3. 批量执行
        final_results = self.step3_process_batches(adaptive_rules, batches)
        
        # 4. 应用修改
        self.step4_apply_changes(final_results)
        
        print("=== 流水线执行完毕 ===")


# ================= 主入口 =================
if __name__ == "__main__":
    # 请填入你的文件路径
    TARGET_FILE = "/work/ai/pdf2train/docs/mimo-v2-flash.md"
    
    # 实例化 Pipeline
    # 注意：这里传入已初始化的 llm_client
    pipeline = MarkdownHeaderPipeline(
        file_path=TARGET_FILE, 
        client=llm_client, 
        model_name=llm_config.model
    )
    
    # 运行
    pipeline.run()