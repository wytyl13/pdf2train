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
import asyncio
from typing import List, Dict
from openai import OpenAI, AsyncOpenAI
import pandas as pd
from io import StringIO

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
    def __init__(
        self, 
        file_path: str,
        llm_config: Dict[str, str],
        doc_id: int
    ):
        self.file_path = None
        self.lines = self._process_input(file_path)
        self.doc_id = doc_id
        self.candidates: List[Dict] = []
        self.llm_config = llm_config

    # def _load_config(self):
    #     """初始化配置"""
    #     root = Path(__file__).parent.parent
    #     config_path = root / "config" / "yaml" / "deepseek_config.yaml"
    #     config = LLMConfig.from_file(str(config_path))
    #     self.client = OpenAI(api_key=config.api_key, base_url=config.base_url)
    #     self.model = config.model
        
    async def _load_config(self):
        """初始化配置"""
        print(self.llm_config)
        self.client = AsyncOpenAI(api_key=self.llm_config.get("api_key"), base_url=self.llm_config.get("base_url"))
        self.model = self.llm_config.get("model_name")
        
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
                content = f.read()
        else:
            # 2. 安全检查：看起来像路径但文件不存在
            is_path_like = len(file_path) < 255 and '\n' not in file_path and (file_path.endswith('.md') or os.sep in file_path)
            if is_path_like and not os.path.exists(file_path):
                 raise FileNotFoundError(f"❌ 看起来像路径但文件不存在: {file_path}")
            
            # 3. 视为直接的 Markdown Content
            content = file_path
            
        # 1. 先清洗 HTML 表格 (改变行结构)
        content = self._clean_html_tables(content)
        
        # 2. 再清洗 OCR 伪影 (优化内容细节)
        content = self._clean_ocr_artifacts(content)
        return content.splitlines(keepends=True)
    

    def _clean_html_tables(self, text: str) -> str:
        """
        [紧凑版] 手动构建 Markdown 表格，去除多余空格，防止编辑器自动换行导致的视觉错乱
        """
        if not text: return ""

        def _html_to_md_converter(match):
            html_content = match.group(0)
            try:
                # 1. 解析 HTML
                dfs = pd.read_html(StringIO(html_content), header=0)
                if not dfs: return html_content
                df = dfs[0]
                
                # 2. 扁平化表头
                if isinstance(df.columns, pd.MultiIndex):
                    new_cols = []
                    for col in df.columns:
                        clean_col = "-".join([str(c) for c in col if "Unnamed" not in str(c)])
                        new_cols.append(clean_col)
                    df.columns = new_cols
                
                # 3. 清洗单元格 (内部换行转空格)
                df = df.astype(str).apply(lambda x: x.str.replace(r'\s+', ' ', regex=True))
                
                # === 4. 手动构建紧凑型表格 (无填充空格) ===
                lines = []
                
                # A. 表头
                # .strip() 去除两侧空格
                headers = [str(c).replace("|", "&#124;").strip() for c in df.columns]
                lines.append("| " + " | ".join(headers) + " |")
                
                # B. 分割线 (只用3个减号，不填充)
                separators = ["---"] * len(df.columns)
                lines.append("| " + " | ".join(separators) + " |")
                
                # C. 数据行
                for _, row in df.iterrows():
                    # .strip() 去除空格，保证紧凑
                    cells = [str(val).replace("|", "&#124;").strip() for val in row]
                    lines.append("| " + " | ".join(cells) + " |")
                
                # D. 拼接
                markdown_table = "\n".join(lines)
                
                # Debug 打印
                print("\n" + "="*40)
                print(f"⚡️ [紧凑模式] 表格已生成 ({len(df)} 行):")
                print(markdown_table[:200] + "...\n(省略后续内容)") # 只打印前200字符避免刷屏
                print("="*40 + "\n")

                return f"\n\n{markdown_table}\n\n"
            
            except Exception as e:
                print(f"❌ 表格转换失败: {e}")
                return html_content

        pattern = r'(<table[^>]*>.*?</table>)'
        return re.sub(pattern, _html_to_md_converter, text, flags=re.DOTALL | re.IGNORECASE)



    def _clean_ocr_artifacts(self, text: str) -> str:
        """
        [安全增强版] 清洗 OCR/PDF 解析产生的数字伪影
        逻辑：
        1. 先处理"整块都是伪影"的情况（直接拆掉 $）。
        2. 再处理"公式内部数字有空格"的情况（保留 $，只修内容）。
        """
        if not text: return ""

        def _remove_space(match):
            return match.group(1).replace(" ", "")

        # =========================================================
        # 1. 安全清洗：整块都是伪影的情况 -> 拆掉外壳
        # =========================================================
        # 目标：$( 5 0 % )$  -> 50%
        # 目标：$ 50 \ % $   -> 50%
        # 目标：$ 50 % $     -> 50%
        # 逻辑：开头必须是 $ 或 $(，结尾必须是 $ 或 )，且中间只能是 数字+空格+\%+反斜杠
        # 这样就不会误伤 $ x = 50\% $
        
        pattern_full_artifact = r'(?:\$|\$\()\s*(\d+(?:\s+\d+)*)\s*\\?%\s*(?:\)|\$)'
        
        def _unwrap_artifact(match):
            # match.group(1) 是纯数字部分 "5 0"
            return f"{match.group(1).replace(' ', '')}%"
            
        text = re.sub(pattern_full_artifact, _unwrap_artifact, text)

        # =========================================================
        # 2. 内部清洗：只修数字，不动环境
        # =========================================================
        # 目标：$ x = 5 0 \ % $  -> $ x = 50\% $ (注意保留了 \%)
        # 目标：5 0 \ %          -> 50% (普通文本)
        
        # 这里的逻辑是：只找 "数字+空格+数字... + %"，不关心有没有 $
        # 但为了安全，如果后面跟的是 \%，我们保留 \%，只去数字空格
        
        # 2.1 针对带反斜杠的：5 0 \ % -> 50\% (保留反斜杠，防止公式内注释化)
        pattern_spaced_latex = r'(\d+(?:\s+\d+)+)\s*(\\%)'
        text = re.sub(pattern_spaced_latex, lambda m: f"{m.group(1).replace(' ', '')}{m.group(2)}", text)

        # 2.2 针对无反斜杠的：5 0 % -> 50%
        pattern_spaced_normal = r'(\d+(?:\s+\d+)+)\s*(%)'
        text = re.sub(pattern_spaced_normal, lambda m: f"{m.group(1).replace(' ', '')}%", text)

        # =========================================================
        # 3. 纯数字伪影 (你原来的逻辑，这个是安全的)
        # =========================================================
        # 只有当 $...$ 内部全是数字和空格时才替换，所以不会误伤 $ 1 + 2 $
        pattern_pure_num = r'\$\(?\s*(\d+(?:\s+\d+)+)\s*\)?\$'
        text = re.sub(pattern_pure_num, lambda m: m.group(1).replace(" ", ""), text)

        return text

    async def _call_llm(self, system_prompt: str, user_content: str, is_json_mode=True) -> Dict:
        """核心工具方法：统一封装 API 调用"""
        try:
            resp = await self.client.chat.completions.create(
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

    async def analyze_style(self) -> str:
        """步骤2：分析风格 (使用未删减的 ANALYSIS Prompt)"""
        if not self.candidates: return ""
        print("步骤2: 分析文档风格...")
        
        # 拿前 300 个做样本
        sample = json.dumps(self.candidates[:300], ensure_ascii=False, indent=2)
        
        # 调用 LLM (非 JSON 模式，因为要返回规则文本)
        rules = await self._call_llm(
            PROMPTS["ANALYSIS"], 
            f"请分析以下疑似标题序列：\n{sample}", 
            is_json_mode=False
        )
        
        if not rules:
            print("⚠️ 风格分析失败，使用默认规则。")
            return "本文档无特殊规则，请遵循通用标准。"
            
        print(f"--> 生成的规则摘要: {rules.replace(chr(10), ' ')}...")
        return rules.strip()

    async def process_batch_single(self, batch: List[Dict], adaptive_rules: str) -> Dict[str, int]:
        """
        [辅助方法] 处理单个批次
        被 process_batches_concurrently 并发调用
        """
        # 1. 组装 Prompt
        sys_prompt = PROMPTS["EXECUTION"].replace("{adaptive_rules}", adaptive_rules)
        batch_json = json.dumps(batch, ensure_ascii=False)
        user_msg = PROMPTS["USER_MSG"].replace("{json_data}", batch_json)
        
        # 2. 异步调用 LLM
        # 注意：这里会 await，但在并发模式下，它是与其他批次的 await 同时进行的
        result = await self._call_llm(sys_prompt, user_msg, is_json_mode=True)
        
        # 3. 格式化结果 (兼容列表或字典返回)
        batch_result = {}
        
        # 情况 A: LLM 返回 [{"line_id": 1, "level": 2}, ...]
        if isinstance(result, list):
            for item in result:
                if "line_id" in item: 
                    # 确保 key 是字符串，value 是整数
                    batch_result[str(item["line_id"])] = int(item.get("level", 0))
                    
        # 情况 B: LLM 返回 {"1": 2, "5": 1, ...}
        elif isinstance(result, dict):
             batch_result.update({str(k): int(v) for k, v in result.items()})
             
        return batch_result

    async def process_batches_concurrently(self, adaptive_rules: str, batch_size=300) -> Dict[str, int]:
        """
        步骤3：并发批处理 (速度提升核心)
        同时发出所有批次的 LLM 请求，而不是串行等待
        """
        print(f"步骤3: 开始并发处理 (Batch Size: {batch_size}, Total Items: {len(self.candidates)})...")
        
        total = len(self.candidates)
        tasks = []
        all_results = {}

        # 1. 创建所有批次的协程任务 (Task Creation)
        # 此时并没有真正阻塞等待，只是把任务打包
        for i in range(0, total, batch_size):
            batch = self.candidates[i : i + batch_size]
            # 调用刚才添加的辅助方法 process_batch_single
            tasks.append(self.process_batch_single(batch, adaptive_rules))
            
        if not tasks:
            return {}

        # 2. 并发执行所有请求！(Concurrent Execution)
        # asyncio.gather 会同时等待所有任务完成
        # 耗时取决于最慢的那一个批次，而不是所有批次之和
        try:
            results_list = await asyncio.gather(*tasks)
        except Exception as e:
            print(f"❌ 并发批处理过程中发生错误: {e}")
            return {}
        
        # 3. 合并结果 (Aggregation)
        # results_list 是一个列表，包含了每个 process_batch_single 返回的字典
        for res in results_list:
            if res:
                all_results.update(res)
            
        print(f"✅ 并发处理完成，共获取 {len(all_results)} 条层级规则。")
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

    async def run(self):
        await self._load_config()
        if not self.extract_candidates(): return
        rules = await self.analyze_style()
        results = await self.process_batches_concurrently(rules)
        return self.apply_changes(results)

if __name__ == "__main__":
    TARGET = "/work/ai/pdf2train/docs/农药学原理.md"
    with open(TARGET, 'r', encoding='utf-8') as f:
        input_data = f.read()
    
    result = MarkdownCleaner(input_data).run()
    print(result)