"""
辅导 Prompt 构建 — Phase 0 任务 8
按 mode=initial/followup 区分首次辅导和追问
"""

SYSTEM_PROMPT_INITIAL = """你是悠米伴学的 AI 辅导老师，面向小学和初中学生。

你的职责：
1. 用亲切、耐心的语气讲解题目
2. 分步骤引导思考，不直接给最终答案
3. 指出易错点和关键思路
4. 每步讲解后询问学生是否理解

规则：
- 如果题目包含图形/表格描述（来自视觉识别），请结合图形讲解
- 数学题要展示计算过程
- 不要说"这道题很简单"之类的话
- 用中文回复"""

SYSTEM_PROMPT_FOLLOWUP = """你是悠米伴学的 AI 辅导老师。学生正在追问刚才的题目。

规则：
- 针对学生的追问精准回答
- 如果学生不理解，换一种方式解释
- 保持耐心，不要重复已经讲过的内容
- 用中文回复"""


def build_tutor_messages(
    mode: str,
    question_text: str,
    visual_description: str = "",
    chat_history: list = None,
    user_message: str = "",
) -> list:
    """
    构建 DeepSeek 消息列表。

    Args:
        mode: "initial" | "followup"
        question_text: OCR 识别的题目文本
        visual_description: Qwen-VL 的视觉描述（可为空）
        chat_history: 之前的对话 [{"role":"user","content":...}, ...]
        user_message: 当前用户消息

    Returns:
        OpenAI 格式消息列表
    """
    messages = []

    if mode == "initial":
        messages.append({"role": "system", "content": SYSTEM_PROMPT_INITIAL})
        # 构建题目上下文
        context = f"题目内容：\n{question_text}"
        if visual_description and "[Qwen-VL" not in visual_description:
            context += f"\n\n图形/表格描述：\n{visual_description}"
        messages.append({"role": "user", "content": context})

    elif mode == "followup":
        messages.append({"role": "system", "content": SYSTEM_PROMPT_FOLLOWUP})
        # 带上历史对话（最近几轮）
        if chat_history:
            for msg in chat_history[-6:]:  # 最近 6 条
                messages.append(msg)
        messages.append({"role": "user", "content": user_message})

    return messages
