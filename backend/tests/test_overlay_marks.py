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
from pipeline import _enrich_questions_with_ocr_bbox
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


# ── Tests 11-14: dict question compatibility ──────────────────────────────────

def _make_dict_q(
    *,
    answer_bbox=None,
    is_correct=None,
    bbox=None,
    student_answer=None,
    question_id="q1",
):
    return {
        "question_id": question_id,
        "question_number": 1,
        "question_text": "1+1=",
        "kind": "question",
        "bbox": bbox,
        "answer_bbox": answer_bbox,
        "is_correct": is_correct,
        "student_answer": student_answer,
        "source": "test",
    }


class TestDictQuestionOverlay:
    def test_correct_mark_for_dict_question_no_bbox(self):
        """dict question + is_correct=True + student_answer set + no bbox → returns None (no position info)."""
        q = _make_dict_q(is_correct=True, student_answer="1017", bbox=None, answer_bbox=None)
        mark = build_overlay_mark(q)
        assert mark is None, "Expected None when dict question has no bbox/answer_bbox"

    def test_no_mark_for_dict_question_no_answer(self):
        """dict question + is_correct=True + student_answer="" → no mark."""
        q = _make_dict_q(is_correct=True, student_answer="")
        assert build_overlay_mark(q) is None

    def test_no_mark_for_dict_question_no_grading(self):
        """dict question + is_correct=None + student_answer set → no mark."""
        q = _make_dict_q(is_correct=None, student_answer="1017")
        assert build_overlay_mark(q) is None

    def test_dict_question_with_answer_bbox(self):
        """dict question + is_correct=True + answer_bbox → generates correct mark."""
        ab = [50.0, 50.0, 60.0, 20.0]
        qb = [10.0, 10.0, 200.0, 80.0]
        q = _make_dict_q(is_correct=True, student_answer="46", answer_bbox=ab, bbox=qb)
        mark = build_overlay_mark(q)
        assert mark is not None, "Expected a mark for dict question with answer_bbox"
        assert mark.mark_type == "correct"
        assert mark.mark_bbox == ab, f"Expected mark_bbox={ab}, got {mark.mark_bbox}"


# ── Tests 15-18: OCR block matching (_enrich_questions_with_ocr_bbox) ─────────

class TestOCRBlockMatching:
    def _q(self, question_text="550+467", student_answer="1017", answer_bbox=None):
        return {
            "question_id": "q1",
            "question_text": question_text,
            "student_answer": student_answer,
            "answer_bbox": answer_bbox,
            "is_correct": True,
        }

    def test_match_simple(self):
        """1 question + matching OCR blocks → answer_bbox filled."""
        ocr_blocks = [
            {"x": 266, "y": 200, "w": 26, "h": 94, "text": "550+467="},
            {"x": 326, "y": 217, "w": 35, "h": 64, "text": "1017"},
        ]
        questions = [self._q()]
        result = _enrich_questions_with_ocr_bbox(questions, ocr_blocks)
        assert result[0]["answer_bbox"] == [326, 217, 35, 64], (
            f"Expected [326, 217, 35, 64], got {result[0]['answer_bbox']}"
        )

    def test_match_no_ocr_match(self):
        """student_answer digits not in OCR blocks → answer_bbox stays None."""
        ocr_blocks = [
            {"x": 10, "y": 10, "w": 50, "h": 20, "text": "550+467="},
            {"x": 70, "y": 10, "w": 40, "h": 20, "text": "9999"},
        ]
        questions = [self._q(student_answer="1017")]
        result = _enrich_questions_with_ocr_bbox(questions, ocr_blocks)
        assert result[0]["answer_bbox"] is None

    def test_match_ocr_garbled(self):
        """OCR garbled '10l7' (digits: '107') → fuzzy matches answer '1017'.

        zip('107','1017') → ('1','1'),('0','0'),('7','1') → 2 common ≥ min(3,4)-1=2 ✓
        """
        ocr_blocks = [
            {"x": 100, "y": 50, "w": 60, "h": 25, "text": "550+467="},
            {"x": 170, "y": 52, "w": 38, "h": 22, "text": "10l7"},  # OCR misread: digits "107"
        ]
        questions = [self._q(student_answer="1017")]
        result = _enrich_questions_with_ocr_bbox(questions, ocr_blocks)
        assert result[0]["answer_bbox"] == [170, 52, 38, 22], (
            f"Expected [170, 52, 38, 22], got {result[0]['answer_bbox']}"
        )

    def test_match_multiple_questions(self):
        """3 questions with distinct answer blocks → all 3 get answer_bbox filled."""
        ocr_blocks = [
            {"x": 10,  "y": 10,  "w": 50, "h": 20, "text": "1+2="},
            {"x": 70,  "y": 12,  "w": 20, "h": 18, "text": "3"},
            {"x": 10,  "y": 50,  "w": 50, "h": 20, "text": "4+5="},
            {"x": 70,  "y": 52,  "w": 20, "h": 18, "text": "9"},
            {"x": 10,  "y": 90,  "w": 50, "h": 20, "text": "6+7="},
            {"x": 70,  "y": 92,  "w": 20, "h": 18, "text": "13"},
        ]
        questions = [
            {"question_id": "q1", "question_text": "1+2", "student_answer": "3",  "answer_bbox": None, "is_correct": True},
            {"question_id": "q2", "question_text": "4+5", "student_answer": "9",  "answer_bbox": None, "is_correct": True},
            {"question_id": "q3", "question_text": "6+7", "student_answer": "13", "answer_bbox": None, "is_correct": True},
        ]
        result = _enrich_questions_with_ocr_bbox(questions, ocr_blocks)
        assert result[0]["answer_bbox"] == [70, 12, 20, 18], f"q1: {result[0]['answer_bbox']}"
        assert result[1]["answer_bbox"] == [70, 52, 20, 18], f"q2: {result[1]['answer_bbox']}"
        assert result[2]["answer_bbox"] == [70, 92, 20, 18], f"q3: {result[2]['answer_bbox']}"


# ── P1-C-4 补测: ambiguity rejection, stem exclusion, oversized filter ───────

def test_repeated_answer_no_cross_borrow():
    """两题答案同为"5"，一题缺q_block → 不补answer_bbox（全局fallback歧义拒绝）"""
    ocr_blocks = [
        {"x": 10, "y": 10, "w": 50, "h": 20, "text": "2+3="},
        {"x": 70, "y": 12, "w": 20, "h": 18, "text": "5"},
        {"x": 10, "y": 50, "w": 50, "h": 20, "text": "1+4="},
        {"x": 70, "y": 52, "w": 20, "h": 18, "text": "5"},
    ]
    questions = [
        {"question_id": "q1", "question_text": "2+3", "student_answer": "5", "answer_bbox": None},
        {"question_id": "q2", "question_text": "1+4", "student_answer": "5", "answer_bbox": None},
    ]
    result = _enrich_questions_with_ocr_bbox(questions, ocr_blocks)
    # q2 has no unique match (2 blocks both match "5"), fallback should reject
    assert result[1]["answer_bbox"] is None, (
        f"Repeated answer '5' with no q_block should return None (ambiguity), got {result[1]['answer_bbox']}"
    )


def test_multiple_candidates_return_none():
    """多个OCR block fuzzy match同一student_answer → 返回None（歧义拒绝）"""
    ocr_blocks = [
        {"x": 10, "y": 10, "w": 50, "h": 20, "text": "12+34="},
        {"x": 70, "y": 10, "w": 30, "h": 20, "text": "46"},
        {"x": 120, "y": 10, "w": 30, "h": 20, "text": "46"},
    ]
    questions = [{"question_id": "q1", "question_text": "12+34", "student_answer": "46", "answer_bbox": None}]
    result = _enrich_questions_with_ocr_bbox(questions, ocr_blocks)
    assert result[0]["answer_bbox"] is None, (
        f"Multiple candidates should return None (ambiguity), got {result[0]['answer_bbox']}"
    )


def test_stem_digit_not_matched_as_answer():
    """题干区含数字块但答案在右侧 → 不把题干块当答案，正确匹配右侧答案块"""
    ocr_blocks = [
        {"x": 50, "y": 50, "w": 60, "h": 25, "text": "5+3="},   # stem block
        {"x": 150, "y": 55, "w": 20, "h": 20, "text": "8"},       # answer block (right side)
    ]
    questions = [{"question_id": "q1", "question_text": "5+3=", "student_answer": "8", "answer_bbox": None}]
    result = _enrich_questions_with_ocr_bbox(questions, ocr_blocks)
    assert result[0]["answer_bbox"] == [150, 55, 20, 20], (
        f"Expected answer '8' bbox at right, got {result[0]['answer_bbox']}"
    )


def test_oversized_bbox_not_drawn():
    """answer_bbox超大(>3x题目面积或>1024px) → overlay不绘制"""
    from schemas.recognition import RecognitionQuestionContract
    # Case 1: answer_bbox area > 3x question area
    q = RecognitionQuestionContract(
        question_id="q1", question_number=1, question_text="1+1=",
        bbox=[10, 10, 200, 80],  # area=16000
        answer_bbox=[10, 10, 220, 220],  # area=48400 > 3*16000=48000
        is_correct=False, student_answer="2", source="test",
    )
    assert build_overlay_mark(q) is None, "Oversized answer_bbox (>3x q area) should not draw"
    # Case 2: answer_bbox width > 1024
    q2 = RecognitionQuestionContract(
        question_id="q2", question_number=2, question_text="2+2=",
        bbox=[10, 10, 200, 80],
        answer_bbox=[0, 0, 1100, 20],  # width > 1024
        is_correct=False, student_answer="4", source="test",
    )
    assert build_overlay_mark(q2) is None, "Oversized answer_bbox (width > 1024) should not draw"
