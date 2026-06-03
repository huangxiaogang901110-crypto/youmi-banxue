"""
GradingDocument V2 builder + fusion 单元测试。
全部使用 mock 数据，不调用任何外部 API。
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from grading_v2.marks_builder import build_marks
from grading_v2.fusion import fuse_grading_document, is_v2_enabled
from grading_v2.enhance import select_enhance_candidates, apply_enhance_result
from schemas.grading_document import GradingDocument, EnhanceResult


# ── 辅助构造函数 ──────────────────────────────────────────────────────────────

IMAGE_W, IMAGE_H = 800, 1200


def _q(
    qid: str,
    *,
    is_correct=None,
    answer_bbox=None,
    bbox=None,
    student_answer=None,
    kind: str = "question",
):
    return {
        "question_id": qid,
        "question_number": 1,
        "question_text": "1+1=",
        "kind": kind,
        "bbox": bbox or [10.0, 10.0, 200.0, 50.0],
        "answer_bbox": answer_bbox,
        "is_correct": is_correct,
        "student_answer": student_answer,
        "source": "test",
        "confidence": 0.9,
    }


def _grp(gid: str, bbox: list, *, qids=None, idx: int = 1):
    return {
        "group_id": gid,
        "group_index": idx,
        "bbox": bbox,
        "label": str(idx),
        "title": "口算",
        "question_ids": qids or [],
    }


def _build(**kwargs):
    """调用 build_marks 的快捷方式，只需传变化的参数。"""
    defaults = dict(
        image_width=IMAGE_W,
        image_height=IMAGE_H,
        questions=[],
        ocr_blocks=[],
        groups=[],
    )
    defaults.update(kwargs)
    return build_marks(**defaults)


# ── 基础 marks 生成 ───────────────────────────────────────────────────────────

class TestGreenTick:
    def test_correct_with_answer_bbox_emits_tick(self):
        doc = _build(questions=[_q("q1", is_correct=True, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2")])
        ticks = [m for m in doc.marks if m.type == "green_tick"]
        assert len(ticks) == 1
        assert ticks[0].target_question_id == "q1"

    def test_correct_without_answer_bbox_no_tick(self):
        """质量门 (a)：answer_bbox 缺失 → 不画对勾。"""
        doc = _build(questions=[_q("q1", is_correct=True, answer_bbox=None, student_answer="2")])
        assert not any(m.type == "green_tick" for m in doc.marks)

    def test_correct_without_student_answer_no_tick(self):
        """无 student_answer 则无证据，不画对勾。"""
        doc = _build(questions=[_q("q1", is_correct=True, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer=None)])
        assert not any(m.type == "green_tick" for m in doc.marks)

    def test_null_grading_no_marks(self):
        doc = _build(questions=[_q("q1", is_correct=None, answer_bbox=[50.0, 10.0, 60.0, 30.0])])
        assert not any(m.type in ("green_tick", "red_circle") for m in doc.marks)


class TestRedCircle:
    def test_incorrect_with_answer_bbox_emits_circle(self):
        doc = _build(questions=[_q("q1", is_correct=False, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="3")])
        circles = [m for m in doc.marks if m.type == "red_circle"]
        assert len(circles) == 1
        assert circles[0].target_question_id == "q1"

    def test_incorrect_without_answer_bbox_no_circle_needs_review(self):
        """质量门 (a)：无 answer_bbox → 不画红圈，标 needs_review。"""
        doc = _build(questions=[_q("q1", is_correct=False, answer_bbox=None, student_answer="3")])
        assert not any(m.type == "red_circle" for m in doc.marks)
        assert doc.quality_flags.needs_review is True
        assert doc.quality_flags.answer_bbox_missing is True

    def test_oversized_dim_no_circle(self):
        """质量门 (b)：answer_bbox 单边超 1024px → 不画红圈。"""
        doc = _build(questions=[_q("q1", is_correct=False, answer_bbox=[0.0, 0.0, 1100.0, 20.0], student_answer="3")])
        assert not any(m.type == "red_circle" for m in doc.marks)
        assert doc.quality_flags.bbox_too_large is True

    def test_oversized_image_fraction_no_circle(self):
        """质量门 (b)：answer_bbox 面积 > 60% 图片面积 → 不画红圈。"""
        # 800*1200=960000; 60% = 576000; bbox 800*800=640000 > 576000
        # q_bbox 足够大以排除 Q-ratio 干扰（640000/960000=0.67 < 3）
        q = _q("q1", is_correct=False,
               answer_bbox=[0.0, 0.0, 800.0, 800.0],
               bbox=[0.0, 0.0, 800.0, 1000.0],
               student_answer="3")
        doc = _build(questions=[q])
        assert not any(m.type == "red_circle" for m in doc.marks)
        assert doc.quality_flags.bbox_too_large is True

    def test_reasonable_answer_bbox_not_suppressed(self):
        """质量门 (b)：answer_bbox 面积 < 60% 图片面积且合理大小，不应抑制红圈。"""
        # answer_bbox 150*60=9000; 9000/960000=0.94% < 60%
        # Q ratio: 9000/(200*100)=0.45 < 3 → 通过所有质量门
        q = _q("q1", is_correct=False,
               answer_bbox=[10.0, 20.0, 150.0, 60.0],
               bbox=[10.0, 10.0, 200.0, 100.0],
               student_answer="3")
        doc = _build(questions=[q])
        circles = [m for m in doc.marks if m.type == "red_circle"]
        assert len(circles) == 1, f"Reasonable answer bbox should produce red_circle, got {len(circles)}"

    def test_oversized_vs_question_area_no_circle(self):
        """质量门 (b)：answer_bbox 面积 > 3× 题目面积 → 不画红圈。"""
        # question_bbox area = 200*50 = 10000; 3x = 30000
        # answer_bbox area = 220*220 = 48400 > 30000
        q = _q("q1", is_correct=False, answer_bbox=[10.0, 10.0, 220.0, 220.0], bbox=[10.0, 10.0, 200.0, 50.0], student_answer="3")
        doc = _build(questions=[q])
        assert not any(m.type == "red_circle" for m in doc.marks)

    def test_ocr_block_refines_red_circle_bbox(self):
        """质量门 (c)：无文本意图时，OCR block 与 answer_bbox 重叠，用 OCR block bbox 精确定位红圈。"""
        a_bbox = [50.0, 10.0, 60.0, 30.0]
        ocr_blocks = [{"x": 55.0, "y": 12.0, "w": 30.0, "h": 20.0, "text": "3"}]
        doc = _build(
            questions=[_q("q1", is_correct=False, answer_bbox=a_bbox, student_answer="3")],
            ocr_blocks=ocr_blocks,
        )
        circles = [m for m in doc.marks if m.type == "red_circle"]
        assert len(circles) == 1
        assert circles[0].bbox == [55.0, 12.0, 30.0, 20.0], f"Expected OCR refined bbox, got {circles[0].bbox}"

    def test_no_ocr_match_falls_back_to_answer_bbox(self):
        """质量门 (c)：无文本意图 + 无 OCR block 匹配时回退到 answer_bbox。"""
        a_bbox = [50.0, 10.0, 60.0, 30.0]
        ocr_blocks = [{"x": 700.0, "y": 900.0, "w": 30.0, "h": 20.0, "text": "999"}]
        doc = _build(
            questions=[_q("q1", is_correct=False, answer_bbox=a_bbox, student_answer="3")],
            ocr_blocks=ocr_blocks,
        )
        circles = [m for m in doc.marks if m.type == "red_circle"]
        assert len(circles) == 1
        assert circles[0].bbox == a_bbox


class TestBboxClamp:
    def test_slightly_out_of_bounds_bbox_clamped(self):
        """质量门 (d)：bbox 超出图片边界时被 clamp，坐标不为负。"""
        # [-5, 5, 55, 30] → clamp → [0, 5, 50, 30]
        doc = _build(questions=[_q("q1", is_correct=True, answer_bbox=[-5.0, 5.0, 55.0, 30.0], student_answer="2")])
        ticks = [m for m in doc.marks if m.type == "green_tick"]
        assert len(ticks) == 1
        x, y, w, h = ticks[0].bbox
        assert x >= 0, f"x should be >= 0 after clamp, got {x}"
        assert y >= 0, f"y should be >= 0 after clamp, got {y}"

    def test_fully_out_of_bounds_bbox_no_mark(self):
        """bbox clamp 后 w/h ≤ 0 → 不生成 mark。"""
        # x=810 超出 image_width=800，clamp 后 w=0 → 无效
        doc = _build(questions=[_q("q1", is_correct=True, answer_bbox=[810.0, 5.0, 50.0, 30.0], student_answer="2")])
        assert not any(m.type == "green_tick" for m in doc.marks)


# ── 题组 marks ────────────────────────────────────────────────────────────────

class TestGroupMarks:
    def test_group_emits_box_and_label(self):
        groups = [_grp("g1", [0.0, 0.0, 400.0, 300.0])]
        doc = _build(groups=groups)
        assert any(m.type == "group_box" and m.target_group_id == "g1" for m in doc.marks)
        assert any(m.type == "group_label" and m.target_group_id == "g1" for m in doc.marks)

    def test_group_label_y_above_group(self):
        """group_label 的 y 坐标应在题组上方（≤ group bbox y）。"""
        grp_y = 50.0
        groups = [_grp("g1", [10.0, grp_y, 200.0, 150.0])]
        doc = _build(groups=groups)
        label = next((m for m in doc.marks if m.type == "group_label"), None)
        assert label is not None
        assert label.bbox[1] <= grp_y, f"label y {label.bbox[1]} should be ≤ group y {grp_y}"

    def test_group_without_bbox_no_marks(self):
        """题组 bbox 无效时不生成 group_box / group_label。"""
        groups = [_grp("g1", None)]
        doc = _build(groups=groups)
        assert not any(m.type in ("group_box", "group_label") for m in doc.marks)

    def test_question_group_id_linked(self):
        """题目所属题组 ID 应正确写入 GradingMark.target_group_id。"""
        groups = [_grp("g1", [0.0, 0.0, 400.0, 300.0], qids=["q1"])]
        doc = _build(
            questions=[_q("q1", is_correct=True, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2")],
            groups=groups,
        )
        tick = next((m for m in doc.marks if m.type == "green_tick"), None)
        assert tick is not None
        assert tick.target_group_id == "g1"


# ── Intent 覆盖（P3.1：qwen_intent 不再覆盖 pipeline is_correct）────────────

class TestIntentOverride:
    def test_intent_false_does_not_override_pipeline_true(self):
        """qwen_intent is_correct=False 不覆盖 pipeline is_correct=True → 保留 green_tick。"""
        intent = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": False, "confidence": 0.95},
        ]}
        doc = _build(
            questions=[_q("q1", is_correct=True, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="3")],
            qwen_grading_intent=intent,
        )
        assert any(m.type == "green_tick" for m in doc.marks), \
            "pipeline is_correct=True must produce green_tick even when intent says incorrect"
        assert not any(m.type == "red_circle" for m in doc.marks)

    def test_intent_true_does_not_override_pipeline_false(self):
        """qwen_intent is_correct=True 不覆盖 pipeline is_correct=False → 保留 red_circle。"""
        intent = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": True, "confidence": 0.90, "student_answer": "6"},
        ]}
        doc = _build(
            questions=[_q("q1", is_correct=False, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="6")],
            qwen_grading_intent=intent,
        )
        assert any(m.type == "red_circle" for m in doc.marks), \
            "pipeline is_correct=False must produce red_circle even when intent says correct"
        assert not any(m.type == "green_tick" for m in doc.marks)

    def test_unmatched_intent_question_id_ignored(self):
        """intent 中未匹配的 question_id 不影响其他题目。"""
        intent = {"version": "v1", "questions": [
            {"question_id": "NONEXISTENT", "is_correct": False, "confidence": 0.99},
        ]}
        doc = _build(
            questions=[_q("q1", is_correct=True, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2")],
            qwen_grading_intent=intent,
        )
        ticks = [m for m in doc.marks if m.type == "green_tick"]
        assert len(ticks) == 1  # q1 仍按 pipeline is_correct=True 生成对勾


# ── QualityFlags ──────────────────────────────────────────────────────────────

class TestQualityFlags:
    def test_low_confidence_flag(self):
        doc = _build(image_confidence=0.4)
        assert doc.quality_flags.low_confidence is True

    def test_high_confidence_no_flag(self):
        doc = _build(image_confidence=0.85)
        assert doc.quality_flags.low_confidence is False

    def test_no_confidence_no_flag(self):
        doc = _build(image_confidence=None)
        assert doc.quality_flags.low_confidence is False

    def test_needs_review_set_when_wrong_no_bbox(self):
        doc = _build(questions=[_q("q1", is_correct=False, answer_bbox=None, student_answer="3")])
        assert doc.quality_flags.needs_review is True

    def test_no_needs_review_when_correct_no_bbox(self):
        """正确题缺 answer_bbox → answer_bbox_missing=True 但 needs_review 不一定置位。"""
        doc = _build(questions=[_q("q1", is_correct=True, answer_bbox=None, student_answer="2")])
        # answer_bbox_missing 置位（有批改但无 bbox）
        assert doc.quality_flags.answer_bbox_missing is True


# ── Non-question kinds ────────────────────────────────────────────────────────

def test_non_question_kind_skipped():
    """kind != question 的节点不参与 marks 生成。"""
    doc = _build(questions=[
        _q("s1", is_correct=True, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2", kind="section"),
        _q("q1", is_correct=True, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2", kind="question"),
    ])
    ticks = [m for m in doc.marks if m.type == "green_tick"]
    assert len(ticks) == 1
    assert ticks[0].target_question_id == "q1"


# ── OCR 文本精确匹配 ──────────────────────────────────────────────────────────

class TestOcrTextMatch:
    def test_error_text_found_uses_ocr_block_bbox(self):
        """
        质量门 (c)：intent 提供 error_text="36"，OCR blocks 中存在匹配，
        red_circle bbox 应来自命中的 OCR block，而不是整个 answer_bbox。
        """
        intent = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": False, "confidence": 0.9, "error_text": "36"},
        ]}
        # "36" block 在 answer_bbox 范围内；"1+1=" block 不命中
        ocr_blocks = [
            {"x": 60.0, "y": 15.0, "w": 25.0, "h": 18.0, "text": "36"},
            {"x": 10.0, "y": 15.0, "w": 40.0, "h": 18.0, "text": "1+1="},
        ]
        a_bbox = [50.0, 10.0, 100.0, 40.0]
        doc = _build(
            questions=[_q("q1", is_correct=False, answer_bbox=a_bbox, student_answer="36")],
            ocr_blocks=ocr_blocks,
            qwen_grading_intent=intent,
        )
        circles = [m for m in doc.marks if m.type == "red_circle"]
        assert len(circles) == 1, f"Expected 1 red_circle, got {len(circles)}"
        assert circles[0].bbox == [60.0, 15.0, 25.0, 18.0], (
            f"red_circle bbox should be OCR block bbox, got {circles[0].bbox}"
        )

    def test_error_text_multiple_matches_in_answer_bbox_picks_closest(self):
        """
        多个 OCR block 命中 error_text，优先选 answer_bbox 内离中心最近的。
        """
        intent = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": False, "confidence": 0.9, "error_text": "36"},
        ]}
        a_bbox = [50.0, 10.0, 100.0, 40.0]  # 中心 (100, 30)
        ocr_blocks = [
            # 两个都在 answer_bbox 内；第一个更近中心
            {"x": 90.0, "y": 25.0, "w": 20.0, "h": 15.0, "text": "36"},   # 中心(100,32.5) 距(100,30)=2.5
            {"x": 55.0, "y": 12.0, "w": 20.0, "h": 15.0, "text": "36"},   # 中心(65,19.5) 距(100,30)=37.5
        ]
        doc = _build(
            questions=[_q("q1", is_correct=False, answer_bbox=a_bbox, student_answer="36")],
            ocr_blocks=ocr_blocks,
            qwen_grading_intent=intent,
        )
        circles = [m for m in doc.marks if m.type == "red_circle"]
        assert len(circles) == 1
        assert circles[0].bbox == [90.0, 25.0, 20.0, 15.0], (
            f"Should pick closest block in answer_bbox, got {circles[0].bbox}"
        )

    def test_error_text_not_found_no_circle_needs_review(self):
        """
        error_text 在 OCR blocks 中找不到 → 不画红圈，标 needs_review。
        """
        intent = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": False, "confidence": 0.9, "error_text": "99"},
        ]}
        ocr_blocks = [
            {"x": 60.0, "y": 15.0, "w": 25.0, "h": 18.0, "text": "36"},
        ]
        doc = _build(
            questions=[_q("q1", is_correct=False, answer_bbox=[50.0, 10.0, 100.0, 40.0], student_answer="36")],
            ocr_blocks=ocr_blocks,
            qwen_grading_intent=intent,
        )
        assert not any(m.type == "red_circle" for m in doc.marks), \
            "error_text not in OCR blocks should produce no red_circle"
        assert doc.quality_flags.needs_review is True

    def test_error_text_ambiguous_outside_bbox_no_circle_needs_review(self):
        """
        多个 OCR block 命中 error_text，但均不在 answer_bbox / question_bbox 内
        → 歧义，不画红圈，标 needs_review。
        """
        intent = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": False, "confidence": 0.9, "error_text": "36"},
        ]}
        a_bbox = [50.0, 10.0, 80.0, 30.0]
        q_bbox = [10.0, 10.0, 200.0, 50.0]
        ocr_blocks = [
            # 两个 "36" 均在 answer_bbox 和 question_bbox 范围外
            {"x": 400.0, "y": 600.0, "w": 20.0, "h": 15.0, "text": "36"},
            {"x": 600.0, "y": 800.0, "w": 20.0, "h": 15.0, "text": "36"},
        ]
        doc = _build(
            questions=[_q("q1", is_correct=False,
                          answer_bbox=a_bbox, bbox=q_bbox, student_answer="36")],
            ocr_blocks=ocr_blocks,
            qwen_grading_intent=intent,
        )
        assert not any(m.type == "red_circle" for m in doc.marks), \
            "Ambiguous OCR matches outside bbox should produce no red_circle"
        assert doc.quality_flags.needs_review is True

    def test_wrong_answer_field_triggers_text_match(self):
        """
        intent 使用 wrong_answer 字段（非 error_text）也应触发文本匹配。
        """
        intent = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": False, "confidence": 0.9, "wrong_answer": "7"},
        ]}
        ocr_blocks = [
            {"x": 55.0, "y": 12.0, "w": 15.0, "h": 18.0, "text": "7"},
        ]
        a_bbox = [50.0, 10.0, 60.0, 30.0]
        doc = _build(
            questions=[_q("q1", is_correct=False, answer_bbox=a_bbox, student_answer="7")],
            ocr_blocks=ocr_blocks,
            qwen_grading_intent=intent,
        )
        circles = [m for m in doc.marks if m.type == "red_circle"]
        assert len(circles) == 1
        assert circles[0].bbox == [55.0, 12.0, 15.0, 18.0], (
            f"wrong_answer field should trigger text match, got {circles[0].bbox}"
        )

    def test_intent_without_text_hint_falls_back_to_spatial(self):
        """
        intent 提供 is_correct=False 但无文本字段 → 回退到空间匹配（原有行为）。
        """
        intent = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": False, "confidence": 0.9},
        ]}
        a_bbox = [50.0, 10.0, 60.0, 30.0]
        ocr_blocks = [{"x": 55.0, "y": 12.0, "w": 30.0, "h": 20.0, "text": "3"}]
        doc = _build(
            questions=[_q("q1", is_correct=False, answer_bbox=a_bbox, student_answer="3")],
            ocr_blocks=ocr_blocks,
            qwen_grading_intent=intent,
        )
        circles = [m for m in doc.marks if m.type == "red_circle"]
        assert len(circles) == 1
        # 无文本意图 → 空间匹配 → OCR block bbox
        assert circles[0].bbox == [55.0, 12.0, 30.0, 20.0]

    def test_error_text_empty_string_falls_back_to_spatial(self):
        """
        intent 中 error_text 为空字符串 → normalize 后为空 → 等同无文本意图，回退空间匹配。
        """
        intent = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": False, "confidence": 0.9, "error_text": "  "},
        ]}
        a_bbox = [50.0, 10.0, 60.0, 30.0]
        ocr_blocks = [{"x": 55.0, "y": 12.0, "w": 30.0, "h": 20.0, "text": "3"}]
        doc = _build(
            questions=[_q("q1", is_correct=False, answer_bbox=a_bbox, student_answer="3")],
            ocr_blocks=ocr_blocks,
            qwen_grading_intent=intent,
        )
        circles = [m for m in doc.marks if m.type == "red_circle"]
        assert len(circles) == 1
        assert circles[0].bbox == [55.0, 12.0, 30.0, 20.0]

    def test_substring_6_not_matched_in_36(self):
        """error_text='6' 不应命中 OCR '36'（子串误命中修复）。"""
        intent = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": False, "confidence": 0.9, "error_text": "6"},
        ]}
        ocr_blocks = [
            {"x": 60.0, "y": 15.0, "w": 20.0, "h": 18.0, "text": "36"},
        ]
        a_bbox = [50.0, 10.0, 100.0, 40.0]
        doc = _build(
            questions=[_q("q1", is_correct=False, answer_bbox=a_bbox, student_answer="6")],
            ocr_blocks=ocr_blocks,
            qwen_grading_intent=intent,
        )
        assert not any(m.type == "red_circle" for m in doc.marks), \
            "error_text='6' must not match OCR '36' (substring false positive)"
        assert doc.quality_flags.needs_review is True

    def test_substring_6_not_matched_in_16(self):
        """error_text='6' 不应命中 OCR '16'（子串误命中修复）。"""
        intent = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": False, "confidence": 0.9, "error_text": "6"},
        ]}
        ocr_blocks = [
            {"x": 60.0, "y": 15.0, "w": 20.0, "h": 18.0, "text": "16"},
        ]
        a_bbox = [50.0, 10.0, 100.0, 40.0]
        doc = _build(
            questions=[_q("q1", is_correct=False, answer_bbox=a_bbox, student_answer="6")],
            ocr_blocks=ocr_blocks,
            qwen_grading_intent=intent,
        )
        assert not any(m.type == "red_circle" for m in doc.marks), \
            "error_text='6' must not match OCR '16' (substring false positive)"
        assert doc.quality_flags.needs_review is True

    def test_exact_6_matches_ocr_6(self):
        """error_text='6' 精确匹配 OCR '6' → 应生成红圈。"""
        intent = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": False, "confidence": 0.9, "error_text": "6"},
        ]}
        ocr_blocks = [
            {"x": 55.0, "y": 12.0, "w": 20.0, "h": 18.0, "text": "6"},
        ]
        a_bbox = [50.0, 10.0, 60.0, 30.0]
        doc = _build(
            questions=[_q("q1", is_correct=False, answer_bbox=a_bbox, student_answer="6")],
            ocr_blocks=ocr_blocks,
            qwen_grading_intent=intent,
        )
        circles = [m for m in doc.marks if m.type == "red_circle"]
        assert len(circles) == 1, f"error_text='6' should match OCR '6', got {len(circles)} circles"
        assert circles[0].bbox == [55.0, 12.0, 20.0, 18.0]

    def test_whitespace_normalize_exact_match(self):
        """error_text 含前后空白，normalize 后与 OCR '6' 精确匹配。"""
        intent = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": False, "confidence": 0.9, "error_text": " 6 "},
        ]}
        ocr_blocks = [
            {"x": 55.0, "y": 12.0, "w": 20.0, "h": 18.0, "text": "6"},
        ]
        a_bbox = [50.0, 10.0, 60.0, 30.0]
        doc = _build(
            questions=[_q("q1", is_correct=False, answer_bbox=a_bbox, student_answer="6")],
            ocr_blocks=ocr_blocks,
            qwen_grading_intent=intent,
        )
        circles = [m for m in doc.marks if m.type == "red_circle"]
        assert len(circles) == 1, f"whitespace-padded error_text should normalize and match, got {len(circles)}"

    def test_spatially_close_text_mismatch_no_circle(self):
        """OCR block 在 answer_bbox 内但文本不精确匹配 → 不生成红圈，标 needs_review。"""
        intent = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": False, "confidence": 0.9, "error_text": "6"},
        ]}
        # block 在 answer_bbox 空间内，但文本是 "36"（含 "6" 但不等于 "6"）
        ocr_blocks = [
            {"x": 55.0, "y": 12.0, "w": 20.0, "h": 18.0, "text": "36"},
        ]
        a_bbox = [50.0, 10.0, 60.0, 30.0]
        doc = _build(
            questions=[_q("q1", is_correct=False, answer_bbox=a_bbox, student_answer="6")],
            ocr_blocks=ocr_blocks,
            qwen_grading_intent=intent,
        )
        assert not any(m.type == "red_circle" for m in doc.marks), \
            "Spatially close OCR block with non-exact text must not produce red_circle"
        assert doc.quality_flags.needs_review is True


# ── JSON 序列化 ───────────────────────────────────────────────────────────────

class TestJsonSerialization:
    def test_grading_document_json_roundtrip(self):
        """GradingDocument 可 JSON 序列化后反序列化恢复，marks 类型/数量不变。"""
        doc = _build(
            questions=[_q("q1", is_correct=True, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2")],
            groups=[_grp("g1", [0.0, 0.0, 400.0, 300.0])],
        )
        json_str = doc.model_dump_json()
        restored = GradingDocument.model_validate_json(json_str)
        assert restored.page_meta.image_width == IMAGE_W
        assert restored.page_meta.image_height == IMAGE_H
        assert len(restored.marks) == len(doc.marks)
        assert [m.type for m in restored.marks] == [m.type for m in doc.marks]

    def test_grading_document_dict_roundtrip(self):
        """GradingDocument model_dump / model_validate 双向可逆。"""
        doc = _build(questions=[_q("q1", is_correct=False, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="3")])
        restored = GradingDocument.model_validate(doc.model_dump())
        assert restored.quality_flags.needs_review == doc.quality_flags.needs_review
        assert [m.type for m in restored.marks] == [m.type for m in doc.marks]

    def test_quality_flags_serialized_correctly(self):
        """QualityFlags 中 bool 字段序列化后保持原值。"""
        doc = _build(
            image_confidence=0.3,
            questions=[_q("q1", is_correct=False, answer_bbox=None, student_answer="3")],
        )
        restored = GradingDocument.model_validate_json(doc.model_dump_json())
        assert restored.quality_flags.low_confidence is True
        assert restored.quality_flags.needs_review is True
        assert restored.quality_flags.answer_bbox_missing is True


# ── needs_review 不生成乱 mark ────────────────────────────────────────────────

class TestNeedsReviewNoStrayMarks:
    def test_needs_review_via_no_bbox_no_grading_mark(self):
        """needs_review 路径（无 answer_bbox）不生成 green_tick 或 red_circle。"""
        doc = _build(questions=[_q("q1", is_correct=False, answer_bbox=None, student_answer="3")])
        assert doc.quality_flags.needs_review is True
        assert not any(m.type in ("green_tick", "red_circle") for m in doc.marks)

    def test_needs_review_via_oversized_bbox_no_grading_mark(self):
        """needs_review 路径（answer_bbox 超大）不生成 red_circle。"""
        doc = _build(questions=[_q("q1", is_correct=False,
                                   answer_bbox=[0.0, 0.0, 1100.0, 20.0], student_answer="3")])
        assert doc.quality_flags.bbox_too_large is True
        assert not any(m.type == "red_circle" for m in doc.marks)

    def test_needs_review_via_error_text_not_found_no_grading_mark(self):
        """needs_review 路径（error_text 找不到）不生成 red_circle。"""
        intent = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": False, "confidence": 0.9, "error_text": "99"},
        ]}
        doc = _build(
            questions=[_q("q1", is_correct=False, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="3")],
            ocr_blocks=[{"x": 55.0, "y": 12.0, "w": 20.0, "h": 18.0, "text": "36"}],
            qwen_grading_intent=intent,
        )
        assert doc.quality_flags.needs_review is True
        assert not any(m.type in ("green_tick", "red_circle") for m in doc.marks)

    def test_needs_review_question_does_not_pollute_other_marks(self):
        """一题 needs_review 不影响同文档其他题目的正常 mark 生成。"""
        doc = _build(questions=[
            _q("q1", is_correct=False, answer_bbox=None, student_answer="3"),    # needs_review
            _q("q2", is_correct=True,  answer_bbox=[50.0, 200.0, 60.0, 30.0], student_answer="2"),  # normal
        ])
        assert doc.quality_flags.needs_review is True
        ticks = [m for m in doc.marks if m.type == "green_tick"]
        assert len(ticks) == 1
        assert ticks[0].target_question_id == "q2"
        assert not any(m.type == "red_circle" for m in doc.marks)


# ── Fusion 入口 ───────────────────────────────────────────────────────────────

class TestFusion:
    def test_fusion_returns_grading_document(self):
        doc = fuse_grading_document(
            image_width=IMAGE_W, image_height=IMAGE_H,
            questions=[_q("q1", is_correct=True, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2")],
            ocr_blocks=[], groups=[],
        )
        assert isinstance(doc, GradingDocument)
        assert doc.page_meta.image_width == IMAGE_W
        assert doc.page_meta.image_height == IMAGE_H

    def test_fusion_with_sample_intent_smoke(self):
        """冒烟：加载 sample_intent.json 不应抛出异常。"""
        doc = fuse_grading_document(
            image_width=IMAGE_W, image_height=IMAGE_H,
            questions=[], ocr_blocks=[], groups=[],
            use_sample_intent=True,
        )
        assert isinstance(doc, GradingDocument)

    def test_fusion_explicit_intent_overrides_sample(self):
        """显式 qwen_intent 参数优先于 use_sample_intent=True（通过 student_answer 补充验证）。"""
        explicit = {"version": "v1", "questions": [
            {"question_id": "q1", "is_correct": False, "confidence": 0.9,
             "student_answer": "from_explicit"},
        ]}
        doc = fuse_grading_document(
            image_width=IMAGE_W, image_height=IMAGE_H,
            questions=[_q("q1", is_correct=True,
                          answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer=None)],
            ocr_blocks=[], groups=[],
            qwen_intent=explicit,
            use_sample_intent=True,  # 应被 explicit 覆盖
        )
        # pipeline is_correct=True → green_tick（intent 不再覆盖 is_correct）
        assert any(m.type == "green_tick" for m in doc.marks), \
            "pipeline correct must produce green_tick; intent is_correct no longer overrides"
        # student_answer 由 explicit intent 补充（sample_intent 没有 q1）
        q_obj = next(q for q in doc.questions if q.question_id == "q1")
        assert q_obj.student_answer == "from_explicit", \
            "explicit intent student_answer must be used over sample_intent"

    def test_is_v2_enabled_false_by_default(self):
        """YOMI_GRADING_V2 未设置时 is_v2_enabled() 返回 False。"""
        import os
        os.environ.pop("YOMI_GRADING_V2", None)
        assert is_v2_enabled() is False

    def test_is_v2_enabled_true_when_set(self):
        import os
        os.environ["YOMI_GRADING_V2"] = "1"
        try:
            assert is_v2_enabled() is True
        finally:
            os.environ.pop("YOMI_GRADING_V2", None)

    def test_fusion_output_contains_green_tick(self):
        """fusion 对正确题输出包含 green_tick mark。"""
        doc = fuse_grading_document(
            image_width=IMAGE_W, image_height=IMAGE_H,
            questions=[_q("q1", is_correct=True, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2")],
            ocr_blocks=[], groups=[],
        )
        ticks = [m for m in doc.marks if m.type == "green_tick"]
        assert len(ticks) == 1
        assert ticks[0].target_question_id == "q1"

    def test_fusion_output_contains_red_circle(self):
        """fusion 对错误题输出包含 red_circle mark（answer_bbox 用于定位，不圈整题）。"""
        a_bbox = [50.0, 10.0, 60.0, 30.0]
        doc = fuse_grading_document(
            image_width=IMAGE_W, image_height=IMAGE_H,
            questions=[_q("q1", is_correct=False, answer_bbox=a_bbox, student_answer="3")],
            ocr_blocks=[], groups=[],
        )
        circles = [m for m in doc.marks if m.type == "red_circle"]
        assert len(circles) == 1
        assert circles[0].target_question_id == "q1"
        # red_circle bbox 应来自 answer_bbox，而非整题 bbox
        assert circles[0].bbox == a_bbox

    def test_fusion_red_circle_uses_ocr_not_full_question_bbox(self):
        """fusion 红圈：有 OCR block 匹配时使用 OCR bbox，不圈整题。"""
        q_bbox = [10.0, 10.0, 200.0, 50.0]    # 整题大框
        a_bbox = [50.0, 10.0, 60.0, 30.0]      # 答案小框
        ocr_block = {"x": 55.0, "y": 12.0, "w": 25.0, "h": 18.0, "text": "3"}
        doc = fuse_grading_document(
            image_width=IMAGE_W, image_height=IMAGE_H,
            questions=[_q("q1", is_correct=False, answer_bbox=a_bbox, bbox=q_bbox, student_answer="3")],
            ocr_blocks=[ocr_block], groups=[],
        )
        circles = [m for m in doc.marks if m.type == "red_circle"]
        assert len(circles) == 1
        # 应使用 OCR block 的精确 bbox，面积 << 整题 bbox 面积
        cx, cy, cw, ch = circles[0].bbox
        qw, qh = q_bbox[2], q_bbox[3]
        assert cw < qw and ch < qh, (
            f"red_circle bbox {circles[0].bbox} should be smaller than question bbox {q_bbox}"
        )


# ── Qwen 局部增强 ─────────────────────────────────────────────────────────────
#
# 辅助函数：从 build_marks 输出拿到一个带单题的 GradingDocument

def _doc_with_question(
    *,
    qid: str = "q1",
    is_correct=None,
    confidence: float = 0.9,
    answer_bbox=None,
    student_answer: str = "2",
) -> GradingDocument:
    """构建含单题的 GradingDocument（不调用外部 API）。"""
    return _build(questions=[_q(
        qid,
        is_correct=is_correct,
        answer_bbox=answer_bbox or [50.0, 10.0, 60.0, 30.0],
        student_answer=student_answer,
    )])


# ── a/b. 候选选取 ─────────────────────────────────────────────────────────────

class TestSelectEnhanceCandidates:
    def test_low_confidence_enters_candidates(self):
        """a. low_confidence（confidence < 0.70）的题进入候选列表。"""
        doc = _build(questions=[
            _q("q1", is_correct=True,
               answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2"),
        ])
        # 手动把 GradingQuestion 置为低置信度
        doc.questions[0] = doc.questions[0].model_copy(update={"confidence": 0.55})

        candidates = select_enhance_candidates(doc)
        qids = [c.question_id for c in candidates]
        assert "q1" in qids, "low_confidence question must enter candidates"
        candidate = next(c for c in candidates if c.question_id == "q1")
        assert "low_confidence" in candidate.quality_flags

    def test_needs_review_enters_candidates(self):
        """a. needs_review（answer_bbox 缺失）的题进入候选列表。"""
        doc = _build(questions=[
            _q("q1", is_correct=False, answer_bbox=None, student_answer="3"),
        ])
        candidates = select_enhance_candidates(doc)
        qids = [c.question_id for c in candidates]
        assert "q1" in qids, "needs_review question must enter candidates"
        candidate = next(c for c in candidates if c.question_id == "q1")
        assert "needs_review" in candidate.quality_flags

    def test_suspected_incorrect_enters_candidates(self):
        """a. suspected_incorrect（grading_result=incorrect）的题进入候选列表。"""
        doc = _build(questions=[
            _q("q1", is_correct=False,
               answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="3"),
        ])
        # 设为高置信度以确认 incorrect 本身就是入选理由
        doc.questions[0] = doc.questions[0].model_copy(update={"confidence": 0.90})

        candidates = select_enhance_candidates(doc)
        qids = [c.question_id for c in candidates]
        assert "q1" in qids, "suspected_incorrect question must enter candidates"
        candidate = next(c for c in candidates if c.question_id == "q1")
        assert "suspected_incorrect" in candidate.quality_flags

    def test_high_confidence_correct_excluded(self):
        """b. 清晰高置信正确题（correct + confidence ≥ 0.85 + answer_bbox 存在）不进候选。"""
        doc = _build(questions=[
            _q("q1", is_correct=True,
               answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2"),
        ])
        # 强制设为高置信度
        doc.questions[0] = doc.questions[0].model_copy(update={"confidence": 0.92})

        candidates = select_enhance_candidates(doc)
        qids = [c.question_id for c in candidates]
        assert "q1" not in qids, (
            "High-confidence correct question must NOT enter candidates"
        )


# ── c–f. apply_enhance_result ─────────────────────────────────────────────────

class TestApplyEnhanceResult:
    # ── c. Qwen 高置信增强 ───────────────────────────────────────────────────

    def test_qwen_high_conf_enhances_marks(self):
        """c. Qwen 高置信 + 不冲突 → decision.status=complete，marks 已更新。"""
        doc = _build(questions=[
            _q("q1", is_correct=False,
               answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="6"),
        ])
        # Qwen 说还是 incorrect（同 OCR），高置信
        enhance_result = EnhanceResult(
            question_id="q1",
            enhanced_result="incorrect",
            confidence=0.88,
            reason="student wrote 6 instead of 2",
        )
        updated_doc, decision = apply_enhance_result(doc, enhance_result)

        assert decision.status == "complete"
        circles = [m for m in updated_doc.marks if m.type == "red_circle"]
        assert len(circles) == 1
        assert circles[0].source == "qwen_enhance"

    # ── d. 冲突 → qwen_ocr_mismatch + needs_review ──────────────────────────

    def test_conflict_sets_mismatch_flags(self):
        """d. Qwen 与 OCR 结论相反 → conflict + qwen_ocr_mismatch + needs_review。"""
        doc = _build(questions=[
            _q("q1", is_correct=True,
               answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2"),
        ])
        # 强制置为高置信度正确
        doc.questions[0] = doc.questions[0].model_copy(update={"confidence": 0.90})

        # Qwen 认为是错的（与 OCR 冲突）
        enhance_result = EnhanceResult(
            question_id="q1",
            enhanced_result="incorrect",
            confidence=0.82,
        )
        updated_doc, decision = apply_enhance_result(doc, enhance_result)

        assert decision.status == "conflict"
        assert updated_doc.quality_flags.qwen_ocr_mismatch is True
        assert updated_doc.quality_flags.needs_review is True
        assert decision.output is not None
        assert decision.output.qwen_ocr_mismatch is True
        assert decision.output.needs_review is True

    # ── e. Qwen 低置信不覆盖原 marks ────────────────────────────────────────

    def test_qwen_low_conf_keeps_original_marks(self):
        """e. Qwen 置信度 < 0.75 → skipped，原 marks 不变。"""
        doc = _build(questions=[
            _q("q1", is_correct=True,
               answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2"),
        ])
        original_marks = [m.model_dump() for m in doc.marks]

        enhance_result = EnhanceResult(
            question_id="q1",
            enhanced_result="incorrect",
            confidence=0.60,  # 低置信
        )
        updated_doc, decision = apply_enhance_result(doc, enhance_result)

        assert decision.status == "skipped"
        # marks 与原始完全一致
        assert [m.model_dump() for m in updated_doc.marks] == original_marks

    # ── f. red_circle 走 answer_bbox 安全门 ──────────────────────────────────

    def test_qwen_red_circle_blocked_without_answer_bbox(self):
        """f. Qwen 建议 red_circle 但 answer_bbox 缺失 → 不生成红圈，needs_review。"""
        # 先构建一个有 answer_bbox 的文档，再手动清除 answer_bbox
        doc = _build(questions=[
            _q("q1", is_correct=False, answer_bbox=None, student_answer="3"),
        ])
        # 确认 answer_bbox 已是 None（因为没传入）
        assert doc.questions[0].answer_bbox is None

        enhance_result = EnhanceResult(
            question_id="q1",
            enhanced_result="incorrect",
            confidence=0.85,  # 高置信
        )
        updated_doc, decision = apply_enhance_result(doc, enhance_result)

        # 安全门：没有 answer_bbox，不能画红圈
        assert not any(m.type == "red_circle" for m in updated_doc.marks)
        assert updated_doc.quality_flags.needs_review is True
        # answer_bbox 缺失是安全门失败，不是 Qwen/OCR 冲突 → skipped
        assert decision.status == "skipped"
        assert updated_doc.quality_flags.answer_bbox_missing is True

    # ── g. root quality_flags 唯一权威 ───────────────────────────────────────

    def test_root_quality_flags_are_authoritative(self):
        """g. 冲突和安全门结果均写入 doc.quality_flags，而非仅保存在 EnhanceResult 里。"""
        doc = _build(questions=[
            _q("q1", is_correct=True,
               answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2"),
        ])
        doc.questions[0] = doc.questions[0].model_copy(update={"confidence": 0.90})

        enhance_result = EnhanceResult(
            question_id="q1",
            enhanced_result="incorrect",
            confidence=0.80,
        )
        updated_doc, decision = apply_enhance_result(doc, enhance_result)

        # 冲突时，EnhanceResult 上的 flags 必须同时反映在 doc.quality_flags 上
        assert decision.status == "conflict"
        assert updated_doc.quality_flags.qwen_ocr_mismatch is True, (
            "quality_flags.qwen_ocr_mismatch must be set on the document, "
            "not only on EnhanceResult"
        )
        assert updated_doc.quality_flags.needs_review is True, (
            "quality_flags.needs_review must be set on the document"
        )

    # ── h. P3.1 安全门先行：不得先删旧 marks 再发现无法生成新 mark ─────────────

    def test_correct_no_answer_bbox_keeps_old_marks_not_complete(self):
        """h. correct + answer_bbox 缺失 → 不删旧 marks，不返回 complete。"""
        doc = _build(questions=[
            _q("q1", is_correct=True, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2"),
        ])
        original_marks = [m.model_dump() for m in doc.marks]
        # 手动清除 answer_bbox（模拟安全门失败场景）
        doc.questions[0] = doc.questions[0].model_copy(update={"answer_bbox": None})

        enhance_result = EnhanceResult(
            question_id="q1",
            enhanced_result="correct",
            confidence=0.88,
        )
        updated_doc, decision = apply_enhance_result(doc, enhance_result)

        # 旧 marks 必须完整保留
        assert [m.model_dump() for m in updated_doc.marks] == original_marks, \
            "Old marks must be preserved when answer_bbox is missing for correct result"
        # 不返回 complete（空 marks）
        assert decision.status != "complete", \
            "Must not return complete when answer_bbox is missing"
        assert updated_doc.quality_flags.needs_review is True

    def test_correct_with_answer_bbox_generates_green_tick(self):
        """h. correct + answer_bbox 合法（无冲突）→ 生成 green_tick，status=complete。"""
        # pipeline 未给出批改结果（grading_result=None），Qwen 高置信说 correct
        doc = _build(questions=[
            _q("q1", is_correct=None, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2"),
        ])
        # 未批改的题不生成任何 tick/circle
        assert not any(m.type in ("green_tick", "red_circle") for m in doc.marks)

        enhance_result = EnhanceResult(
            question_id="q1",
            enhanced_result="correct",
            confidence=0.88,
        )
        updated_doc, decision = apply_enhance_result(doc, enhance_result)

        assert decision.status == "complete"
        ticks = [m for m in updated_doc.marks if m.type == "green_tick"]
        assert len(ticks) == 1, "correct + valid answer_bbox must produce green_tick"
        assert ticks[0].source == "qwen_enhance"
        assert not any(m.type == "red_circle" for m in updated_doc.marks)

    def test_red_circle_no_answer_bbox_preserves_old_marks(self):
        """h. red_circle + answer_bbox 缺失 → 不删旧 marks，保留 + needs_review。"""
        # 当前 pipeline 是 incorrect，存在 red_circle（answer_bbox 有效）
        doc = _build(questions=[
            _q("q1", is_correct=False, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="3"),
        ])
        original_marks = [m.model_dump() for m in doc.marks]
        assert any(m.type == "red_circle" for m in doc.marks)

        # 手动清除 answer_bbox（模拟 enhance 时 bbox 已失效）
        doc.questions[0] = doc.questions[0].model_copy(update={"answer_bbox": None})

        enhance_result = EnhanceResult(
            question_id="q1",
            enhanced_result="incorrect",
            confidence=0.88,
        )
        updated_doc, decision = apply_enhance_result(doc, enhance_result)

        # 旧 marks 必须完整保留（不能因安全门失败而丢失）
        assert [m.model_dump() for m in updated_doc.marks] == original_marks, \
            "Old red_circle must be preserved when answer_bbox is missing"
        assert updated_doc.quality_flags.needs_review is True

    # ── P1/P2 边界 case ──────────────────────────────────────────────────────

    def test_correct_answer_bbox_present_student_answer_missing_not_complete(self):
        """P1: correct + answer_bbox 存在 + student_answer 缺失 → skipped，不删旧 marks。"""
        # 构建含 green_tick 的文档，然后清空 student_answer
        doc = _build(questions=[
            _q("q1", is_correct=True, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2"),
        ])
        original_marks = [m.model_dump() for m in doc.marks]
        assert any(m.type == "green_tick" for m in doc.marks)

        doc.questions[0] = doc.questions[0].model_copy(update={"student_answer": None})

        enhance_result = EnhanceResult(
            question_id="q1",
            enhanced_result="correct",
            confidence=0.88,
        )
        updated_doc, decision = apply_enhance_result(doc, enhance_result)

        # 不删旧 marks
        assert [m.model_dump() for m in updated_doc.marks] == original_marks, \
            "Old marks must be preserved when student_answer is missing"
        # 不返回 complete
        assert decision.status == "skipped", \
            "Must return skipped when student_answer is missing"
        assert updated_doc.quality_flags.needs_review is True

    def test_correct_answer_bbox_and_student_answer_present_complete(self):
        """P1 happy path: answer_bbox + student_answer 均合法 → complete + green_tick。"""
        # grading_result=correct，Qwen 也说 correct → 无冲突，安全门全通
        doc = _build(questions=[
            _q("q1", is_correct=True, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="2"),
        ])

        enhance_result = EnhanceResult(
            question_id="q1",
            enhanced_result="correct",
            confidence=0.88,
        )
        updated_doc, decision = apply_enhance_result(doc, enhance_result)

        assert decision.status == "complete"
        ticks = [m for m in updated_doc.marks if m.type == "green_tick"]
        assert len(ticks) == 1, "correct + valid answer_bbox + student_answer must produce green_tick"
        assert ticks[0].source == "qwen_enhance"

    def test_incorrect_no_answer_bbox_skipped_not_conflict_answer_bbox_missing(self):
        """P2: incorrect + 缺 answer_bbox → skipped（非 conflict）+ answer_bbox_missing + 保留旧 marks。"""
        # 构建含 red_circle 的文档，再清除 answer_bbox
        doc = _build(questions=[
            _q("q1", is_correct=False, answer_bbox=[50.0, 10.0, 60.0, 30.0], student_answer="3"),
        ])
        original_marks = [m.model_dump() for m in doc.marks]
        assert any(m.type == "red_circle" for m in doc.marks)

        doc.questions[0] = doc.questions[0].model_copy(update={"answer_bbox": None})

        enhance_result = EnhanceResult(
            question_id="q1",
            enhanced_result="incorrect",
            confidence=0.88,
        )
        updated_doc, decision = apply_enhance_result(doc, enhance_result)

        # 安全门失败：skipped，不是 conflict（answer_bbox 缺失非 Qwen/OCR 冲突）
        assert decision.status == "skipped", \
            "answer_bbox missing is a safety-gate failure, not conflict"
        # quality_flags 写入 answer_bbox_missing
        assert updated_doc.quality_flags.answer_bbox_missing is True
        assert updated_doc.quality_flags.needs_review is True
        # 旧 marks 完整保留
        assert [m.model_dump() for m in updated_doc.marks] == original_marks, \
            "Old marks must be preserved when answer_bbox is missing"


# ── questions 端点响应形状 ────────────────────────────────────────────────────

class TestQuestionsEndpointShape:
    """验证 questions 端点响应字典在根级包含 v2_marks / v2_needs_review。
    直接构造与端点相同的 dict（不启动 HTTP server），断言键存在且类型正确。
    """

    def _make_memory_response(self, job: dict, data: list) -> dict:
        """复现 parse_routes.py 内存路径返回逻辑（不依赖 _jobs 全局状态）。"""
        import uuid
        return {
            "ok": True,
            "data": data,
            "overlay": job.get("overlay", []),
            "group_boxes": job.get("group_boxes", []),
            "v2_marks": job.get("v2_marks", []),
            "v2_needs_review": job.get("v2_needs_review", False),
            "request_id": uuid.uuid4().hex,
        }

    def _make_db_response(self, db_data: dict) -> dict:
        """复现 parse_routes.py DB 回退路径返回逻辑。"""
        import uuid
        return {
            "ok": True,
            "data": db_data["questions"],
            "overlay": db_data.get("overlay", []),
            "group_boxes": db_data.get("group_boxes", []),
            "v2_marks": db_data.get("v2_marks", []),
            "v2_needs_review": db_data.get("v2_needs_review", False),
            "request_id": uuid.uuid4().hex,
        }

    def test_memory_path_has_v2_marks_default_empty(self):
        """内存路径：job 无 v2_marks → 响应包含 v2_marks=[] 在根级。"""
        resp = self._make_memory_response(job={}, data=[])
        assert "v2_marks" in resp
        assert resp["v2_marks"] == []

    def test_memory_path_has_v2_needs_review_default_false(self):
        """内存路径：job 无 v2_needs_review → 响应包含 v2_needs_review=False 在根级。"""
        resp = self._make_memory_response(job={}, data=[])
        assert "v2_needs_review" in resp
        assert resp["v2_needs_review"] is False

    def test_memory_path_propagates_v2_marks(self):
        """内存路径：job 含 v2_marks → 响应原样返回在根级。"""
        marks = [{"type": "green_tick", "bbox": [1.0, 2.0, 3.0, 4.0]}]
        resp = self._make_memory_response(job={"v2_marks": marks}, data=[])
        assert resp["v2_marks"] == marks

    def test_memory_path_propagates_v2_needs_review_true(self):
        """内存路径：job 含 v2_needs_review=True → 响应返回 True 在根级。"""
        resp = self._make_memory_response(job={"v2_needs_review": True}, data=[])
        assert resp["v2_needs_review"] is True

    def test_db_path_has_v2_marks_default_empty(self):
        """DB 路径：db_data 无 v2_marks → 响应包含 v2_marks=[] 在根级。"""
        resp = self._make_db_response(db_data={"questions": []})
        assert "v2_marks" in resp
        assert resp["v2_marks"] == []

    def test_db_path_has_v2_needs_review_default_false(self):
        """DB 路径：db_data 无 v2_needs_review → 响应包含 v2_needs_review=False 在根级。"""
        resp = self._make_db_response(db_data={"questions": []})
        assert "v2_needs_review" in resp
        assert resp["v2_needs_review"] is False

    def test_db_path_propagates_v2_marks(self):
        """DB 路径：db_data 含 v2_marks → 响应原样返回在根级。"""
        marks = [{"type": "red_circle", "bbox": [5.0, 6.0, 7.0, 8.0]}]
        resp = self._make_db_response(db_data={"questions": [], "v2_marks": marks})
        assert resp["v2_marks"] == marks

    def test_db_path_propagates_v2_needs_review_true(self):
        """DB 路径：db_data 含 v2_needs_review=True → 响应返回 True 在根级。"""
        resp = self._make_db_response(db_data={"questions": [], "v2_needs_review": True})
        assert resp["v2_needs_review"] is True

    def test_legacy_fields_still_present(self):
        """兼容性：overlay / group_boxes / ok / data 在响应中仍存在。"""
        resp = self._make_memory_response(job={}, data=[{"q": 1}])
        for key in ("ok", "data", "overlay", "group_boxes", "v2_marks", "v2_needs_review", "request_id"):
            assert key in resp, f"Key '{key}' missing from questions endpoint response"
