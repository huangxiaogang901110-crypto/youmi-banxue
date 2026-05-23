"""数学规则判题。

第一轮只覆盖高置信的基础算式：
- 口算：`12 + 34 = ?`
- 简单带括号：`36 ÷ (4 + 2) × 3 = ?`

解析失败时返回 None，让上层回退到 DeepSeek。
"""

from __future__ import annotations

import ast
import operator
import re
from typing import Optional


_OP_NORMALIZATION = str.maketrans({
    "×": "*",
    "x": "*",
    "X": "*",
    "÷": "/",
    "（": "(",
    "）": ")",
    "＝": "=",
    "？": "?",
})
_ALLOWED_TEXT = re.compile(r"[0-9+\-*/().=\s?]+")
_ANSWER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def _normalize_text(text: str) -> str:
    return text.translate(_OP_NORMALIZATION).replace(" ", "")


def _extract_expression(question_text: str) -> Optional[str]:
    normalized = _normalize_text(question_text)
    normalized = re.sub(r"^\d+[.、)]", "", normalized)
    if "=" in normalized:
        normalized = normalized.split("=", 1)[0]
    if ":" in normalized:
        normalized = normalized.split(":", 1)[-1]
    if "：" in normalized:
        normalized = normalized.split("：", 1)[-1]

    candidate = "".join(ch for ch in normalized if _ALLOWED_TEXT.fullmatch(ch) or ch.isdigit())
    candidate = candidate.rstrip("?")
    if not candidate or not any(op in candidate for op in "+-*/"):
        return None
    return candidate


def _extract_numeric_answer(student_answer: str) -> Optional[float]:
    match = _ANSWER_RE.search(_normalize_text(student_answer))
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _safe_eval(expr: str) -> Optional[float]:
    try:
        node = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    return _eval_ast(node.body)


def _eval_ast(node) -> Optional[float]:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _eval_ast(node.operand)
        return -value if value is not None else None
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
        left = _eval_ast(node.left)
        right = _eval_ast(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Div) and right == 0:
            return None
        return _BINARY_OPS[type(node.op)](left, right)
    return None


def _numbers_equal(actual: float, expected: float) -> bool:
    return abs(actual - expected) < 1e-6


def _try_math_rule_grading(question_text: str, student_answer: str) -> dict | None:
    expr = _extract_expression(question_text)
    answer = _extract_numeric_answer(student_answer)
    if expr is None or answer is None:
        return None

    expected = _safe_eval(expr)
    if expected is None:
        return None

    is_correct = _numbers_equal(expected, answer)
    expected_display = int(expected) if float(expected).is_integer() else round(expected, 4)
    return {
        "is_correct": is_correct,
        "explanation": f"规则判题：{expr} = {expected_display}",
    }
