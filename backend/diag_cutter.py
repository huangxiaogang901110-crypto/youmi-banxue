"""
诊断脚本：测试 OCR+cutting 切题在各种边界条件下的表现
模拟真实作业 OCR 可能返回的 blocks 格式
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from question_cutter import cut_to_questions, cut_questions, _parse_question_number

def test_scenario(name, blocks, expected_min):
    """返回 (pass, actual_count, result)"""
    results = cut_to_questions(blocks)
    actual = len(results)
    passed = actual >= expected_min
    status = "✅" if passed else "❌"
    print(f"\n{status} {name}: {len(blocks)} blocks → {actual} questions (expected ≥{expected_min})")
    for q in results:
        print(f"    #{q['question_number']}: {q['question_text'][:80]}")
    return passed, actual, results

# ── Scenario 1: 典型作业（题号清晰）──
print("=" * 60)
print("SCENARIO 1: 典型作业，OCR 正常识别题号")
test_scenario("Standard", [
    {"text": "1. 计算：3+5=___", "pos": [10, 10, 300, 30]},
    {"text": "2. 填空：三角形有___条边", "pos": [10, 80, 300, 30]},
    {"text": "3. 判断：1+1=3是对的还是错的？", "pos": [10, 150, 300, 30]},
    {"text": "4. 选择题：最长的河是？", "pos": [10, 220, 300, 30]},
], 4)

# ── Scenario 2: OCR 块未按 y 排序（排序由 cut_questions 内部完成）──
print("\n" + "=" * 60)
print("SCENARIO 2: OCR 块乱序（内部排序应纠正）")
test_scenario("Unordered", [
    {"text": "3. 判断：1+1=3是对的还是错的？", "pos": [10, 150, 300, 30]},
    {"text": "1. 计算：3+5=___", "pos": [10, 10, 300, 30]},
    {"text": "4. 选择题：最长的河是？", "pos": [10, 220, 300, 30]},
    {"text": "2. 填空：三角形有___条边", "pos": [10, 80, 300, 30]},
], 4)

# ── Scenario 3: 只有 2 题（Qwen-VL < 5 阈值触发的典型场景）──
print("\n" + "=" * 60)
print("SCENARIO 3: 仅 2 题（Qwen-VL <5 回落场景）")
test_scenario("2 questions", [
    {"text": "1. 口算：25×4=___", "pos": [10, 10, 200, 30]},
    {"text": "2. 口算：100÷5=___", "pos": [10, 80, 200, 30]},
], 2)

# ── Scenario 4: OCR 没有识别出题号（gap fallback）──
print("\n" + "=" * 60)
print("SCENARIO 4: OCR 无题号（gap fallback）")
test_scenario("No anchors", [
    {"text": "计算下面各题", "pos": [10, 10, 200, 25]},
    {"text": "3+5等于多少？", "pos": [10, 80, 200, 25]},
    {"text": "小明有12个苹果", "pos": [10, 150, 200, 25]},
    {"text": "吃了3个还剩几个？", "pos": [10, 180, 200, 25]},
], 2)

# ── Scenario 5: gap 不足 40px 的密集排版 ──
print("\n" + "=" * 60)
print("SCENARIO 5: 密集排版，gap < 40px（无锚点时全部合并）")
test_scenario("Dense no anchors", [
    {"text": "第一段文字", "pos": [10, 10, 200, 20]},
    {"text": "第二段文字", "pos": [10, 33, 200, 20]},  # gap = 33-30 = 3
    {"text": "第三段文字", "pos": [10, 56, 200, 20]},  # gap = 56-53 = 3
    {"text": "第四段文字", "pos": [10, 79, 200, 20]},  # gap = 79-76 = 3
], 1)  # 全部合并为1题

# ── Scenario 6: 含干扰项（OCR 把非题号识别为题号）──
print("\n" + "=" * 60)
print("SCENARIO 6: OCR 误识别题号（数字开头的非题目文本）")
test_scenario("False anchors", [
    {"text": "1. 计算题", "pos": [10, 10, 200, 30]},
    {"text": "2个苹果和3个梨", "pos": [10, 80, 200, 30]},  # 误识别为题号
    {"text": "2. 填空题", "pos": [10, 150, 200, 30]},
], 2)  # 期望至少能切出题

# ── Scenario 7: 括号小题格式 ──
print("\n" + "=" * 60)
print("SCENARIO 7: 括号小题格式")
test_scenario("Parentheses", [
    {"text": "(1) 计算：3+5=___", "pos": [10, 10, 250, 30]},
    {"text": "(2) 填空：正方形有___条边", "pos": [10, 80, 250, 30]},
    {"text": "（3）选择：最长的河", "pos": [10, 150, 250, 30]},
    {"text": "(4) 计算：12×3=___", "pos": [10, 220, 250, 30]},
], 4)

# ── Scenario 8: 仅1个 block 含多题（OCR 把整页识别为1个块）──
print("\n" + "=" * 60)
print("SCENARIO 8: 单块含多题文字（OCR 合并了）")
test_scenario("Single merged block", [
    {"text": "1.计算 2.填空 3.判断 4.选择", "pos": [10, 10, 500, 30]},
], 1)

# ── Scenario 9: 空 blocks ──
print("\n" + "=" * 60)
print("SCENARIO 9: 空 blocks（OCR 失败）")
results = cut_to_questions([])
print(f"  Empty blocks → {len(results)} questions")
assert len(results) == 0, "Expected 0 questions for empty input"
print("  ✅ Correct")

# ── Scenario 10: OCR 返回的 blocks 坐标全为 0 ──
print("\n" + "=" * 60)
print("SCENARIO 10: OCR 坐标全为 0（所有题堆叠）")
test_scenario("Zero coords", [
    {"text": "1. 计算：3+5=___", "pos": [0, 0, 0, 0]},
    {"text": "2. 填空：三角形有___条边", "pos": [0, 0, 0, 0]},
    {"text": "3. 选择：最长的河是？", "pos": [0, 0, 0, 0]},
    {"text": "4. 判断：1+1=3？", "pos": [0, 0, 0, 0]},
], 4)  # 有锚点应按锚点分组

# ── Scenario 11: 含 section_title/大题标题 ──
print("\n" + "=" * 60)
print("SCENARIO 11: 含大题标题")
test_scenario("Section titles", [
    {"text": "一、口算题", "pos": [10, 10, 200, 30]},
    {"text": "1. 25×4=___", "pos": [10, 50, 150, 25]},
    {"text": "2. 100÷5=___", "pos": [10, 85, 150, 25]},
    {"text": "二、填空题", "pos": [10, 140, 200, 30]},
    {"text": "1. 三角形有___条边", "pos": [10, 180, 200, 25]},
    {"text": "2. 正方形有___个角", "pos": [10, 215, 200, 25]},
], 4)  # "一、"和"二、"被识别为题号锚点

print("\n" + "=" * 60)
print("ALL SCENARIOS COMPLETE")
