"""
Tests for overlay mark safe generation strategy.

Covers:
  1. math_ocr_first._make_question keeps answer_bbox as raw candidate (not union with seed)
  2. is_correct=None  → no mark generated
  3. is_correct=True, no answer_bbox → small green tick at bottom-right of question.bbox
  4. is_correct=False, no answer_bbox → no mark generated (禁止估算红圈)
  5. is_correct=False, with answer_bbox → mark uses answer_bbox

All tests use pure mock/fake objects. No real OCR/Qwen/DeepSeek client is imported.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from math_ocr_first import _make_question
from schemas.recognition import build_overlay_mark, RecognitionQuestionContract


def _make_q(
    *,
    answer_bbox=None,
    is_correct=None,
    bbox=None,
    student_answer=None,
) -> RecognitionQuestionContract:
    return RecognitionQuestionContract(
        question_id="q1",
        question_number=1,
        question_text="1+1=",
        bbox=bbox or [10.0, 20.0, 200.0, 80.0],
        answer_bbox=answer_bbox,
        is_correct=is_correct,
        student_answer=student_answer,
        source="test",
    )


# ── Test 1: _make_question ────────────────────────────────────────────────────

def test_answer_bbox_not_union_with_seed():
    """answer_bbox in the question dict must be the raw candidate bbox, never union with seed."""
    seed = {
        "question_index": 0,
        "text": "12+34",
        "bbox": [10.0, 10.0, 100.0, 30.0],
        "student_answer": "46",
        "answer_source": "right_neighbor",
        "answer_score": 1.0,
    }
    # candidate answer bbox is to the right of and separate from the seed bbox
    answer_bbox = [120.0, 10.0, 40.0, 25.0]

    q = _make_question(seed, answer_bbox, False, 0.9, {})

    assert q["answer_bbox"] == answer_bbox, (
        f"answer_bbox must remain the raw candidate bbox, got {q['answer_bbox']}"
    )
    # question bbox (题目框) may be the union — it must span both seed and answer
    assert q["bbox"][0] <= seed["bbox"][0], "question bbox must cover seed x-start"
    assert q["bbox"][0] <= answer_bbox[0], "question bbox must cover answer x-start"


# ── Tests 2-5: build_overlay_mark (schemas/recognition.py) ───────────────────

def test_no_mark_for_null_grading():
    """is_correct=None must produce no overlay mark regardless of answer_bbox."""
    q_with = _make_q(is_correct=None, answer_bbox=[50.0, 50.0, 60.0, 20.0])
    q_without = _make_q(is_correct=None, answer_bbox=None)
    assert build_overlay_mark(q_with) is None
    assert build_overlay_mark(q_without) is None


def test_correct_mark_without_answer_bbox():
    """is_correct=True with no answer_bbox → small green tick anchored at bottom-right of question.bbox."""
    qbbox = [10.0, 20.0, 200.0, 80.0]  # right-bottom corner at (210, 100)
    q = _make_q(is_correct=True, bbox=qbbox, answer_bbox=None, student_answer="2")

    mark = build_overlay_mark(q)

    assert mark is not None, "Expected a mark for is_correct=True even without answer_bbox"
    assert mark.mark_type == "correct"
    mb = mark.mark_bbox
    assert mb is not None and len(mb) == 4, f"mark_bbox must be a 4-element list, got {mb}"
    # mark must be small (well under the full question bbox size)
    assert mb[2] <= 80 and mb[3] <= 80, f"mark must be small, got w={mb[2]} h={mb[3]}"
    # right edge of mark must be at or before the right edge of question.bbox
    assert mb[0] + mb[2] <= qbbox[0] + qbbox[2] + 1, (
        f"mark right edge {mb[0]+mb[2]} must be within question right edge {qbbox[0]+qbbox[2]}"
    )
    # bottom edge of mark must be at or before the bottom edge of question.bbox
    assert mb[1] + mb[3] <= qbbox[1] + qbbox[3] + 1, (
        f"mark bottom edge {mb[1]+mb[3]} must be within question bottom edge {qbbox[1]+qbbox[3]}"
    )


def test_no_incorrect_mark_without_answer_bbox():
    """is_correct=False with no answer_bbox → no mark (don't estimate red circle over stem area)."""
    q = _make_q(is_correct=False, answer_bbox=None)
    assert build_overlay_mark(q) is None


def test_incorrect_mark_uses_answer_bbox():
    """is_correct=False with a valid answer_bbox → mark uses that answer_bbox exactly."""
    ab = [50.0, 60.0, 80.0, 30.0]
    q = _make_q(is_correct=False, answer_bbox=ab, student_answer="3")

    mark = build_overlay_mark(q)

    assert mark is not None, "Expected a mark for is_correct=False with answer_bbox"
    assert mark.mark_type == "incorrect"
    assert mark.mark_bbox == ab, (
        f"mark_bbox should equal answer_bbox {ab}, got {mark.mark_bbox}"
    )


# ── Tests 6-10: answer evidence gate (student_answer required) ────────────────

def test_no_mark_when_no_student_answer_true():
    """No student_answer + is_correct=True + no answer_bbox → no mark."""
    q = _make_q(is_correct=True, answer_bbox=None, student_answer=None)
    assert build_overlay_mark(q) is None


def test_no_mark_when_no_student_answer_false():
    """No student_answer + is_correct=False + has answer_bbox → no mark."""
    q = _make_q(is_correct=False, answer_bbox=[50.0, 60.0, 80.0, 30.0], student_answer=None)
    assert build_overlay_mark(q) is None


def test_no_mark_when_no_student_answer_true_with_bbox():
    """No student_answer + is_correct=True + has answer_bbox → no mark."""
    q = _make_q(is_correct=True, answer_bbox=[50.0, 60.0, 80.0, 30.0], student_answer=None)
    assert build_overlay_mark(q) is None


def test_mark_when_has_student_answer_true_no_bbox():
    """Has student_answer + is_correct=True + no answer_bbox → 40×40 green tick at bottom-right."""
    qbbox = [10.0, 20.0, 200.0, 80.0]
    q = _make_q(is_correct=True, bbox=qbbox, answer_bbox=None, student_answer="2")

    mark = build_overlay_mark(q)

    assert mark is not None, "Expected a mark when student_answer is set and is_correct=True"
    assert mark.mark_type == "correct"
    mb = mark.mark_bbox
    assert mb is not None and len(mb) == 4
    assert mb[2] == 40.0 and mb[3] == 40.0, f"Expected 40×40 fallback tick, got {mb[2]}×{mb[3]}"


def test_mark_when_has_student_answer_false_with_bbox():
    """Has student_answer + is_correct=False + has answer_bbox → red circle on answer_bbox."""
    ab = [50.0, 60.0, 80.0, 30.0]
    q = _make_q(is_correct=False, answer_bbox=ab, student_answer="3")

    mark = build_overlay_mark(q)

    assert mark is not None, "Expected a mark when student_answer is set and is_correct=False with answer_bbox"
    assert mark.mark_type == "incorrect"
    assert mark.mark_bbox == ab, f"mark_bbox should equal answer_bbox {ab}, got {mark.mark_bbox}"
