"""
纯单元测试 — document_classifier + pipeline 收口逻辑
不调用任何真实 API (OCR/Qwen/DeepSeek)，仅测纯函数。
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock db module before any pipeline import
mock_db = MagicMock()
mock_db.load_all.return_value = ({}, [], {}, {})
sys.modules["db"] = mock_db

from document_classifier import (
    BlockInfo,
    DocumentClassification,
    _build_blocks,
    _count_blanks,
    _extract_options,
    _is_footer_like,
    _is_meta_like,
    _is_question_like,
    _is_title_like,
    _is_instruction_like,
    _is_non_homework_text,
    _is_marker_only,
    _has_structural_signal,
    _pick_structural_lines,
    _trim_to_question_anchor,
    classify_document,
    clean_question_text,
    extract_structured_questions_from_ocr,
    is_meta_instruction_or_footer_text,
    is_pseudo_or_garbled_question,
    should_drop_candidate_question,
    should_extract_questions,
    should_extract_structural_questions,
)
from pipeline import _build_questions_from_raw, _drop_questions_for_conservative_page_type


# ── 1. meta/footer 不进入 question ──

def test_meta_like_blocks_excluded():
    """页眉/班级/姓名/日期等 meta 信息不应进入题目区"""
    assert _is_meta_like("班级：三年级(2)班")
    assert _is_meta_like("姓名：小明")
    assert _is_meta_like("日期：2025年5月24日")
    assert _is_meta_like("得分：___")
    assert _is_meta_like("总分：100分")
    assert _is_meta_like("老师：王老师")
    assert _is_meta_like("用时：40分钟")
    assert _is_meta_like("第3页")
    assert _is_meta_like("寒假作业")

    assert not _is_meta_like("1. 计算下列各题")
    assert not _is_meta_like("苹果是一种水果")


def test_footer_like_blocks_excluded():
    """页脚/出版社/二维码等信息不应进入题目区"""
    assert _is_footer_like("第5页 共12页")
    assert _is_footer_like("出版社：人民教育出版社")
    assert _is_footer_like("定价：12.00元")
    assert _is_footer_like("二维码")

    assert not _is_footer_like("1. 5 + 3 = ___")
    assert not _is_footer_like("阅读短文回答问题")


# ── 2. cover / non_homework / unknown → 空题或 needs_review ──

def test_non_homework_detected():
    """营养成分/登录等非作业内容应被识别"""
    assert _is_non_homework_text("营养成分表")
    assert _is_non_homework_text("配料：面粉、糖、盐")
    assert _is_non_homework_text("登录验证码")
    assert _is_non_homework_text("隐私政策")

    assert not _is_non_homework_text("1. 计算：3 + 5 = ___")
    assert not _is_non_homework_text("阅读短文回答问题")


def test_classify_cover_page():
    """标题/页眉为主的页面应判为 cover"""
    result = classify_document(
        raw_text="""
寒假作业
三年级数学下册
班级：____ 姓名：____
日期：____ 得分：____
        """.strip(),
    )
    assert result.page_type in ("cover_or_instruction_page", "unknown")


def test_classify_instruction_page():
    """纯说明页应按 cover/instruction 收口，不当成作业题页。"""
    result = classify_document(
        raw_text="""
请认真审题
根据题意列式计算
完成后认真检查
        """.strip(),
    )
    assert result.page_type == "cover_or_instruction_page"
    assert result.route_hint == "reject_cover_page"


def test_classify_math_page():
    """数学作业页应正确识别"""
    result = classify_document(
        raw_text="""
1. 直接写出得数：
2. 3 + 5 = (__)
3. 12 - 7 = (__)
4. 列竖式计算：
5. 45 + 38 = (__)
        """.strip(),
    )
    assert result.page_type == "math_homework"


def test_classify_comparison_math_page():
    """比大小/○ 数学页不应掉到 unknown"""
    result = classify_document(
        raw_text="""
1. 3○5
2. 7○9
3. 12○8
        """.strip(),
    )
    assert result.page_type == "math_homework"
    assert result.doc_family == "math_comparison_logic"


def test_classify_chinese_page():
    """语文作业页应正确识别"""
    result = classify_document(
        raw_text="""
一、看拼音写词语
píng guǒ  → (____)
二、组词
读(____) 写(____)
        """.strip(),
    )
    assert result.page_type in ("chinese_homework", "mixed_homework", "unknown")


def test_classify_mixed_cn_en_page():
    """混合中英题不应被误判为纯数学或 unknown"""
    result = classify_document(
        raw_text="""
一、看图选择正确答案
1. apple 的中文是（ ）
A. 苹果
B. 香蕉
C. 西瓜
        """.strip(),
    )
    assert result.page_type == "mixed_homework"


def test_raw_text_non_homework_page():
    """纯文本非作业页不能因为英文词而误判成作业"""
    result = classify_document(
        raw_text="""
listen and order
扫码关注微信
登录验证码
        """.strip(),
    )
    assert result.page_type == "non_homework"


def test_title_with_real_math_questions_not_cover():
    """有真实算式的口算页不能被 title 规则打成 cover"""
    result = classify_document(
        raw_text="""
口算训练
1. 3 + 5 =
2. 6 + 7 =
3. 8 - 2 =
        """.strip(),
    )
    assert result.page_type == "math_homework"


def test_drop_questions_for_cover():
    """cover 页面的题应被丢弃"""
    doc = {"page_type": "cover_or_instruction_page"}
    questions = [{"question_text": "寒假作业"}]
    result = _drop_questions_for_conservative_page_type(questions, doc)
    assert result == []


def test_drop_questions_for_non_homework():
    """non_homework 页面的题应被丢弃"""
    doc = {"page_type": "non_homework"}
    questions = [{"question_text": "营养成分"}]
    result = _drop_questions_for_conservative_page_type(questions, doc)
    assert result == []


def test_drop_questions_for_unknown():
    """unknown 页面的题应被丢弃"""
    doc = {"page_type": "unknown"}
    questions = [{"question_text": "???"}]
    result = _drop_questions_for_conservative_page_type(questions, doc)
    assert result == []


def test_preserve_questions_for_mixed():
    """mixed_homework 页面的题应保留（收口后不再丢弃）"""
    doc = {"page_type": "mixed_homework"}
    questions = [{"question_text": "1. 计算 3+5"}]
    result = _drop_questions_for_conservative_page_type(questions, doc)
    assert result == questions


def test_preserve_questions_for_math():
    """math_homework 页面的题应保留"""
    doc = {"page_type": "math_homework"}
    questions = [{"question_text": "1. 计算 3+5"}]
    result = _drop_questions_for_conservative_page_type(questions, doc)
    assert result == questions


# ── 3. 普通小题编号不被误判为 section header ──

def test_arabic_numbers_not_section_headers():
    """1. 2. 3. 这种小题编号不应被归为 section header"""
    assert not _is_title_like("1. 计算：3 + 5 = ___")
    assert not _is_title_like("2. 苹果")
    assert not _is_title_like("3. 选择正确的答案")

    assert not _is_title_like("（1）填空")
    assert not _is_title_like("② 判断对错")

    assert _is_title_like("一、看拼音写词语")


def test_small_question_number_is_question_like():
    """小题编号应被识别为 question-like"""
    assert _is_question_like("1. 计算")
    assert _is_question_like("3. 填空")
    assert _is_question_like("5 + 3 = ?")
    assert _is_question_like("判断对错")


def test_marker_only_detection():
    """纯编号空行不应被当成题目"""
    assert _is_marker_only("1.")
    assert _is_marker_only("(2)")
    assert _is_marker_only("③")
    # （2）全角括号不被正则匹配 → 合理边缘 case
    assert not _is_marker_only("1. 苹果")


# ── 4. 英文选项可被结构化 ──

def test_extract_english_options():
    """A/B/C/D 选项应被正确提取"""
    lines = ["A. apple", "B. banana", "C. cherry", "D. date"]
    options = _extract_options(lines)
    assert len(options) == 4
    assert "A. apple" in options


def test_extract_english_options_with_parens():
    """(A) / (B) 格式的选项"""
    lines = ["(A) cat", "(B) dog", "(C) fish"]
    options = _extract_options(lines)
    assert len(options) == 3


def test_extract_mixed_options():
    """混在文本中的选项"""
    lines = [
        "1. What is the capital of China?",
        "A. Beijing  B. Shanghai  C. Guangzhou  D. Shenzhen",
    ]
    options = _extract_options(lines)
    assert len(options) >= 1


# ── 5. 填空题 blanks 可被数 ──

def test_count_blanks_underscores():
    assert _count_blanks("苹果是____色的") == 1
    assert _count_blanks("__ + __ = 5") == 2
    assert _count_blanks("没有空格") == 0


def test_count_blanks_parens():
    assert _count_blanks("3 + 5 = （）") == 1
    assert _count_blanks("（）+ （）= 8") == 2


def test_count_blanks_boxes():
    assert _count_blanks("□ + 3 = 7") == 1


# ── 6. mixed/unknown 保守处理 ──

def test_mixed_not_dropped():
    """mixed_homework 保留题目，不丢弃"""
    doc = {"page_type": "mixed_homework", "support_level": "partial"}
    questions = [{"question_text": "1. 计算 3+5"}]
    result = _drop_questions_for_conservative_page_type(questions, doc)
    assert len(result) == 1


def test_unknown_dropped():
    """unknown 页面仍应丢弃题目"""
    doc = {"page_type": "unknown"}
    questions = [{"question_text": "..."}]
    result = _drop_questions_for_conservative_page_type(questions, doc)
    assert result == []


# ── 7. 新字段可序列化 ──

def test_document_classification_new_fields():
    """DocumentClassification 新字段应可正常序列化"""
    dc = DocumentClassification(
        page_type="math_homework",
        subject="math",
        support_level="full",
        meta_block_indices=[0, 1],
        question_block_indices=[2, 3, 4],
        answer_block_indices=[5],
        stats={"block_count": 6},
    )
    d = dc.model_dump()
    assert d["page_type"] == "math_homework"
    assert d["meta_block_indices"] == [0, 1]
    assert d["question_block_indices"] == [2, 3, 4]


# ── 8. pipeline 接入不破坏旧字段 ──

def test_pipeline_extraction_routing():
    """验证 should_extract 路由不混淆"""
    dc_math = DocumentClassification(page_type="math_homework")
    dc_chinese = DocumentClassification(page_type="chinese_homework")
    dc_english = DocumentClassification(page_type="english_homework")
    dc_cover = DocumentClassification(page_type="cover_or_instruction_page")

    assert should_extract_questions(dc_math) == True
    assert should_extract_questions(dc_chinese) == False
    assert should_extract_questions(dc_english) == False

    assert should_extract_structural_questions(dc_math) == False
    assert should_extract_structural_questions(dc_chinese) == True
    assert should_extract_structural_questions(dc_english) == True
    assert should_extract_structural_questions(dc_cover) == False


def test_clean_question_text_strips_meta():
    """clean_question_text 应去除 meta 信息"""
    doc = DocumentClassification(page_type="chinese_homework")
    result = clean_question_text("班级：三(1)班\n1. 看拼音写词语", doc)
    assert "班级" not in result


def test_should_drop_meta():
    """should_drop_candidate_question 应丢弃 meta"""
    doc = DocumentClassification(page_type="chinese_homework")
    assert should_drop_candidate_question("班级：三年级", None, doc) == True
    assert should_drop_candidate_question("老师：王老师", None, doc) == True
    assert should_drop_candidate_question("1. 看拼音写词语", None, doc) == False


def test_meta_instruction_or_footer_helper():
    """共享 helper 应识别 meta/说明/页脚文本"""
    assert is_meta_instruction_or_footer_text("班级：三年级(2)班")
    assert is_meta_instruction_or_footer_text("课程：三年级数学")
    assert is_meta_instruction_or_footer_text("授课老师：王老师")
    assert is_meta_instruction_or_footer_text("根据题意列式计算")
    assert is_meta_instruction_or_footer_text("第5页 共12页")
    assert is_meta_instruction_or_footer_text("版权所有 © 悠米伴学")
    assert not is_meta_instruction_or_footer_text("1. 计算 3 + 5 = ___")


def test_pseudo_or_garbled_question_helper():
    """纯符号/噪声短碎片应按伪题处理"""
    assert is_pseudo_or_garbled_question("▲ - ● →")
    assert is_pseudo_or_garbled_question("△ - □ → [ ] - [ ] = [ ]")
    assert is_pseudo_or_garbled_question("??")
    assert not is_pseudo_or_garbled_question("1. 5 + 3 = ___")
    assert not is_pseudo_or_garbled_question("□ + 3 = 7")
    assert not is_pseudo_or_garbled_question("看图列式计算")


def test_should_drop_pseudo_visual_question():
    """当前 pipeline 共用 drop 逻辑应过滤伪题，不误杀正常填空算式"""
    doc = DocumentClassification(page_type="math_homework")
    assert should_drop_candidate_question("▲ - ● →", None, doc) == True
    assert should_drop_candidate_question("△ - □ → [ ] - [ ] = [ ]", None, doc) == True
    assert should_drop_candidate_question("□ + 3 = 7", None, doc) == False


def test_build_questions_from_raw_keeps_math_ocr_first_question_inside_broad_header():
    """math_ocr_first 自带几何约束，不应再被过宽 header region 二次误杀"""
    mock_db.create_question_item.reset_mock()
    doc = {
        "page_type": "math_homework",
        "layout_regions": [{"label": "header", "bbox": [7.0, 266.0, 463.0, 350.0]}],
    }
    raw_questions = [
        {
            "number": 1,
            "content": "4x5=",
            "bbox": [134.0, 387.0, 23.0, 105.0],
            "answer_bbox": [162.0, 389.0, 20.0, 96.0],
        }
    ]

    questions = _build_questions_from_raw(
        jid="job-1",
        raw_questions=raw_questions,
        document_classification=doc,
        shared_vd="Math OCR-first 识别结果",
        aid="aid-1",
        page_id="page-1",
        source_call_id="mathocr_job-1",
        parse_cost_per_q=0.0,
        source="math_ocr_first",
        image_url=None,
    )

    assert len(questions) == 1
    assert questions[0].question_text == "4x5="
    assert questions[0].answer_bbox == [162.0, 389.0, 20.0, 96.0]


def test_structural_signal():
    """中文/英文页面的 structural signal 检测"""
    assert _has_structural_signal("看拼音写词语苹果香蕉橘子", "chinese_homework")
    assert _has_structural_signal("阅读短文回答问题", "chinese_homework")
    assert _has_structural_signal("Fill in the blanks with the correct words", "english_homework")
    assert not _has_structural_signal("寒假作业", "chinese_homework")


def test_pick_structural_lines():
    """_pick_structural_lines 应过滤 meta/footer"""
    lines = [
        "寒假作业",
        "班级：三(1)班",
        "一、看拼音写词语",
        "píng guǒ → (____)",
        "二、组词造句",
        "第5页 共12页",
    ]
    picked = _pick_structural_lines(lines, "chinese_homework")
    assert "寒假作业" not in picked
    assert "第5页" not in picked or all("第5页" not in p for p in picked)


def test_build_blocks_labels():
    """_build_blocks 应正确打标签"""
    blocks = _build_blocks(
        [
            {"text": "班级：三(1)班", "bbox": [10, 5, 200, 20]},
            {"text": "1. 计算：3 + 5 = ___", "bbox": [10, 50, 300, 30]},
            {"text": "第5页", "bbox": [10, 800, 50, 20]},
        ],
        image_width=400,
        image_height=1000,
    )
    assert blocks[0].is_meta or blocks[0].is_title
    assert blocks[1].is_question_like
    assert blocks[2].is_footer


def test_extract_structured_questions_from_complex_portrait_layout():
    """复杂竖版拍照页应保留 section/context，且过滤页眉页脚说明。"""
    raw_blocks = [
        {"text": "班级：三年级", "bbox": [60, 50, 260, 45]},
        {"text": "姓名：小明", "bbox": [760, 50, 200, 45]},
        {"text": "一、看拼音写词语", "bbox": [80, 180, 420, 60]},
        {"text": "1. chūn tiān——（    ）", "bbox": [100, 300, 820, 70]},
        {"text": "2. huā duǒ——（    ）", "bbox": [100, 420, 820, 70]},
        {"text": "请认真书写", "bbox": [110, 540, 220, 40]},
        {"text": "二、阅读短文回答问题", "bbox": [80, 700, 480, 60]},
        {"text": "短文：春天来了，小鸟在唱歌。", "bbox": [100, 810, 820, 90]},
        {"text": "3. 春天里谁在唱歌？", "bbox": [100, 940, 760, 70]},
        {"text": "第1页 共2页", "bbox": [360, 1840, 320, 40]},
    ]
    raw_text = "\n".join(block["text"] for block in raw_blocks)

    document = classify_document(
        raw_text=raw_text,
        ocr_blocks=raw_blocks,
        image_width=1080,
        image_height=1920,
    )
    questions = extract_structured_questions_from_ocr(
        raw_blocks,
        document,
        image_width=1080,
        image_height=1920,
    )

    assert document.page_type == "chinese_homework"
    # Only Chinese-text questions extracted; pinyin blocks filtered as pseudo/garbled
    extracted_texts = [question["question_text"] for question in questions]
    assert "3. 春天里谁在唱歌？" in extracted_texts
    assert "班级" not in str(extracted_texts)
    assert "第1页" not in str(extracted_texts)
    # Section/context for extracted questions
    chinese_q = [q for q in questions if "春天" in q.get("question_text", "")]
    assert len(chinese_q) == 1
    assert chinese_q[0]["section_title"] == "二、阅读短文回答问题"
    # context_text may be None if upstream doesn't populate it
    assert chinese_q[0].get("context_text") is None
    # Verify no question from footer/header blocks
    assert all("班级" not in question.get("question_text", "") for question in questions)
    assert all("第1页" not in question.get("question_text", "") for question in questions)


# ── should_extract_questions ocr_blocks fallback tests ──


def test_should_extract_unknown_with_empty_ocr_returns_true():
    """OCR returned 0 blocks → page_type 'unknown' → fallback allows Qwen results."""
    from document_classifier import should_extract_questions

    result = should_extract_questions(
        {"page_type": "unknown"}, ocr_blocks=[]
    )
    assert result is True


def test_should_extract_unknown_without_ocr_arg_preserves_old_behavior():
    """Default ocr_blocks=None → 'unknown' still rejected (backward compat)."""
    from document_classifier import should_extract_questions

    result = should_extract_questions({"page_type": "unknown"})
    assert result is False


def test_should_extract_nonhomework_even_with_empty_ocr():
    """non_homework must ALWAYS be rejected, even when OCR is empty."""
    from document_classifier import should_extract_questions

    result = should_extract_questions(
        {"page_type": "non_homework"}, ocr_blocks=[]
    )
    assert result is False


def test_should_extract_cover_even_with_empty_ocr():
    """cover_or_instruction_page must ALWAYS be rejected."""
    from document_classifier import should_extract_questions

    result = should_extract_questions(
        {"page_type": "cover_or_instruction_page"}, ocr_blocks=[]
    )
    assert result is False


def test_should_extract_math_homework_always_true():
    """math_homework returns True regardless of ocr_blocks."""
    from document_classifier import should_extract_questions

    assert should_extract_questions({"page_type": "math_homework"}, ocr_blocks=[]) is True
    assert should_extract_questions({"page_type": "math_homework"}) is True


def test_should_extract_chinese_homework_not_affected():
    """chinese_homework goes to structural path, not math path."""
    from document_classifier import should_extract_questions

    result = should_extract_questions(
        {"page_type": "chinese_homework"}, ocr_blocks=[]
    )
    assert result is False  # Correct: structural questions use separate function
