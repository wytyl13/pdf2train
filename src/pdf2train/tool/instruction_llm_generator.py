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
import logging
import asyncio

from pdf2train.core.table.llm_config import LLMConfig


class InstructionLLMGenerator:
    """
    只负责接收处理好的 H1 上下文数据，调度 LLM 进行微调数据生成。
    不包含任何数据预处理、清洗或 XML 组装逻辑。
    高质量 Prompt 约束（LaTeX, 负面约束, 思维链）
    预训练数据的token数一般控制在参数量的1-2倍
    
    指令微调数据中：
    无法回答比例一般控制在5%-10%之间
    通用数据一般控制在30%
    rag类型的数据一般占比40%-50%
    知识内化数据一般占比40%-50%
    通用/逻辑/多轮对话一般占比5%-10%
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
    
    # Reference Material (Context Caching Anchor)
    下方是本次任务的核心参考资料，所有回答必须基于此内容：
    
    {xml_context}
    """

    # ================= 3. Mapper Prompt (基于 XML 结构的规划) =================
    MAPPER_PROMPT_TEMPLATE = """
    # Phase 1: Knowledge Mapping (知识规划)

    请深度阅读 **System Prompt 中提供的 XML 教材内容**，制定本章的“微调数据生成计划”。

    ## 1. 动态评估策略
    请非常克制地规划生成数量，我们追求**“模块化”的深度**而非“碎片化”的数量。
    * **总量控制**：建议将本章节划分为 **3-6 个** 核心主题（Topic）。
    * **生成密度**：
        * **核心重难点**（Complex）：建议覆盖全视角（定义+机制+应用），生成 3 条/Topic。
        * **普通知识点**（Simple）：建议只生成 1 条/Topic。
        * **简单常识/细枝末节**：直接跳过。

    ## 2. Topic 划分原则（颗粒度控制 - 关键）
    Topic 的划分必须遵循“高颗粒度”原则，拒绝碎片化：
    * **原子型 (Atomic)**: 针对单一 `<chunk>` 的**核心概念**（如“手性拆分”）。
        * ⚠️ 注意：不要把独立的参数（如“温度”、“pH值”）当作原子型 Topic。
    * **复合型 (Composite)**: 需要跨多个 `<chunk>` 或将**一组相关参数**进行聚类打包。
        * ✅ **聚类要求**：将分散的实验条件、步骤、影响因素，打包成一个逻辑完整的**技术模块**。

    ## 3. Few-Shot Demonstrations (正反示例 - 必读)
    
    ### ❌ Bad Case (错误：颗粒度太细，碎片化)
    * Topic 1: "提取温度" (太细)
    * Topic 2: "提取溶剂" (太细)
    * Topic 3: "提取时间" (太细)
    * **评价**: 这是错误的！不要把参数拆开。

    ### ✅ Good Case (正确：复合型聚类，模块化)
    * Topic 1: **"超声辅助提取的工艺优化策略"** (完美！将温度、溶剂、时间打包成一个复合型主题)
    * Topic 2: **"农药残留的定义与分类"** (原子型，虽然简单但概念完整)
    * **评价**: 既有原子型概念，又有复合型模块，且覆盖全面。

    ## 4. Perspective Analysis (视角评估)
    对于每一个 Topic，请检查原文内容是否足以支持以下视角的深度提问。**只有在原文有确凿证据支持时才选择该视角**：
    * **事实定义 (Fact)**: 原文明确给出了定义、数值、分类或组成结构。
    * **原理机制 (Mechanism)**: 原文详细解释了“为什么”、“反应机理”、“微观变化”或“根本原因”。(如果原文只给了结论没给解释，不要选此项)
    * **应用场景 (Application)**: 原文提到了具体的实验条件、病害现象、生产操作或实际案例。(如果原文是纯理论，不要选此项)
    
    ## 4. Output Format (JSON Only)
    {{
    "reasoning": "本章主要讲述了...",
    "topics": [
        {{
        "topic": "羊肚菌栽培的温湿度耦合效应", 
        "complexity": "hard",
        "suitable_perspectives": ["原理机制", "应用场景"],
        "reason": "文中详细描述了HLB值对乳化稳定性的影响机制，并列举了不同作物的使用场景。"
        }},
        {{
        "topic": "农药的定义", 
        "complexity": "easy",
        "suitable_perspectives": ["事实定义"], // 纯定义，不适合问机制
        "reason": "文中仅给出了标准定义，无深度解释。"
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

    ## 2. The "Quote-First" Rule (新增：防幻觉铁律)
    为了防止知识外泄，你必须遵循“先找证据，再生成”的原则：
    1. **evidence_quote**：必须**逐字逐句**从 XML 文本中复制一段或多段话。这是你回答的唯一法律依据。
    2. **拒绝脑补**：如果你在 XML 中找不到支持【{perspective}】视角的原句，请直接生成 `"type": "无法回答"`，不要强行编造。

    ## 3. Thought Chain Rules (核心：深度推理 - 已增强)
    Output 的 `chain_of_thought` 字段必须包含三个明确步骤，将引用与推理结合：

    1. **Step 1 检索与锁定 (Retrieval & Grounding)**：
    - 明确指出在哪些 `<chunk id="...">` 中找到了关键线索。
    - **必须摘录**出支持你回答的核心原句（即 `evidence_quote` 的来源）。

    2. **Step 2 逻辑推演 (Deduction)**：
    - **核心要求**：不要只当复读机。
    - **深度加工**：根据 Step 1 找到的证据（Chunk A 的条件），结合（Chunk B 的原理），推导出结果 C。
    - *例如：“虽然原句只说了现象 X，但结合上下文的 Y 原理，我们可以推断出这是因为 Z 机制导致的。”*

    3. **Step 3 结论验证 (Verification)**：
    - 检查推导结果是否符合科学常识。
    - **关键检查**：检查你的 Answer 是否完全被 Step 1 的原句所支撑？有没有引入原文没有的外部概念（如具体的受体名称、未提及的数据）？

    ## 4. Output Format (JSON List Only)
    [
        {{
            "type": "{perspective}",
            "evidence_quote": "这里粘贴 Step 1 中找到的原文原句（用于代码校验，必须完全一致）",
            "question": "结合光照和温度要求，分析为何...",
            "referenced_ids": ["1", "3"],
            "chain_of_thought": "Step 1: 在 chunk 1 找到原句'...'。Step 2: 结合 chunk 3 的原理，推导出... Step 3: 经检查，所有概念均有出处。",
            "answer": "根据相关生长特性（引用原文），光照过强会导致..." 
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

    # ================= 6. Knowledge Injection Prompt（知识内化）=================
    KNOWLEDGE_WORKER_PROMPT_TEMPLATE = """
    # Phase 2: Knowledge Internalization (知识内化)

    请基于 **System Prompt 中提供的参考资料**，提炼出一个高价值的“通用领域知识问答”。

    当前主题：**【{topic}】**

    ## 核心差异
    此任务用于训练模型**脱离文档、依靠自身知识**回答问题的能力。
    
    ## 生成约束
    1. **去语境化**：问题和答案**严禁**出现“文中”、“参考资料”等依赖语境的词汇。
    2. **引用留空**：JSON 中的 `referenced_ids` 必须为空 `[]`，但必须提供 `evidence_quote` 用于后台校验。

    ## Thought Chain Rules (关键修改：生成“科学推理”而非“编辑步骤”)
    你的 `chain_of_thought` 必须模拟一个专家的**思考推导过程**，而非数据处理过程：
    1. **知识定位**：思考该问题涉及的核心概念和生物学/化学原理。
    2. **逻辑推演**：解释为什么是这个答案？（例如：通过生态习性推导环境要求，通过反应机理推导结果）。
    3. **结论综合**：将碎片化信息整合成最终结论。
    
    * **错误示范 (严禁出现)**："Step 1: 找到原句。Step 2: 去掉'本文认为'四个字。Step 3: 改写为..."
    * **正确示范**："思考：用户询问羊肚菌的光照需求。羊肚菌属于好气性真菌，虽然菌丝生长需避光，但子实体分化必须有散射光刺激。这与大多数食用菌不同。因此答案需要区分'菌丝阶段'和'出菇阶段'..."

    ## Output Format (JSON List Only)
    [
        {{
            "type": "知识内化",
            "evidence_quote": "原文原句...",
            "question": "羊肚菌栽培过程中光照强度应如何管理？",
            "referenced_ids": [],
            "chain_of_thought": "思考：羊肚菌的光照需求具有阶段性差异。首先分析菌丝体阶段，参考资料指出其通过土壤蔓延，该阶段无论是实验室培养还是田间生长，强光都会抑制菌丝活力，因此推断需避光。其次分析原基分化及子实体生长阶段，资料显示没有散射光诱导无法形成原基，且弱光会导致畸形。综合来看，管理策略应是：前期严格避光发菌，后期引入“三分阳七分阴”的散射光刺激出菇。",
            "answer": "羊肚菌的光照管理需分阶段进行：1. **菌丝生长阶段**：严格避光，强光会抑制菌丝生长；2. **出菇阶段**：必须给予适度的散射光刺激..."
        }}
    ]
    """

    def __init__(self, 
                 max_workers: int = 5,
                 log_file: str = "gen_progress.log",
    ):
        """
        注意：这里不再需要 output_file 参数了
        """
        self.max_workers = max_workers
        self.log_file = log_file
        self.logger = logging.getLogger(self.__class__.__name__)
        # 仍然保留进度记录，避免重复请求扣费

    def _verify_quote_grounding(self, item: Dict, full_xml_text: str) -> bool:
        """
        零成本校验：检查 'evidence_quote' 是否真的存在于原文中。
        阈值建议：0.85 (85%相似度即可)
        """
        import difflib
        # 1. 获取引用
        quote = item.get("evidence_quote", "").strip()
        
        # 特殊情况：如果是“无法回答”或者“总结全文”，可能没有单一引用
        item_type = item.get("type")
        if item_type == "无法回答" or item_type == "知识内化":
             return True
        
        if not quote:
            print(f"❌️ [拒绝] 数据：没有提供引用证据 (evidence_quote 为空)")
            return False

        # 2. 文本归一化处理 (防止因为多一个空格或标点符号导致匹配失败)
        def normalize(s): return "".join(s.split()).lower()

        norm_quote = normalize(quote)
        norm_full_text = normalize(full_xml_text)
        if len(norm_quote) < 5:
            return norm_quote in norm_full_text

        # 3. 核心校验：字符串匹配
        matcher = difflib.SequenceMatcher(None, norm_quote, norm_full_text)
        match = matcher.find_longest_match(0, len(norm_quote), 0, len(norm_full_text))
        ratio = match.size / len(norm_quote)
        if ratio > 0.65:
            return True
        matching_blocks = matcher.get_matching_blocks()
        total_match_len = sum(block.size for block in matching_blocks)
        scattered_ratio = total_match_len / len(norm_quote)
        
        if scattered_ratio > 0.8:
            return True
        
        print(f"🔍 [Difflib 拒绝] 匹配度: {ratio:.2f} | Quote: {quote[:10]}...")
        return False

    def _create_llm_context(self, llm_config: LLMConfig) -> Dict[str, Any]:
        """
        【无状态工厂方法】
        根据 doc_id 创建并返回专属的 OpenAI Client 和 Model Name。
        不再修改 self.client，解决并发冲突问题。
        """
        try:
            if not llm_config:
                # 如果没找到配置，抛出异常或者返回默认
                raise ValueError(f"LLM Config not found for doc_id: {doc_id}")

            # 创建全新的 Client 实例
            new_client = OpenAI(
                api_key=llm_config.api_key,
                base_url=llm_config.base_url
            )
            
            # 返回上下文包
            return {
                "client": new_client,
                "model_name": llm_config.model_name
            }
            
        except Exception as e:
            raise ValueError(f"Failed to create LLM context: {str(e)}") from e

    def _call_llm(self, messages: List[Dict], client, model_name) -> Any:
        """
        [优化版] API 调用与多级 JSON 修复机制
        """
        import re 
        import json
        import time

        for attempt in range(3):
            try:
                # 1. 发起请求
                response = client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0.7,
                    max_tokens=4096
                )
                content = response.choices[0].message.content
                
                # 2. 基础清洗 (去 Markdown)
                clean_json = content.replace("```json", "").replace("```", "").strip()
                
                # === 🛡️ 分级解析策略 ===
                
                # 【尝试 1】直接解析 (最快，保留原汁原味)
                try:
                    return json.loads(clean_json)
                except json.JSONDecodeError:
                    pass # 失败了，进入一级急救

                # 【尝试 2】正则智能修复 (修复 LaTeX 的 \alpha 等，但保留 \n)
                # 逻辑：查找后面不是 " \ / b f n r t u 的反斜杠，将其双写
                try:
                    fixed_json = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', clean_json)
                    return json.loads(fixed_json)
                except json.JSONDecodeError:
                    pass # 还是失败，进入二级急救

                # 【尝试 3】核弹级暴力修复 (保命要紧)
                # 场景：模型输出了极其混乱的反斜杠，我们宁愿牺牲格式也要把数据拿回来
                # 后果：\n 会变成 \\n (失去换行)，但 LaTeX 公式 $Fe^{2+}$ 会变成 $Fe^{{2+}}$ (能存库)
                try:
                    nuclear_json = clean_json.replace('\\', '\\\\')
                    return json.loads(nuclear_json)
                except json.JSONDecodeError as e:
                    # 如果这都解析不了，那确实是 JSON 结构坏了 (比如缺了括号)
                    raise e 

            except Exception as e:
                print(f"⚠️ [LLM 异常] 重试 {attempt+1}/3. 错误类型: {type(e).__name__} | 信息: {e}", flush=True)
                # 如果是解析错误，打印一下 content 方便排查
                # if "Expecting" in str(e):
                #     print(f"📝 [错误JSON片段]: {content[:100]}...", flush=True)
                time.sleep(2) 
        
        return {}

    def _call_llm_bake(self, messages: List[Dict], client, model_name) -> Any:
        """API 调用与 JSON 修复"""
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=model_name,
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
                print(f"⚠️ [LLM 异常] 重试 {attempt+1}/3. 错误信息: {e}")
                time.sleep(2) # 稍微多睡一会
        return {}

    def _validate_and_format_bake(self, generated_items: List[Dict], id_mapping: Dict[str, str]) -> List[Dict]:
        """
        【修正版】返回扁平化的字典，直接对应数据库字段
        """
        valid_records = []
        for item in generated_items:
            short_ids = item.get("referenced_ids", [])
            real_uuids = []
            is_hallucination = False
            
            cleaned_ids = []
            # 1. ID 映射校验
            for sid in short_ids:
                sid_str = str(sid)
                # 2. 暴力清洗：只保留数字 (处理 "2+", "chunk_1", "1." 等情况)
                import re
                # 提取字符串中的第一组连续数字
                match = re.search(r'\d+', sid_str)
                if match:
                    cleaned_ids.append(match.group())
                else:
                    # 如果连数字都没有 (例如 "unknown")，则标记为幻觉
                    if item.get("type") != "知识内化":
                        print(f"⚠️ [ID 异常] 无法识别的 ID 格式: {sid}")
                        
            for sid in cleaned_ids:
                if sid in id_mapping:
                    real_uuids.append(id_mapping[sid])
                else:
                    # 如果清洗后的数字（如 '2'）依然不在 mapping 里，那就是真幻觉
                    is_hallucination = True
                    # 只有普通 RAG 才由于幻觉被杀，知识内化不需要 ID
                    if item.get("type") != "知识内化":
                        break
            
            # 2. 过滤逻辑
            if is_hallucination: continue 
            # 如果是正样本(非"无法回答")且没有引用，视为无效

            # === 【核心修复点】构造扁平字典 ===
            # 数据库 batch_save_instructions 需要直接读取 item['question']
            data_type = item.get("type", "原理机制")
            if not real_uuids and data_type != "无法回答" and data_type != "知识内化": continue
            
            if data_type == "知识内化":
                system_prompt = "你是一位农业领域资深专家。请基于你的专业知识，准确、全面地回答用户的问题。"
                real_uuids = []
            else:
                # RAG 模式
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
                "type": data_type,
                
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

    def _validate_and_format(self, generated_items: List[Dict], id_mapping: Dict[str, str]) -> List[Dict]:
        """
        【最终防御版】绝对不报错的数据清洗
        """
        import re # 放在这里确保一定有
        valid_records = []
        
        for item in generated_items:
            try:
                short_ids = item.get("referenced_ids", [])
                real_uuids = []
                # === 1. ID 清洗与查找 ===
                # 哪怕这一步全挂了，也不能让整个程序崩溃
                if short_ids:
                    for raw_sid in short_ids:
                        try:
                            sid_str = str(raw_sid).strip()
                            
                            # A. 正则提取数字 (处理 "2+", "chunk-5")
                            match = re.search(r'\d+', sid_str)
                            if not match:
                                # 如果完全没数字，跳过
                                continue
                            
                            clean_sid = match.group() # 拿到纯数字字符串 "2"
                            
                            # B. 安全查找 (使用 .get 而不是 [])
                            # 这样绝对不会报 KeyError
                            uuid_val = id_mapping.get(clean_sid)
                            
                            if uuid_val:
                                real_uuids.append(uuid_val)
                            else:
                                # 如果没找到，仅仅打印日志，不抛异常
                                # print(f"⚠️ [ID丢失] ID '{clean_sid}' (原 '{sid_str}') 不在 mapping 中")
                                pass
                                
                        except Exception as inner_e:
                            print(f"⚠️ [ID解析失败] 处理 ID {raw_sid} 时出错: {inner_e}")
                            continue

                # === 2. 核心过滤逻辑 ===
                data_type = item.get("type", "原理机制")
                
                # 只有当 (是RAG类型) 且 (没找到任何有效ID) 时，才跳过
                if data_type not in ["知识内化", "无法回答"] and not real_uuids:
                    continue

                # === 3. 构造 System Prompt ===
                if data_type == "知识内化":
                    # 纯专家模式
                    system_prompt = "你是一位农业领域资深专家。请基于你的专业知识，准确、全面地回答用户的问题。"
                    final_uuids = [] # 知识内化强制为空
                else:
                    # RAG 模式
                    system_prompt = (
                        "你是一个专业的智能助手。请严格依据下方提供的【参考资料】回答用户的【问题】。"
                        "回答需保持客观、准确。如果参考资料中不包含问题的答案，请直接说明无法回答，严禁依据背景知识编造信息。"
                    )
                    final_uuids = real_uuids

                # === 4. 构造记录 ===
                record = {
                    "system_prompt": system_prompt,
                    "question": item.get("question", ""),
                    "answer": item.get("answer", ""),
                    "chain_of_thought": item.get("chain_of_thought", ""),
                    "type": data_type,
                    "ref_chunk_ids": final_uuids,
                    "meta_info": {
                        "raw_short_ids": short_ids,
                        "token_usage": len(item.get("answer", "")) 
                    }
                }
                valid_records.append(record)
                
            except Exception as e:
                # 如果单条数据处理出错，打印日志但不要崩掉整个流程
                print(f"❌ [数据跳过] 处理生成项时发生未知错误: {e}")
                continue
        
        return valid_records

    def _worker_task(
        self, 
        topic: str, 
        perspective: str, 
        xml_context: str, 
        id_mapping: Dict, 
        llm_context: Dict,
        shared_system_content: str
    ) -> List[Dict]:
        """Worker 任务：返回生成的数据列表"""
        print(f"\n👉 [处理中...] 主题:【{topic}】 视角:【{perspective}】")
        try:
            if perspective == "无法回答":
                template = self.NEGATIVE_WORKER_PROMPT_TEMPLATE
            elif perspective == "知识内化":
                template = self.KNOWLEDGE_WORKER_PROMPT_TEMPLATE
            else:
                template = self.WORKER_PROMPT_TEMPLATE

            prompt = template.format(topic=topic, perspective=perspective)
            
            messages = [{"role": "system", "content": shared_system_content}, {"role": "user", "content": prompt}]
            
            result = self._call_llm(messages=messages, client=llm_context["client"], model_name=llm_context["model_name"])
            
            # debug
            self.logger.info(f"✅ [已完成] 主题:【{topic}】 结果如下:\n{json.dumps(result, ensure_ascii=False, indent=2)}\n")
            
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
                valid_records = []
                for item in dataset:
                    generated_type = item.get("type", "")
                    if perspective != "无法回答" and generated_type == "无法回答":
                        print(f"🧹 [自动过滤] 请求视角【{perspective}】但模型未找到证据，退化为【无法回答】，已丢弃。", flush=True)
                        continue
                    # === 这里插入零成本校验 知识内化不需要校验===
                    if self._verify_quote_grounding(item, xml_context):
                        valid_records.append(item)
                    else:
                        # 记录一下被扔掉的数据，方便后期调整 Prompt
                        self.logger.info(f"❌ [数据被拒绝] 主题:【{topic}】 视角:【{perspective}】 引用内容未在原文中找到。")
                        pass
                if valid_records:
                    # 调用校验逻辑，获取清洗后的数据
                    return self._validate_and_format(valid_records, id_mapping)
                
        except Exception as e:
            print(f"❌ Task Error ({topic}): {e}", flush=True)
            
        return [] # 失败返回空列表

    def process_single_h1(
        self, 
        h1_data: Dict,
        llm_config: LLMConfig
    ) -> List[Dict]:
        """
        【改动点】：处理单个 H1，返回该章节生成的所有数据列表
        """
        h1_title = h1_data.get('h1_title', 'Unknown')
        
        xml_context = h1_data['prompt_text']
        id_mapping = h1_data['id_mapping']
        
        llm_context = self._create_llm_context(llm_config)
        print(f"🚀 [Processing] {h1_title}")
        
        # 构建共享的 System Content (包含 XML)
        shared_system_content = self.SYSTEM_PROMPT.replace(
            "{negative_constraints}", self.COMMON_NEGATIVE_CONSTRAINTS
        ).replace(
            "{xml_context}", xml_context
        )
        
        # 1. Mapper Phase
        mapper_prompt = self.MAPPER_PROMPT_TEMPLATE
        plan = self._call_llm(
            messages=[
                {"role": "system", "content": shared_system_content}, 
                {"role": "user", "content": mapper_prompt}
            ],
            client=llm_context["client"],
            model_name=llm_context["model_name"]
        )
        topics = plan.get("topics", [])
        print(f"topics: \n {topics}")
        
        MAX_TOPICS_LIMIT = 6
        if len(topics) > MAX_TOPICS_LIMIT:
            print(f"✂️ [物理截断] Mapper 规划了 {len(topics)} 个主题，强制截取前 {MAX_TOPICS_LIMIT} 个。")
            topics = topics[:MAX_TOPICS_LIMIT]

        if not topics:
            return []

        # 2. Worker Phase (修改这里)
        tasks_to_run = []
        
        # 配额控制（适用于每章）
        negative_sample_quota = 1
        knowledge_injection_quota = 3  # <--- 新增：知识内化配额
        import random
        for topic_obj in topics:
            topic_str = topic_obj.get("topic", "Unknown")
            # 如果模型没返回 suitable_perspectives，则默认只生成"事实定义"以保底
            suggested_perspectives = topic_obj.get("suitable_perspectives", ["事实定义"])
            complexity = topic_obj.get("complexity", "easy").lower()
            
            # --- 1. 常规 RAG 任务
            for p in suggested_perspectives:
                # 过滤掉非法的视角字符串 (以防模型幻觉输出奇怪的视角)
                if p in ["事实定义", "原理机制", "应用场景"]:
                    tasks_to_run.append((topic_str, p))
                # 特殊处理：如果是“原理机制”，可以顺便加一个“反直觉”变体（可选）
                if p == "原理机制":
                     tasks_to_run.append((topic_str, "原理机制（侧重于挖掘不同的细节或反直觉的现象）"))
            
            # --- 2. 新增：知识内化任务 (Knowledge Injection) ---
            is_core_knowledge = "事实定义" in suggested_perspectives or "原理机制" in suggested_perspectives

            if knowledge_injection_quota > 0 and is_core_knowledge:
                if random.random() > 0.5: # 50% 概率
                    tasks_to_run.append((topic_str, "知识内化")) # <--- 派发新任务类型
                    knowledge_injection_quota -= 1

            # === 负样本逻辑 (独立控制) ===
            # 只有当 Topic 比较复杂时 (Hard)，才尝试生成负样本，简单的定义题没必要生成负样本
            if negative_sample_quota > 0 and complexity == "hard" and random.random() > 0.5:
                tasks_to_run.append((topic_str, "无法回答"))
                negative_sample_quota -= 1
            break
        # 3 收集本章节所有生成的数据
        chapter_results = []
        
        if not tasks_to_run:
            print(f"⚠️ [Skip] {h1_title}: No tasks generated by Mapper.")
            return []
        
        # A. 拆分任务：取出第1个 vs 剩余的
        first_task = tasks_to_run[0]
        remaining_tasks = tasks_to_run[1:]

        # === Phase 1: 预热 (Warm-up) ===
        print(f"🔥 [预热中] {h1_title}: 正在执行第 1/{len(tasks_to_run)} 个任务以建立 DeepSeek 缓存...")
        
        # 解包参数
        w_topic, w_perspective = first_task
        
        # 【关键】直接同步调用 _worker_task，不放入线程池
        # 这确保了在预热完成前，后续的大量并发请求不会发出去
        warmup_data = self._worker_task(
            w_topic, w_perspective, 
            xml_context, id_mapping, llm_context, shared_system_content
        )
        if warmup_data:
            chapter_results.extend(warmup_data)

        # === Phase 2: 并发 (Parallel Execution) ===
        if remaining_tasks:
            print(f"⚡ [并发中] 缓存已建立，正在加速执行剩余 {len(remaining_tasks)} 个任务...")
            active_workers = min(self.max_workers, len(remaining_tasks))
            
            with ThreadPoolExecutor(max_workers=active_workers) as executor:
                futures = []
                for t_topic, t_perspective in remaining_tasks:
                    futures.append(
                        executor.submit(
                            self._worker_task,
                            t_topic, t_perspective,
                            xml_context, id_mapping, llm_context, shared_system_content
                        )
                    )
                
                # 收集并发结果
                for future in as_completed(futures):
                    try:
                        data = future.result()
                        if data:
                            chapter_results.extend(data)
                    except Exception as e:
                        # 捕获线程异常，防止影响主流程
                        self.logger.error(f"❌ [并发任务失败] {h1_title}: {e}")

        print(f"✅ [Done] {h1_title} | Generated: {len(chapter_results)} items")
        
        # 最终返回数据给调用者
        return chapter_results
        