"""Unit test for question_cutter"""
from question_cutter import cut_to_questions, cut_questions, _parse_question_number

# Test 1: Number parsing
print("=== Test 1: 题号解析 ===")
tests = [
    ("1. 小明有12个苹果", (1, "1.")),
    ("2、计算题", (2, "2、")),
    ("(3) 选择题", (3, "(3)")),
    ("（4）填空题", (4, "（4）")),
    ("① 圈号题", (1, "①")),
    ("一、中文大题", (1, "一、")),
    ("三．语文题", (3, "三．")),
]
for text, expected in tests:
    result = _parse_question_number(text)
    assert result[0] == expected[0], f"FAIL: '{text}' -> {result}, expected number={expected[0]}"
    print(f"  OK '{text[:20]}' -> {result}")

# Test 2: Cut with anchors
print("\n=== Test 2: 锚点切题 ===")
blocks = [
    {"text": "1. 小明有12个苹果，吃了3个", "pos": [10, 10, 400, 30]},
    {"text": "又买了5个，还剩几个？", "pos": [10, 45, 300, 28]},
    {"text": "2. 长方形长8cm宽5cm", "pos": [10, 100, 350, 30]},
    {"text": "求它的面积。", "pos": [10, 135, 200, 28]},
    {"text": "3. 计算：36÷(4+2)×3=", "pos": [10, 200, 300, 30]},
]
results = cut_to_questions(blocks)
assert len(results) == 3, f"Expected 3 questions, got {len(results)}"
print(f"  {len(blocks)} blocks -> {len(results)} questions")
for q in results:
    print(f"    #{q['question_number']}: {q['question_text'][:60]}")
    print(f"       bbox: {[int(x) for x in q['bbox']]}")

# Test 3: No anchors (gap fallback)
print("\n=== Test 3: 无题号(gap fallback) ===")
blocks2 = [
    {"text": "第一段文字内容", "pos": [10, 10, 300, 25]},
    {"text": "续上一段", "pos": [10, 38, 200, 25]},
    {"text": "第二段文字内容", "pos": [10, 120, 300, 25]},  # gap > 40
    {"text": "第三段文字内容", "pos": [10, 200, 300, 25]},  # gap > 40
]
results2 = cut_to_questions(blocks2)
assert len(results2) == 3, f"Expected 3 groups, got {len(results2)}"
print(f"  {len(blocks2)} blocks -> {len(results2)} questions")
for q in results2:
    print(f"    #{q['question_number']}: {q['question_text'][:60]}")

# Test 4: Mixed Chinese numbers
print("\n=== Test 4: 中文大题 ===")
blocks3 = [
    {"text": "一、选择题（每题3分）", "pos": [10, 10, 300, 30]},
    {"text": "1. 下列哪个是质数？", "pos": [10, 45, 300, 28]},
    {"text": "二、填空题（每题4分）", "pos": [10, 120, 300, 30]},
    {"text": "1. 三角形内角和=___", "pos": [10, 155, 250, 28]},
]
results3 = cut_to_questions(blocks3)
assert len(results3) == 4, f"Expected 4 groups, got {len(results3)}"
print(f"  {len(blocks3)} blocks -> {len(results3)} questions")
for q in results3:
    print(f"    #{q['question_number']}: {q['question_text'][:60]}")

print("\nAll tests passed!")
