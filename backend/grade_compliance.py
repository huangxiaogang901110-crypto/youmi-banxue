"""判后合规校验 — 防 DeepSeek 误判数学题"""

import re


def parse_arithmetic(expr: str) -> tuple | None:
    """解析简单算式 如 '97-6=91' → ('97-6', 97, '-', 6, 91)"""
    expr = expr.strip().replace(" ", "")
    m = re.match(r'^(\d+)([+\-×xX*/÷])(\d+)=(\d+)$', expr)
    if not m:
        return None
    a = int(m.group(1))
    op = m.group(2)
    b = int(m.group(3))
    expected = int(m.group(4))
    op_map = {'×': '*', 'x': '*', 'X': '*', '÷': '/'}
    op = op_map.get(op, op)
    return (m.group(0), a, op, b, expected)


def eval_arithmetic(a: int, op: str, b: int) -> int | None:
    """安全求值"""
    try:
        if op == '+':
            return a + b
        elif op == '-':
            return a - b
        elif op == '*':
            return a * b
        elif op == '/':
            return a // b if b != 0 else None
        return None
    except Exception:
        return None


def extract_student_expr(student_answer: str) -> str | None:
    """从答案中提取算式片段 '数字 op 数字 = 数字'"""
    if not student_answer:
        return None
    s = student_answer.strip()
    m = re.search(r'(\d+[+\-×xX*/÷]\d+=\d+)', s)
    return m.group(1) if m else None


def check_compliance(questions: list) -> dict:
    """
    对判题结果做合规校验。
    规则：如果 student_answer 是数学算式，算式求值正确，
    但 DeepSeek 判了 is_correct=False → 翻转为 True。
    只翻正向（判错→翻对），不翻反向。
    """
    flipped = 0
    details = []

    for q in questions:
        sa = None
        is_correct = None
        if hasattr(q, 'student_answer'):
            sa = q.student_answer
            is_correct = q.is_correct
        elif isinstance(q, dict):
            sa = q.get('student_answer')
            is_correct = q.get('is_correct')

        if is_correct is not False or not sa:
            continue

        expr = extract_student_expr(str(sa))
        if not expr:
            continue

        parsed = parse_arithmetic(expr)
        if not parsed:
            continue

        left_expr, a, op, b, expected = parsed
        actual = eval_arithmetic(a, op, b)
        if actual is None:
            continue

        if actual == expected:
            if hasattr(q, 'is_correct'):
                q.is_correct = True
            else:
                q['is_correct'] = True
            flipped += 1
            qn = q.question_number if hasattr(q, 'question_number') else q.get('question_number', '?')
            details.append({
                "question_number": qn,
                "expr": left_expr,
                "reason": f"合规校验：{a}{op}{b}={actual}，孩子答案正确，翻转误判"
            })

    return {"flipped": flipped, "details": details}
