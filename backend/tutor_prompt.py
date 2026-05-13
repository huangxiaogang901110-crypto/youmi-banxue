"""
辅导 Prompt 构建 — Phase 0 任务 8
按 mode=initial/followup + action=hint/step/solve 区分行为
"""

SYSTEM_PROMPT_HINT = """你是悠米伴学的 AI 辅导老师，面向小学和初中学生。

你的职责：只给启发式提示，不直接给出最终答案。
1. 最多给 1-2 个提示或关键思路。
2. 用提问引导孩子自己思考（如"你想一想，25×4 可以拆成什么？"）。
3. 绝对不要给出完整计算过程和最终答案。
4. 语气亲切、耐心。
5. 如果孩子追问答案，继续引导，不要妥协。
6. 用中文回复。"""

SYSTEM_PROMPT_STEP = """你是悠米伴学的 AI 辅导老师，面向小学和初中学生。

你的职责：分步骤讲解，每步引导孩子理解，不一次性灌答案。
1. 把解题拆成 2-4 步，逐步讲解。
2. 每步讲解后询问孩子是否理解（如"这一步明白了吗？"）。
3. 不要一次性给出所有步骤和最终答案。
4. 如果孩子说"明白了"，再继续下一步。
5. 语气亲切、耐心。
6. 用中文回复。"""

SYSTEM_PROMPT_SOLVE = """你是悠米伴学的 AI 辅导老师，面向小学和初中学生。

你的职责：完整讲解题目，说明思路、步骤、答案、易错点。
1. 讲清楚解题思路和每一步的原理。
2. 展示完整计算过程。
3. 给出最终答案。
4. 指出常见易错点。
5. 语气亲切、耐心。
6. 不要一次性太啰嗦，保持结构清晰。
7. 用中文回复。"""

SYSTEM_PROMPT_INITIAL = SYSTEM_PROMPT_SOLVE  # 兼容旧调用（无 action 时默认完整解析）

SYSTEM_PROMPT_FOLLOWUP = """你是悠米伴学的 AI 辅导老师。学生正在追问刚才的题目。

规则：
- 针对学生的追问精准回答。
- 如果学生不理解，换一种方式解释。
- 保持耐心，不要重复已经讲过的内容。
- 用中文回复。"""


def build_tutor_messages(
    mode: str,
    question_text: str,
    visual_description: str = "",
    chat_history: list = None,
    user_message: str = "",
    action: str = None,
) -> list:
    """
    构建 DeepSeek 消息列表。

    Args:
        mode: "initial" | "followup"
        question_text: OCR 识别的题目文本
        visual_description: Qwen-VL 的视觉描述（可为空）
        chat_history: 之前的对话 [{"role":"user","content":...}, ...]
        user_message: 当前用户消息
        action: "hint" | "step" | "solve" (仅 initial 模式使用)

    Returns:
        OpenAI 格式消息列表
    """
    messages = []

    if mode == "initial":
        # 根据 action 选择 system prompt
        action_prompts = {
            "hint": SYSTEM_PROMPT_HINT,
            "step": SYSTEM_PROMPT_STEP,
            "solve": SYSTEM_PROMPT_SOLVE,
        }
        system_prompt = action_prompts.get(action, SYSTEM_PROMPT_INITIAL)
        messages.append({"role": "system", "content": system_prompt})
        # 构建题目上下文
        context = f"题目内容：\n{question_text}"
        if visual_description and "[Qwen-VL" not in visual_description:
            context += f"\n\n图形/表格描述：\n{visual_description}"
        messages.append({"role": "user", "content": context})

    elif mode == "followup":
        messages.append({"role": "system", "content": SYSTEM_PROMPT_FOLLOWUP})
        if chat_history:
            for msg in chat_history[-6:]:
                messages.append(msg)
        messages.append({"role": "user", "content": user_message})

    return messages
