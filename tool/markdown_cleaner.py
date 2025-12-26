#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/21 12:00
@Author  : weiyutao
@File    : markdown_cleaner.py
"""

import re
import json
import os
from typing import List, Dict
from pathlib import Path
from openai import OpenAI
from agent.config.llm_config import LLMConfig



# ================= 1. 完整的高智商 Prompts (未删减版) =================

PROMPTS = {
    # 步骤2的分析师 Prompt：负责制定规则
    "ANALYSIS": """
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
""",

    # 步骤3的执行官 Prompt：负责具体判定
    "EXECUTION": """
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
""",
    
    # 简单的用户 Prompt
    "USER_MSG": """
请基于 System Prompt 中的规则，分析以下数据块：
{json_data}
仅输出 JSON 结果。
"""
}

# ================= 2. 简化的 Python 逻辑结构 =================

class MarkdownCleaner:
    def __init__(self, file_path: str):
        self.file_path = None
        self._load_config()
        self.lines = self._process_input(file_path)
        self.candidates: List[Dict] = []

    def _load_config(self):
        """初始化配置"""
        root = Path(__file__).parent.parent
        config_path = root / "config" / "yaml" / "deepseek_config.yaml"
        config = LLMConfig.from_file(str(config_path))
        self.client = OpenAI(api_key=config.api_key, base_url=config.base_url)
        self.model = config.model

    def _load_file(self) -> List[str]:
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"❌ 文件不存在: {self.file_path}")
        with open(self.file_path, 'r', encoding='utf-8') as f:
            return f.readlines()

    def _process_input(self, file_path: str) -> List[str]:
        """
        处理输入数据：
        1. 如果是存在的文件路径，读取文件。
        2. 如果不是文件路径，视为直接的 Markdown 内容。
        """
        # 1. 尝试检测是否为文件路径
        # 注意：这里限制了长度防止把超长文本误判为路径，尽管 os.path.exists 会处理，但作为一种优化
        if len(file_path) < 4096 and os.path.exists(file_path):
            self.file_path = file_path
            # print(f"📂 检测到输入为文件路径: {data}")
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.readlines()
        
        # 2. 安全检查（可选）：如果看起来像路径但文件不存在，抛出异常
        # 如果字符串很短、没换行且包含路径分隔符，可能是用户写错了路径
        # (这一步是为了防止用户想传路径却写错，导致程序把它当成一行文本处理了)
        is_path_like = len(file_path) < 255 and '\n' not in file_path and (file_path.endswith('.md') or os.sep in file_path)
        if is_path_like and not os.path.exists(file_path):
             raise FileNotFoundError(f"❌ 看起来像路径但文件不存在: {file_path}")

        # 3. 视为直接的 Markdown Content
        # print("📝 检测到输入为 Markdown 文本内容")
        # keepends=True 非常重要！它能保留行尾的 \n，保持与 f.readlines() 格式一致
        return file_path.splitlines(keepends=True)


    def _call_llm(self, system_prompt: str, user_content: str, is_json_mode=True) -> Dict:
        """核心工具方法：统一封装 API 调用"""
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                # Step2 返回的是纯文本规则，Step3 返回的是 JSON
                response_format={"type": "json_object"} if is_json_mode else None,
                temperature=0.1
            )
            content = resp.choices[0].message.content
            
            if is_json_mode:
                clean_json = content.replace("```json", "").replace("```", "").strip()
                return json.loads(clean_json)
            else:
                return content # 如果不是 JSON 模式，直接返回文本 (给 Step2 用)
        except Exception as e:
            print(f"⚠️ LLM 调用异常: {e}")
            return {} if is_json_mode else ""

    def extract_candidates(self) -> List[Dict]:
        """步骤1：提取 # 开头的行"""
        print("步骤1: 提取候选行...")
        self.candidates = []
        for i, line in enumerate(self.lines):
            stripped = line.strip()
            if stripped.startswith('#'):
                next_context = "".join([l.strip()[:50] for l in self.lines[i+1:i+6] if l.strip()][:1])
                self.candidates.append({
                    "line_id": i,
                    "text": stripped,
                    "next_line": next_context
                })
        print(f"✅ 提取到 {len(self.candidates)} 个候选标题。")
        return self.candidates

    def analyze_style(self) -> str:
        """步骤2：分析风格 (使用未删减的 ANALYSIS Prompt)"""
        if not self.candidates: return ""
        print("步骤2: 分析文档风格...")
        
        # 拿前 300 个做样本
        sample = json.dumps(self.candidates[:300], ensure_ascii=False, indent=2)
        
        # 调用 LLM (非 JSON 模式，因为要返回规则文本)
        rules = self._call_llm(
            PROMPTS["ANALYSIS"], 
            f"请分析以下疑似标题序列：\n{sample}", 
            is_json_mode=False
        )
        
        if not rules:
            print("⚠️ 风格分析失败，使用默认规则。")
            return "本文档无特殊规则，请遵循通用标准。"
            
        print(f"--> 生成的规则摘要: {rules.replace(chr(10), ' ')}...")
        return rules.strip()

    def process_batches(self, adaptive_rules: str, batch_size=300) -> Dict[str, int]:
        """步骤3：分批处理 (使用未删减的 EXECUTION Prompt)"""
        print(f"步骤3: 开始批处理 (Batch Size: {batch_size})...")
        
        # 组装完整的 System Prompt
        sys_prompt = PROMPTS["EXECUTION"].replace("{adaptive_rules}", adaptive_rules)
        all_results = {}

        total = len(self.candidates)
        # 动态切片循环
        for i in range(0, total, batch_size):
            batch = self.candidates[i : i + batch_size]
            batch_json = json.dumps(batch, ensure_ascii=False)
            user_msg = PROMPTS["USER_MSG"].replace("{json_data}", batch_json)
            
            # 调用 LLM
            result = self._call_llm(sys_prompt, user_msg, is_json_mode=True)
            
            if result:
                # 兼容 {line_id: level} 或 [{line_id:..., level:...}]
                if isinstance(result, list):
                    for item in result:
                        if "line_id" in item: all_results[str(item["line_id"])] = item.get("level", 0)
                else:
                    all_results.update({str(k): v for k, v in result.items()})
                    
                print(f"  - 进度: {min(i+batch_size, total)}/{total} 行处理完毕。")
            else:
                print(f"  ❌ 批次 {i} 处理失败。")

        return all_results

    def apply_changes(self, headers_map: Dict[str, int], output_path=None):
        """步骤4：应用修改 (含 Python 卫士)"""
        if not output_path: output_path = self.file_path
        
        modified = 0
        new_lines = self.lines[:]

        for item in self.candidates:
            idx = item["line_id"]
            line_key = str(idx)
            raw_text = re.sub(r'^#+\s*', '', item["text"]).strip()
            
            # 判定逻辑
            is_toc = self._is_toc_noise(raw_text)
            level = headers_map.get(line_key, 0)

            if level > 0 and not is_toc:
                new_line = f"{'#' * level} {raw_text}\n"
            else:
                new_line = f"{raw_text}\n"

            if new_lines[idx] != new_line:
                new_lines[idx] = new_line
                modified += 1

        if not output_path:
            return "".join(new_lines)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        print(f"✅ 完成！共修改 {modified} 行。文件保存至: {output_path}")
        return ""

    @staticmethod
    def _is_toc_noise(text: str) -> bool:
        """辅助：判断目录噪音"""
        clean = text.strip()
        if len(clean) < 3: return False
        return bool(re.search(r'(\.{2,}|。{2,})', clean) or re.search(r'\s\d{1,4}$', clean))

    def run(self):
        if not self.extract_candidates(): return
        rules = self.analyze_style()
        results = self.process_batches(rules)
        return self.apply_changes(results)

if __name__ == "__main__":
    TARGET = "/work/ai/pdf2train/docs/农药学原理.md"
    with open(TARGET, 'r', encoding='utf-8') as f:
        input_data = f.read()
    
    result = MarkdownCleaner(input_data).run()
    print(result)