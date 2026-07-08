"""
Repair-2B 最小后端测试
覆盖四个案例：
1. 无题号密集口算（2列）拆成多题
2. header bbox 不误删含算式 block（_collect 豁免）
3. meta/header 纯文字仍可过滤（豁免不扩大）
4. 已有题号 marker 逻辑不回退
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Mock db before any pipeline import
mock_db = MagicMock()
mock_db.load_all.return_value = ({}, [], {}, {})
sys.modules.setdefault("db", mock_db)

import pytest

from question_cutter import cut_to_questions, _split_arithmetic_groups, _expand_multi_arith_block, QuestionGroup, OCRBlock
from document_classifier import should_drop_candidate_question, _build_structural_candidate_blocks


# ── 1. 无题号密集口算（2列）拆成多题 ──────────────────────────────────────

def test_dense_arithmetic_no_number_splits_to_multiple():
    """2列口算无题号时应拆成多个独立题目，而不是全部合并"""
    # 模拟2列3行，共6道口算题
    blocks = [
        {"text": "3+2=", "pos": [10, 10, 80, 25]},
        {"text": "5-1=", "pos": [200, 10, 80, 25]},
        {"text": "4+6=", "pos": [10, 45, 80, 25]},
        {"text": "9-3=", "pos": [200, 45, 80, 25]},
        {"text": "7+2=", "pos": [10, 80, 80, 25]},
        {"text": "8-4=", "pos": [200, 80, 80, 25]},
    ]
    results = cut_to_questions(blocks)
    assert len(results) >= 4, (
        f"期望至少4题（每道独立口算），实际得 {len(results)} 题: "
        f"{[r['question_text'] for r in results]}"
    )


def test_dense_arithmetic_2block_group_splits():
    """2个算术 block 的组也应被拆分（原阈值 ≥3 blocks 会漏判）"""
    blocks = [
        OCRBlock("4+5=", [10, 10, 80, 25]),
        OCRBlock("6-2=", [200, 10, 80, 25]),
    ]
    g = QuestionGroup(blocks, 1, "")
    result = _split_arithmetic_groups([g])
    assert len(result) == 2, f"2个算术块应拆为2题，得 {len(result)}"


def test_single_block_multi_arith_expanded():
    """单 block 内包含多个算式（OCR 合并情形）应展开为多个伪子块"""
    b = OCRBlock("3+2=  7-4=  9+1=", [10, 10, 300, 25])
    expanded = _expand_multi_arith_block(b)
    assert len(expanded) >= 2, f"多算式 block 应展开，得 {len(expanded)} 个子块"
    # 每个子块文本应含原算式的片段
    texts = [e.text for e in expanded]
    assert any("3+2" in t or "3" in t for t in texts)


# ── 2. header bbox 不误删含算式 block（_collect 豁免）─────────────────────

def _make_dc(header_bbox):
    """构造包含 header region 的 DocumentClassification dict"""
    return {
        "page_type": "math_homework",
        "doc_family": "math",
        "subject": "math",
        "meta_block_indices": [],
        "layout_regions": [
            {"label": "header", "bbox": header_bbox},
        ],
        "section_headers": [],
    }


def test_arithmetic_block_in_header_bbox_not_dropped():
    """含算式的 block 即使在 header bbox 内也应通过 _build_structural_candidate_blocks 保留"""
    # header bbox 覆盖整个上半页
    dc = _make_dc([0, 0, 800, 200])
    raw_blocks = [
        {"text": "3+5=", "pos": [10, 10, 80, 25]},    # 在 header bbox 内
        {"text": "7-2=", "pos": [10, 50, 80, 25]},    # 在 header bbox 内
        {"text": "9+1=", "pos": [10, 250, 80, 25]},   # header 外
    ]
    candidates = _build_structural_candidate_blocks(
        raw_blocks, dc, image_width=800, image_height=1000
    )
    texts = [c.text for c in candidates]
    assert "3+5=" in texts or "7-2=" in texts, (
        f"含算式的 header 内 block 不应被过滤，candidates: {texts}"
    )


def test_should_drop_arithmetic_in_header_not_dropped():
    """should_drop_candidate_question 对含算式的 bbox 应返回 False（即使在 header 内）"""
    dc = _make_dc([0, 0, 800, 200])
    result = should_drop_candidate_question(
        text="5+3=",
        bbox=[10, 10, 80, 25],
        document_classification=dc,
    )
    assert result is False, "含算式题不应被 should_drop_candidate_question 丢弃"


# ── 3. meta/header 纯文字仍可过滤（豁免不扩大）──────────────────────────

def test_pure_meta_text_in_header_still_dropped():
    """纯文字（无算式）的 header 内 block 应仍被 should_drop_candidate_question 过滤"""
    dc = _make_dc([0, 0, 800, 200])
    result = should_drop_candidate_question(
        text="班级：三年级(2)班",
        bbox=[10, 10, 200, 25],
        document_classification=dc,
    )
    assert result is True, "纯 meta 文字应被过滤"


def test_pure_instruction_text_in_header_still_dropped():
    """说明文字（无算式）在 header bbox 内应被过滤"""
    dc = _make_dc([0, 0, 800, 150])
    result = should_drop_candidate_question(
        text="请直接写出得数",
        bbox=[50, 20, 300, 25],
        document_classification=dc,
    )
    assert result is True, "纯说明文字应被过滤"


# ── 3b. OCR 轻微 y 抖动：gap 小正数，同行多列仍正确拆分 ─────────────────────

def test_same_row_with_y_jitter_splits_to_atomic():
    """
    两列算式因 OCR y 轻微抖动导致 gap = cur.y - prev.bottom 为小正数（+2 px），
    旧 gap<=0 条件此时失效；_y_overlap_ratio 含 50% min_h 容差，
    仍能判定同行并按 x 列间距拆成独立题目。

    布局（pos=[x,y,w,h]）：
      左列 y=10,h=25,bottom=35   右列 y=37,h=25 → gap=+2（jitter）
      左列 y=70,h=25,bottom=95   右列 y=97,h=25 → gap=+2（jitter）
    gap_threshold 默认 40；x_gap=110 > 2*40=80 → 应拆列。
    """
    blocks = [
        {"text": "3+2=", "pos": [10,  10, 80, 25]},   # left row1, bottom=35
        {"text": "5-1=", "pos": [200, 37, 80, 25]},   # right row1, gap=+2 (jitter)
        {"text": "4+6=", "pos": [10,  70, 80, 25]},   # left row2, bottom=95
        {"text": "9-3=", "pos": [200, 97, 80, 25]},   # right row2, gap=+2 (jitter)
    ]
    results = cut_to_questions(blocks)
    assert len(results) >= 3, (
        f"y 轻微抖动（gap=+2）下同行多列应拆成独立题目，"
        f"实际 {len(results)} 题: {[r['question_text'] for r in results]}"
    )


# ── 4. 已有题号 marker 逻辑不回退 ─────────────────────────────────────────

def test_numbered_questions_not_broken_by_arith_split():
    """有题号的算式题不应被 _split_arithmetic_groups 拆散"""
    blocks_data = [
        {"text": "1. 3+2=", "pos": [10, 10, 200, 25]},
        {"text": "续行文字", "pos": [10, 40, 200, 25]},
        {"text": "2. 7-4=", "pos": [10, 80, 200, 25]},
        {"text": "3. 9+1=", "pos": [10, 120, 200, 25]},
    ]
    results = cut_to_questions(blocks_data)
    # 题号路径：应有3道题（1、2、3），不会因 arith split 增加或减少
    assert len(results) == 3, (
        f"有题号时 cut_to_questions 应返回3题，实际 {len(results)}: "
        f"{[r['question_text'] for r in results]}"
    )
    assert results[0]["question_number"] == 1
    assert results[1]["question_number"] == 2
    assert results[2]["question_number"] == 3


def test_numbered_mixed_content_stable():
    """题号路径在有算式的情况下也应稳定返回正确数量"""
    blocks_data = [
        {"text": "一、口算", "pos": [10, 10, 300, 25]},
        {"text": "1. 4+6=", "pos": [10, 45, 200, 25]},
        {"text": "2. 8-3=", "pos": [10, 80, 200, 25]},
    ]
    results = cut_to_questions(blocks_data)
    assert len(results) == 3, f"应为3组(大题+2小题)，得 {len(results)}"
