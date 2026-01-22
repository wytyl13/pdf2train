#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/03 11:15
@Author  : weiyutao
@File    : deepseek_arig_generator.py
"""



import os
import json
import time
from typing import List, Dict, Optional, Union, Set
from openai import OpenAI
import unicodedata
import re

class DeepSeekAgriGenerator:
    
    FINETUNE_SYSTEM_PROMPT = "你是一个专业的农业领域专家，能够基于教材知识回答复杂的问题。"
    
    # ================= 核心约束 (全局生效) =================
    COMMON_NEGATIVE_CONSTRAINTS = """
    【严重警告 - 负面约束】
    1. **拒绝“自指”**：输出的答案必须是独立的知识陈述。**严禁**出现“根据提供的文本”、“文中提到”、“如上所述”等字样。直接回答事实即可。
    2. **拒绝“省略”**：答案必须完整。**严禁**使用“及其他”、“等”、“...”来省略关键信息。
    3. **拒绝“见图”**：如果文中包含“如图所示”、“见图3-1”，请将其转化为纯文字的详细描述。如果无法用文字描述清楚，请直接**忽略**该知识点。
    4. **拒绝“机械填空”**：不要出“文中提到的温度是多少？”这种题。要出“该过程的最佳温度是多少？过高会有什么后果？”。
    """

    # ================= 提示词模板 =================
    
    # 1. System Prompt
    SYSTEM_PROMPT_TEMPLATE = """
    # Role
    你是一位拥有 20 年教学经验的农业/化学领域资深教授，同时也是大语言模型微调数据构建专家。

        # Output Standard (输出规范)
    在生成数据时，请严格遵守以下格式要求：
    1. **化学式与复杂公式**：必须保持格式统一，使用 **LaTeX 格式**。
       * 正确示例：$H_2O$, $CO_2$, $Fe^{2+}$, $[ \alpha ]_D^{20}$
    
    2. **物理单位与数值**：
       * **严禁**将简单物理单位（如 g, mL, cm, m, L, mol）放入 LaTeX 公式中。
       * 请使用**普通文本**书写单位，并在数字和单位之间保留一个空格。
       * 对于温度，建议直接使用符号或中文。
       * **正确示例**：1 g, 100 mL, 10 cm, 20 °C, 50 kg/亩
       * **错误示例**：$1\mathrm{g}$, $100\ mL$, $20^{\circ}C$

    3. **标点符号**：使用中文标点符号（公式内部除外）。

    # Task Context
    我们正在基于提供的教材章节，构建一个高质量的“RAG（检索增强生成）微调数据集”。
    所有生成的数据必须严格遵守以下负面约束：
    {negative_constraints}

    # Material (教材内容)
    {chapter_content}
    """

    # 2. Mapper Prompt
    MAPPER_PROMPT = """
    # Phase 1: Knowledge Mapping & Dynamic Planning (知识规划)

    请深度阅读上述教材内容，制定本章的“微调数据生成计划”。

    ## 1. 动态评估策略
    请非常克制地规划生成数量，我们追求多样性而非数量。
    *   **核心重难点**（如复杂反应机理、易混淆概念）：建议生成 3-5 条/Topic。
    *   **普通知识点**（如定义、参数）：建议生成 1-2 条/Topic。
    *   **简单常识**：可以直接跳过，或仅生成 1 条。

    ## 2. Topic 划分原则
    Topic 必须包含：
    *   **原子型**：针对单一小节的核心概念。
    *   **复合型**：跨小节的逻辑关联。

    ## 3. Output Format (JSON Only)
    请**只输出**一个 JSON 对象：
    {
    "chapter_assessment": "简述本章特点...",
    "total_estimated_count": 25,
    "topics": [
        {
        "topic": "SN1与SN2反应机理的深度对比",
        "reason": "本章核心难点...",
        "suggested_count": 4
        },
        ...
    ]
    }
    """

    # 3. Worker Prompt
    WORKER_PROMPT_TEMPLATE = """
    # Phase 2: Targeted Data Generation (定向生成)

    当前任务：基于主题 **【{{TOPIC}}】**，生成 **{{COUNT}} 条** 类型为 **【{{PERSPECTIVE}}】** 的微调数据。

    ## 1. RAG Simulation (RAG 场景模拟)
    * **构造输入**：【参考资料】必须是**含噪音的长段落**（300-600字符）。你可以拼接不相关的段落来增加难度。
    * **拒绝洁癖**：不要只摘录答案句，必须包含上下文背景。

    ## 2. Perspective Focus: {{PERSPECTIVE}}
    你必须严格遵守当前指定的视角：
    * **如果当前是【原理机制】**：问题必须涉及“为什么”、“反应机理”、“微观变化”或“根本原因”。**严禁**生成简单的定义或填空题。
    * **如果当前是【应用场景】**：问题必须设定具体的实验条件、病害现象或生产环境。
    * **如果当前是【事实定义】**：关注核心参数、结构组成或分类标准。

    ## 3. Thought Chain Rules (核心：深度推理)
    Output 的 `<thought>` 标签内容必须包含三个明确步骤：
    1.  **Step 1 检索与降噪**：明确指出参考资料中哪部分是干扰项，哪部分是关键依据。
    2.  **Step 2 逻辑推演 (至关重要)**：
        * 不要只说“文中提到了X”。
        * **必须解释**：根据文中提到的条件A，结合化学/生物原理，推导出结果B。例如：“虽然文中未直接解释，但由于A具有强氧化性，且环境为碱性，因此优先发生氧化反应...”
    3.  **Step 3 结论验证**：检查推导结果是否符合科学常识。

    ## 4. Output Format (JSON List Only)
    [
      {
        "type": "{{PERSPECTIVE}}",
        "input": "【参考资料】\\n(一段含噪音的长文本)...\\n\\n【问题】\\n(符合当前视角的问题)",
        "output": "<thought>Step 1: 资料前半段讲的是呼吸作用，是噪音，跳过。关键在第二段... Step 2: 既然甲醛不仅空间位阻小，且羰基碳正电性更强，OH-会优先进攻甲醛。这解释了为何甲醛总是先被氧化... Step 3: 结论符合康尼查罗反应规律。</thought>(正式回答...)"
      }
    ]
    """
    
    
    NEGATIVE_WORKER_PROMPT = """
    # Phase 2: Negative Sample Generation (拒答测试)

    当前任务：基于主题 **【{{TOPIC}}】**，生成 **1 条** **【无法回答】** 的微调数据。

    ## 核心要求
    1.  **看似相关**：问题必须包含参考资料中的关键词（如“光合作用”、“反应温度”），看起来似乎能在文中找到答案。
    2.  **实则缺失**：确保【参考资料】中**绝对没有**包含该问题的核心答案。
    3.  **拒答回复**：Assistant 的回答必须礼貌地指出：“根据提供的参考资料，无法回答关于...的问题，因为资料中未提及相关信息。”

    ## Output Format (JSON Only)
    Please output the result in strictly valid **JSON** format. Do not output any other text.
    
    [
      {
        "type": "无法回答",
        "input": "【参考资料】\\n(关于光合作用光反应的描述)...\\n\\n【问题】\\n光合作用暗反应的具体酶促反应方程是什么？",
        "output": "根据提供的参考资料，文中仅详细描述了光反应阶段的过程，未提及暗反应的具体酶促反应方程，因此无法回答该问题。"
      }
    ]
    """
    
    

    def __init__(self, 
                 input_file: str, 
                 output_file: str, 
                 log_file: str = "gen_progress.log", # 新增：日志文件路径
                 api_client: Optional[OpenAI] = None,
                 api_key: Optional[str] = None,
                 base_url: Optional[str] = "https://api.deepseek.com",
                 model_name: str = "deepseek-chat"):
        
        self.input_file = input_file
        self.output_file = output_file
        self.log_file = log_file # 保存日志路径
        self.model_name = model_name

        if api_client:
            self.client = api_client
        elif api_key:
            self.client = OpenAI(api_key=api_key, base_url=base_url)
        else:
            raise ValueError("必须提供 api_client 或 api_key")


    def _clean_text_for_finetune(self, text: str) -> str:
        """
        [关键增强] 深度清洗文本，专治 PDF 解析后遗症
        """
        if not text:
            return ""
            
        # 1. NFKC 归一化：把 '𝑓'(数学斜体) 变 'f'，把 '−'(数学减号) 变 '-'，把不可见空格变正常
        text = unicodedata.normalize('NFKC', text)
        
        # 2. 强制修复连字 (Ligatures) - NFKC 不一定能全搞定
        text = text.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff")
        
        # 3. 解决公式粘连问题：在 $ 和 $$ 两侧强制加空格
        # 这一步能极大改善 Tokenizer 对公式的识别
        # 处理行间公式 $$...$$
        text = re.sub(r'(?<!\s)(\$\$.*?\$\$)(?!\s)', r' \1 ', text, flags=re.DOTALL)
        # 处理行内公式 $...$
        text = re.sub(r'(?<!\s)(\$.*?\$)(?!\s)', r' \1 ', text)
        
        # 4. 清理多余空格
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text

    # ================= 新增：进度管理方法 =================
    def _load_processed_chapters(self) -> Set[str]:
        """读取日志，返回已完成的章节标题集合"""
        if not os.path.exists(self.log_file):
            return set()
        processed = set()
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        processed.add(line.strip())
        except Exception:
            pass
        return processed

    def _mark_chapter_as_done(self, chapter_title: str):
        """将章节标记为已完成"""
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"{chapter_title}\n")
        except Exception as e:
            print(f"⚠️ 无法写入进度日志: {e}")
    # ====================================================

    def _call_llm_json(self, messages: List[Dict]) -> Union[Dict, List]:
        """封装 API 调用，强制 JSON 返回，并包含自动修复逻辑"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    temperature=0.7,
                    response_format={"type": "json_object"},
                    max_tokens=8192
                )
                content = response.choices[0].message.content
                
                # 1. 基础清洗
                clean_text = content.replace("```json", "").replace("```", "").strip()
                
                # 2. 【核心修复】尝试直接解析
                try:
                    return json.loads(clean_text)
                except json.JSONDecodeError:
                    # 3. 如果解析失败，说明有非法反斜杠（LaTeX公式导致的）
                    # 使用正则：找到所有“后面不是有效转义字符”的反斜杠，并将其双写
                    # 有效的 JSON 转义字符是: " \ / b f n r t u
                    print(f"⚠️ 检测到 JSON 格式错误 (尝试 {attempt+1})，正在尝试自动修复 LaTeX 反斜杠...")
                    
                    # 正则逻辑：匹配一个 \，且其后面紧跟的字符不是 ["\/bfnrtu] 中的任何一个
                    fixed_text = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', clean_text)
                    
                    return json.loads(fixed_text)
                    
            except Exception as e:
                print(f"⚠️ API 调用或解析彻底失败 (尝试 {attempt+1}): {e}")
                # 打印出有问题的文本以便调试
                # print(f"Problematic JSON: {clean_text[:200]}...") 
                time.sleep(2)
                
        return {}

    def _save_batch(self, data_list: List[Dict]):
        """
        实时保存数据，将中间格式转换为 OpenAI Chat Completion JSONL 格式
        Target Format: {"messages": [{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}]}
        """
        if not data_list: return
        
        with open(self.output_file, "a", encoding="utf-8") as f:
            for item in data_list:
                # 1. 提取 User 内容 (包含了 Context 和 Question)
                user_content = item.get("input", "")
                
                # 2. 提取 Assistant 内容 (包含了 Thought 和 Answer)
                assistant_content = item.get("output", "")
                
                # 简单的合法性检查
                if not user_content or not assistant_content:
                    continue

                # 【新增步骤】在写入前进行深度清洗
                user_clean = self._clean_text_for_finetune(user_content)
                assistant_clean = self._clean_text_for_finetune(assistant_content)

                # 3. 构造 OpenAI 格式对象
                record = {
                    "messages": [
                        {
                            "role": "system", 
                            "content": self.FINETUNE_SYSTEM_PROMPT
                        },
                        {
                            "role": "user", 
                            "content": user_clean
                        },
                        {
                            "role": "assistant", 
                            "content": assistant_clean
                        }
                    ]
                }
                # 4. 写入一行 JSONL
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def process_chapter_pipeline(self, chapter_title: str, chapter_content: str):
        print(f"\n🚀 [Start] 处理章节：{chapter_title} (长度: {len(chapter_content)})")
        
        system_msg = {
            "role": "system",
            "content": self.SYSTEM_PROMPT_TEMPLATE.replace("{chapter_content}", chapter_content).replace("{negative_constraints}", self.COMMON_NEGATIVE_CONSTRAINTS)
        }

        # 1. Mapper: 规划主题
        print("🗺️  正在规划考点 (Mapper)...")
        mapper_msgs = [system_msg, {"role": "user", "content": self.MAPPER_PROMPT}]
        plan = self._call_llm_json(mapper_msgs)
        topics = plan.get("topics", [])
        
        if not topics:
            print("❌ Mapper 规划失败，跳过本章")
            return 

        print(f"✅ 规划完成！共 {len(topics)} 个 Topic")

        # 2. Worker: 分桶策略执行
        # 定义生成策略：(视角名称, 权重/数量占比, 使用的Prompt模板)
        # 注意：这里我们强制规定了每个Topic要生成的视角分布
        STRATEGY = [
            ("原理机制 (Deep Mechanism)", 2, self.WORKER_PROMPT_TEMPLATE), # 权重最高，每个Topic至少2条机理
            ("事实定义 (Fact)", 1, self.WORKER_PROMPT_TEMPLATE),           # 1条事实
            ("应用场景 (Scenario)", 1, self.WORKER_PROMPT_TEMPLATE),       # 1条场景
            ("无法回答 (Negative)", 1, self.NEGATIVE_WORKER_PROMPT)        # 1条负样本
        ]

        total_gen_count = 0

        for idx, item in enumerate(topics):
            topic_name = item.get('topic', '未知主题')
            # 原始建议数量如果过大，可以限制一下，或者直接忽略建议，使用我们的强制策略
            # 这里我们采用“强制策略”优先，确保质量
            
            print(f"  🔨 [{idx+1}/{len(topics)}] 处理主题: {topic_name}")
            
            # 对每个主题，遍历所有视角进行生成
            for perspective, count, prompt_template in STRATEGY:
                if count <= 0: continue

                # 构造具体的 Prompt
                final_prompt = prompt_template.replace("{{TOPIC}}", str(topic_name))\
                                              .replace("{{COUNT}}", str(count))\
                                              .replace("{{PERSPECTIVE}}", perspective)
                
                worker_msgs = [system_msg, {"role": "user", "content": final_prompt}]
                
                # 调用 LLM
                # print(f"      -> 正在生成 [{perspective}] x {count}...") 
                result = self._call_llm_json(worker_msgs)
                
                batch_data = []
                if isinstance(result, list):
                    batch_data = result
                elif isinstance(result, dict):
                    # 尝试寻找 list 类型的 value
                    for v in result.values():
                        if isinstance(v, list):
                            batch_data = v
                            break
                
                # 简单验证一下生成数量，没生成够也不强求，避免死循环
                if batch_data:
                    self._save_batch(batch_data)
                    total_gen_count += len(batch_data)
                    print(f"      ✅ [{perspective}]: +{len(batch_data)} 条")
                else:
                    print(f"      ⚠️ [{perspective}]: 生成为空")

                # 避免并发过快
                time.sleep(0.5) 

        # 3. 标记完成
        self._mark_chapter_as_done(chapter_title)
        print(f"🏁 章节 【{chapter_title}】 完成，累计生成 {total_gen_count} 条数据！")
    
    

    def run(self):
        if not os.path.exists(self.input_file):
            print(f"❌ 找不到输入文件: {self.input_file}")
            return

        print("📂 加载整章数据...")
        with open(self.input_file, "r", encoding="utf-8") as f:
            chapters = json.load(f)

        # 1. 加载进度
        processed_chapters = self._load_processed_chapters()
        print(f"🔄 已完成章节数：{len(processed_chapters)} / {len(chapters)}")
        print(f"💾 数据输出至：{self.output_file}")
        
        for i, ch in enumerate(chapters):
            title = ch['chapter_title']

            # 2. 断点检查
            if title in processed_chapters:
                # 为了不刷屏，每隔几章提示一次跳过
                if i % 5 == 0: 
                    print(f"⏭️  [跳过] 已完成章节: {title}")
                continue

            try:
                self.process_chapter_pipeline(title, ch['context'])
            except KeyboardInterrupt:
                print("\n🛑 用户手动停止！进度文件 `gen_progress.log` 已保存。")
                print("下次运行将从当前章节继续。")
                break
            except Exception as e:
                print(f"❌ 处理章节 {title} 时发生未知错误: {e}")
                print("程序将继续尝试下一章，或请检查网络连接。")
                continue
                
        print("\n🎉 所有任务流程结束！")

if __name__ == "__main__":
    API_KEY = "sk-d8b7a899050f41c7a3deac1cb149cbb4"
    
    generator = DeepSeekAgriGenerator(
        input_file="youhua_chapter.json",
        output_file="final_rag_sft_dataset_v5.jsonl",
        log_file="gen_progress.log", # 指定日志文件名
        api_key=API_KEY,
        base_url="https://api.deepseek.com",
        model_name="deepseek-chat"
    )
    
    generator.run()