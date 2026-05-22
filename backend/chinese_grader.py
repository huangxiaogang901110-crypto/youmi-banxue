"""
语文规则判题 — 填空/选择/看拼音/组词/默写规则判断，减少 DeepSeek 调用。
返回 {"is_correct": True|False|None, "explanation": str} 或 None（无法判断）。
"""
import re


def _try_chinese_rule_grading(question_text: str, child_answer: str, question_type: str = "",
                               standard_answer: str = "") -> dict | None:
    """语文规则判题入口。
    question_type: 填空/选择/看拼音/组词/默写/其他
    """
    qt = (question_text or "").strip()
    ca = (child_answer or "").strip()
    sa = (standard_answer or "").strip()

    if not ca or not qt:
        return None

    # 1. 有标准答案 → 精确/模糊比对
    if sa:
        return _match_standard(ca, sa, qt)

    # 2. 填空：题干带 "___" 或 "（ ）" 或 "（)"
    if _is_fill_blank(qt) or question_type == "填空":
        return None  # 无标准答案则无法规则判

    # 3. 选择：题干含选项 A/B/C/D
    if _is_multiple_choice(qt) or question_type == "选择":
        return None

    # 4. 看拼音写词
    if question_type == "看拼音" or "看拼音" in qt:
        return None

    return None


def _match_standard(child: str, standard: str, qt: str) -> dict | None:
    """比对标准答案和孩子答案"""
    c = child.strip()
    s = standard.strip()

    # 完全相同
    if c == s:
        return {"is_correct": True, "explanation": f"答案「{s}」，孩子写「{c}」✓"}
    # 忽略标点后相同
    c_clean = re.sub(r'[，。！？、；：""''（）\s]', '', c)
    s_clean = re.sub(r'[，。！？、；：""''（）\s]', '', s)
    if c_clean == s_clean:
        return {"is_correct": True, "explanation": f"答案「{s}」，孩子写「{c}」✓"}
    # 子串包含（多字答案）
    if len(s) >= 2 and s in c:
        return {"is_correct": True, "explanation": f"答案含「{s}」，孩子写「{c}」✓"}
    if len(c) >= 2 and c in s:
        return {"is_correct": False, "explanation": f"答案「{s}」，孩子写「{c}」（不完整）✗"}
    # 编辑距离（短答案）
    if len(s) <= 4 and len(c) <= 4 and len(s) == len(c):
        diff = sum(1 for a, b in zip(s, c) if a != b)
        if diff <= 1:
            return {"is_correct": True, "explanation": f"答案「{s}」，孩子写「{c}」✓（近似）"}
    return {"is_correct": False, "explanation": f"答案「{s}」，孩子写「{c}」✗"}


def _is_fill_blank(text: str) -> bool:
    return bool(re.search(r'___|（\s*）|\(\s*\)|\[\\s*\]', text))


def _is_multiple_choice(text: str) -> bool:
    return bool(re.search(r'[A-D][.、．)]', text))
