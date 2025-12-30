#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/28 15:26
@Author  : weiyutao
@File    : instruction_generator.py
"""

import os
import json
import time
import re
import threading
from typing import List, Dict, Optional, Set, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
import unicodedata

from api.service.llm_config_service import LLMConfigService

class InstructionLLMGenerator:
    """
    只负责接收处理好的 H1 上下文数据，调度 LLM 进行微调数据生成。
    不包含任何数据预处理、清洗或 XML 组装逻辑。
    高质量 Prompt 约束（LaTeX, 负面约束, 思维链）
    """

    # ================= Prompt Templates =================
    
    # ================= 1. 核心约束 (复用你原始定义的规则) =================
    COMMON_NEGATIVE_CONSTRAINTS = """
    【严重警告 - 负面约束】
    1. **拒绝“自指”**：输出的答案必须是独立的知识陈述。**严禁**出现“根据提供的文本”、“文中提到”、“如上所述”、“结合 XML”等字样。直接回答事实即可。
    2. **拒绝“省略”**：答案必须完整。**严禁**使用“及其他”、“等”、“...”来省略关键信息。
    3. **拒绝“见图”**：如果文中包含“如图所示”、“见图3-1”，请将其转化为纯文字的详细描述。如果无法用文字描述清楚，请直接**忽略**该知识点。
    4. **拒绝“机械填空”**：不要出“文中提到的温度是多少？”这种题。要出“该过程的最佳温度是多少？过高会有什么后果？”。
    """
    
    # ================= 2. System Prompt (融合 XML 结构 + LaTeX 规则) =================
    SYSTEM_PROMPT = """
    # Role
    你是一位拥有 20 年教学经验的农业/化学领域资深教授，同时也是大语言模型微调数据构建专家。

    # Output Standard (输出规范)
    在生成数据时，请严格遵守以下格式要求：
    1. **化学式与复杂公式**：必须保持格式统一，使用 **LaTeX 格式**。
    * 正确示例：$H_2O$, $CO_2$, $Fe^{2+}$, $[ \\alpha ]_D^{20}$

    2. **物理单位与数值**：
    * **严禁**将简单物理单位（如 g, mL, cm, m, L, mol）放入 LaTeX 公式中。
    * 请使用**普通文本**书写单位，并在数字和单位之间保留一个空格。
    * 对于温度，建议直接使用符号或中文。
    * **正确示例**：1 g, 100 mL, 10 cm, 20 °C, 50 kg/亩
    * **错误示例**：$1\\mathrm{g}$, $100\\ mL$, $20^{\\circ}C$

    3. **引用规范 (关键)**：
    * 你收到的输入是带 XML 标签的文本：`<chunk id="0">...</chunk>`。
    * 在 JSON 输出中，**必须**准确填写 `referenced_ids` 字段，列出回答问题所依据的 chunk id。
    * **拒绝幻觉**：严禁引用不存在的 ID。

    # Task Context
    我们正在构建一个高质量的“RAG（检索增强生成）微调数据集”。
    所有生成的数据必须严格遵守以下负面约束：
    {negative_constraints}
    """

    # ================= 3. Mapper Prompt (基于 XML 结构的规划) =================
    MAPPER_PROMPT_TEMPLATE = """
    # Phase 1: Knowledge Mapping (知识规划)

    请深度阅读以下 XML 结构的教材内容，制定本章的“微调数据生成计划”。

    ## 1. 动态评估策略
    请非常克制地规划生成数量，我们追求多样性而非数量。
    * **核心重难点**（如复杂反应机理、跨章节关联）：建议生成 3-5 条/Topic。
    * **普通知识点**（如定义、参数）：建议生成 1-2 条/Topic。
    * **简单常识**：可以直接跳过。

    ## 2. Topic 划分原则
    Topic 必须包含：
    * **原子型**：针对单一 `<chunk>` 或小节的核心概念。
    * **复合型**：需要跨多个 `<chunk>` (例如 id="1" 和 id="5") 才能解释清楚的逻辑关联。

    ## 3. Input Context (XML)
    {xml_context}

    ## 4. Output Format (JSON Only)
    {{
    "reasoning": "本章主要讲述了...",
    "topics": [
        {{
        "topic": "羊肚菌栽培的温湿度耦合效应", 
        "suggested_perspective": "原理机制",
        "complexity": "hard"
        }},
        ...
    ]
    }}
    """
    
    # ================= 4. Worker Prompt (恢复了思维链和视角要求) =================
    WORKER_PROMPT_TEMPLATE = """
    # Phase 2: Targeted Data Generation (定向生成)

    当前任务：基于主题 **【{topic}】**，生成 **1 条** 类型为 **【{perspective}】** 的微调数据。

    ## 1. Perspective Focus: {perspective}
    你必须严格遵守当前指定的视角：
    * **如果当前是【原理机制】**：问题必须涉及“为什么”、“反应机理”、“微观变化”或“根本原因”。**严禁**生成简单的定义或填空题。
    * **如果当前是【应用场景】**：问题必须设定具体的实验条件、病害现象或生产环境。
    * **如果当前是【事实定义】**：关注核心参数、结构组成或分类标准。

    ## 2. Thought Chain Rules (核心：深度推理)
    Output 的 `chain_of_thought` 字段必须包含三个明确步骤：
    1. **Step 1 检索 (Retrieval)**：明确指出在哪些 `<chunk id="...">` 中找到了关键线索。
    2. **Step 2 逻辑推演 (Deduction)**：
    * 不要只说“文中提到了X”。
    * **必须解释**：根据 chunk A 的条件，结合 chunk B 的原理，推导出结果 C。
    3. **Step 3 结论验证 (Verification)**：检查推导结果是否符合科学常识，是否触犯负面约束（如自指）。

    ## 3. Input Context (XML)
    {xml_context}

    ## 4. Output Format (JSON List Only)
    [
    {{
        "type": "{perspective}",
        "question": "结合光照和温度要求，分析为何...",
        "referenced_ids": ["1", "3"], 
        "chain_of_thought": "Step 1: 在 chunk 1 找到... Step 2: 结合 chunk 3... Step 3: ...",
        "answer": "根据相关生长特性，光照过强会导致..." 
    }}
    ]
    """

    # ================= 5. Negative Prompt (负样本生成) =================
    NEGATIVE_WORKER_PROMPT_TEMPLATE = """
    # Phase 2: Negative Sample Generation (拒答测试)

    当前任务：基于主题 **【{topic}】**，生成 **1 条** **【无法回答】** 的微调数据。

    ## 核心要求
    1. **看似相关**：问题必须包含 XML 上下文中的关键词（如“光合作用”），看起来似乎能在文中找到答案。
    2. **实则缺失**：确保提供的 `<chunk>` 中 **绝对没有** 包含该问题的核心答案（可能在下一章，或者完全未提及）。
    3. **拒答回复**：Assistant 的回答必须礼貌地指出：“无法回答关于...的问题，因为提供的参考资料中未提及相关信息。”（注意：不要说“根据 XML”，要说“根据参考资料”）。

    ## Input Context (XML)
    {xml_context}

    ## Output Format (JSON List Only)
    [
    {{
        "type": "无法回答",
        "question": "羊肚菌菌丝体培养阶段的具体光照强度数值是多少？",
        "referenced_ids": [],
        "chain_of_thought": "Step 1: 扫描所有 chunk。Step 2: 发现文中只提到了'避光培养'，未提及具体数值。Step 3: 确认无法回答。",
        "answer": "无法回答关于菌丝体培养阶段具体光照强度数值的问题，因为提供的参考资料中仅提及需避光培养，未给出具体数值。"
    }}
    ]
    """

    def __init__(self, 
                 client: OpenAI, 
                 llm_config_service: LLMConfigService,
                 model_name: str = "deepseek-chat",
                 max_workers: int = 5,
                 log_file: str = "gen_progress.log",
    ):
        """
        注意：这里不再需要 output_file 参数了
        """
        self.client = client
        self.model_name = model_name
        self.max_workers = max_workers
        self.log_file = log_file
        self.llm_config_service = llm_config_service
        
        # 仍然保留进度记录，避免重复请求扣费

    async def _reset_clien(self, doc_id: int, field_llm_name: str):
        if not doc_id:
            return
        try:
            # get llm config based on doc_id
            config = await self.llm_config_service.get_config_by_doc_id(doc_id=doc_id, field_llm_name=field_llm_name)
            if config:
                self.client = OpenAI(
                    api_key=config.get("api_key"),
                    base_url=config.get("base_url")
                )
                self.model_name = config.get("model_name")
        except Exception as e:
            raise ValueError(f"{str(e)}") from e

    def _call_llm(self, messages: List[Dict]) -> Any:
        """API 调用与 JSON 修复"""
        for attempt in range(3):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0.7,
                    max_tokens=4096
                )
                content = response.choices[0].message.content
                clean_json = content.replace("```json", "").replace("```", "").strip()
                clean_json = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', clean_json)
                return json.loads(clean_json)
            except Exception as e:
                time.sleep(1)
        return {}

    def _validate_and_format(self, generated_items: List[Dict], id_mapping: Dict[str, str]) -> List[Dict]:
        """
        【修正版】返回扁平化的字典，直接对应数据库字段
        """
        valid_records = []
        for item in generated_items:
            short_ids = item.get("referenced_ids", [])
            real_uuids = []
            is_hallucination = False
            
            # 1. ID 映射校验
            for sid in short_ids:
                sid_str = str(sid)
                if sid_str in id_mapping:
                    real_uuids.append(id_mapping[sid_str])
                else:
                    is_hallucination = True
                    break 
            
            # 2. 过滤逻辑
            if is_hallucination: continue 
            # 如果是正样本(非"无法回答")且没有引用，视为无效
            if not real_uuids and item.get("type") != "无法回答": continue

            # === 【核心修复点】构造扁平字典 ===
            # 数据库 batch_save_instructions 需要直接读取 item['question']
            system_prompt = (
                "你是一个专业的智能助手。请严格依据下方提供的【参考资料】回答用户的【问题】。"
                "回答需保持客观、准确。如果参考资料中不包含问题的答案，请直接说明无法回答，严禁依据背景知识编造信息。"
            )
            record = {
                # 直接把字段放在最外层
                "system_prompt": system_prompt,
                "question": item.get("question", ""),
                "answer": item.get("answer", ""),
                "chain_of_thought": item.get("chain_of_thought", ""),
                "type": item.get("type", "general"),
                
                # 引用 ID
                "ref_chunk_ids": real_uuids,
                
                # 其他元数据放入 meta_info
                "meta_info": {
                    "raw_short_ids": short_ids,
                    "token_usage": len(item.get("answer", "")) 
                }
            }
            valid_records.append(record)
        
        return valid_records

    def _worker_task(self, topic: str, perspective: str, xml_context: str, id_mapping: Dict) -> List[Dict]:
        """Worker 任务：返回生成的数据列表"""
        print(f"\n👉 [处理中...] 主题:【{topic}】 视角:【{perspective}】")
        try:
            if perspective == "无法回答":
                template = self.NEGATIVE_WORKER_PROMPT_TEMPLATE
            else:
                template = self.WORKER_PROMPT_TEMPLATE

            prompt = template.format(topic=topic, perspective=perspective, xml_context=xml_context)
            sys_content = self.SYSTEM_PROMPT.replace("{negative_constraints}", self.COMMON_NEGATIVE_CONSTRAINTS)
            
            messages = [{"role": "system", "content": sys_content}, {"role": "user", "content": prompt}]
            
            result = self._call_llm(messages)
            
            # debug
            print(f"✅ [已完成] 主题:【{topic}】 结果如下:\n{json.dumps(result, ensure_ascii=False, indent=2)}\n")
            
            # 解析结果
            dataset = []
            if isinstance(result, list): dataset = result
            elif isinstance(result, dict):
                # 尝试各种可能的key
                for k in ["dataset", "data", "pairs"]:
                    if k in result and isinstance(result[k], list):
                        dataset = result[k]
                        break
                if not dataset and "question" in result: dataset = [result]

            if dataset:
                # 调用校验逻辑，获取清洗后的数据
                return self._validate_and_format(dataset, id_mapping)
                
        except Exception as e:
            print(f"❌ Task Error ({topic}): {e}")
            
        return [] # 失败返回空列表

    async def process_single_h1(self, doc_id: int, h1_data: Dict) -> List[Dict]:
        """
        【改动点】：处理单个 H1，返回该章节生成的所有数据列表
        """
        h1_title = h1_data.get('h1_title', 'Unknown')
        
        xml_context = h1_data['prompt_text']
        id_mapping = h1_data['id_mapping']
        
        print(f"🚀 [Processing] {h1_title}")
        
        # 1. Mapper Phase
        mapper_prompt = self.MAPPER_PROMPT_TEMPLATE.format(xml_context=xml_context)
        sys_content = self.SYSTEM_PROMPT.replace("{negative_constraints}", self.COMMON_NEGATIVE_CONSTRAINTS)
        
        plan = self._call_llm([{"role": "system", "content": sys_content}, {"role": "user", "content": mapper_prompt}])
        topics = plan.get("topics", [])
        
        if not topics:
            return []

        # 2. Worker Phase
        tasks_to_run = []
        for topic_obj in topics:
            topic_str = topic_obj.get("topic", "Unknown")
            tasks_to_run.append((topic_str, "原理机制"))
            tasks_to_run.append((topic_str, "原理机制"))
            tasks_to_run.append((topic_str, "应用场景"))
            tasks_to_run.append((topic_str, "事实定义"))
            tasks_to_run.append((topic_str, "无法回答"))
        # tasks_to_run.append((topics[0].get("topic", "Unknown"), "无法回答"))
        
        # 收集本章节所有生成的数据
        chapter_results = []
        
        # reset client
        await self._reset_clien(doc_id=doc_id, field_llm_name="instruction_gen_llm_config")
        
        active_workers = min(self.max_workers, len(tasks_to_run))
        with ThreadPoolExecutor(max_workers=active_workers) as executor:
            futures = []
            for t_topic, t_perspective in tasks_to_run:
                futures.append(
                    executor.submit(self._worker_task, t_topic, t_perspective, xml_context, id_mapping)
                )
            
            for future in as_completed(futures):
                data = future.result()
                if data:
                    chapter_results.extend(data)

        print(f"✅ [Done] {h1_title} | Generated: {len(chapter_results)} items")
        
        # 最终返回数据给调用者
        return chapter_results