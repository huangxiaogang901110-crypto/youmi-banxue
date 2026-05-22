"""
数学规则判题 — 口算/加减乘除/比大小/填空优先规则判断，减少 DeepSeek 调用。
返回 {"is_correct": True|False|None, "explanation": str} 或 None（无法判断）。
"""
import re

# 四则运算模式
_ARITH_PAT = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*([+\-×÷])\s*(\d+(?:\.\d+)?)\s*=\s*(\d+(?:\.\d+)?)\s*$')
_COMPARE_PAT = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*([<>])\s*(\d+(?:\.\d+)?)\s*$')
_FILL_PAT = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*=\s*(\d+(?:\.\d+)?)\s*$')

def _try_math_rule_grading(question_text: str, student_answer: str) -> dict | None:
    """Try to grade a math question using regex rules.
    Returns {"is_correct": bool, "explanation": str} or None if unable to judge.
    """
    qt = question_text.strip()
    sa = student_answer.strip()

    # 1. 四则运算：如 "12 + 5 = 17"
    m = _ARITH_PAT.match(qt)
    if m:
        a, op, b, expected = float(m.group(1)), m.group(2), float(m.group(3)), float(m.group(4))
        try:
            sa_num = float(sa)
        except ValueError:
            return None
        correct = abs(sa_num - expected) < 0.01
        if correct:
            return {"is_correct": True, "explanation": f"{a}{op}{b}={expected}，孩子写{sa} ✓"}
        else:
            return {"is_correct": False, "explanation": f"{a}{op}{b}={expected}，孩子写{sa} ✗"}

    # 2. 比大小：如 "12 > 5"
    m = _COMPARE_PAT.match(qt)
    if m:
        a, op, b = float(m.group(1)), m.group(2), float(m.group(3))
        try:
            sa_num = float(sa)
        except ValueError:
            return None
        if op == '>':
            correct = a > b
        elif op == '<':
            correct = a < b
        else:
            return None
        return {"is_correct": correct, "explanation": f"{a}{op}{b}，孩子写{sa}"}

    # 3. 填空等号：如题目已含 "___=12"，孩子填了 12
    m = _FILL_PAT.match(qt)
    if m:
        expected = float(m.group(2))
        try:
            sa_num = float(sa)
        except ValueError:
            return None
        correct = abs(sa_num - expected) < 0.01
        return {"is_correct": correct, "explanation": f"答案={expected}，孩子写{sa}"}

    # 4. 紧凑格式：如 "7+8=15"，学生写 "15"
    _COMPACT_PAT = re.compile(r'(\d+(?:\.\d+)?)\s*([+\-×÷])\s*(\d+(?:\.\d+)?)\s*=\s*(\d+(?:\.\d+)?)')
    m = _COMPACT_PAT.search(qt)
    if m:
        expected = float(m.group(4))
        try:
            sa_num = float(sa)
        except ValueError:
            return None
        correct = abs(sa_num - expected) < 0.01
        a, op, b = m.group(1), m.group(2), m.group(3)
        return {"is_correct": correct, "explanation": f"{a}{op}{b}={expected}，孩子写{sa}{' ✓' if correct else ' ✗'}"}

    return None
