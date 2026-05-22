"""
英语规则判题 — 选择/填空/单词/短句规则判断，减少 DeepSeek 调用。
返回 {"is_correct": True|False|None, "explanation": str} 或 None（无法判断）。
"""
import re


def _try_english_rule_grading(question_text: str, child_answer: str, question_type: str = "",
                               standard_answer: str = "") -> dict | None:
    """英语规则判题入口。"""
    qt = (question_text or "").strip()
    ca = (child_answer or "").strip()
    sa = (standard_answer or "").strip()

    if not ca or not qt:
        return None

    # 1. 有标准答案 → 比对
    if sa:
        return _match_english(ca, sa, qt)

    # 2. 无标准答案 → 无法规则判
    return None


def _match_english(child: str, standard: str, qt: str) -> dict | None:
    """英语答案比对"""
    c = child.strip().lower()
    s = standard.strip().lower()

    # 完全相同
    if c == s:
        return {"is_correct": True, "explanation": f"答案「{standard}」，孩子写「{child}」✓"}
    # 忽略尾部标点
    c_clean = re.sub(r'[.!?,;:]+$', '', c)
    s_clean = re.sub(r'[.!?,;:]+$', '', s)
    if c_clean == s_clean:
        return {"is_correct": True, "explanation": f"答案「{standard}」，孩子写「{child}」✓"}
    # 单字母选择 A/B/C/D
    if len(s) == 1 and s in 'abcd' and len(c) <= 3:
        if c.strip().lower() == s:
            return {"is_correct": True, "explanation": f"选{s.upper()}，孩子选{c.upper()} ✓"}
        else:
            return {"is_correct": False, "explanation": f"选{s.upper()}，孩子选{c.upper()} ✗"}
    # 短单词：编辑距离容错
    if len(s) <= 8 and len(c) <= 8 and abs(len(s) - len(c)) <= 1:
        # Levenshtein 手动
        diff = _levenshtein(s, c)
        if diff <= 1:
            return {"is_correct": True, "explanation": f"答案「{standard}」，孩子写「{child}」✓（近似）"}
        if diff <= 2 and len(s) >= 4:
            return {"is_correct": False, "explanation": f"答案「{standard}」，孩子写「{child}」✗（拼写可能错误）"}
    return {"is_correct": False, "explanation": f"答案「{standard}」，孩子写「{child}」✗"}


def _levenshtein(a: str, b: str) -> int:
    """Levenshtein distance"""
    if len(a) < len(b):
        a, b = b, a
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(
                prev[j + 1] + 1,
                curr[j] + 1,
                prev[j] + (0 if ca == cb else 1)
            ))
        prev = curr
    return prev[-1]
