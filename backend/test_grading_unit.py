from grading_unit import apply_grading_unit_metadata, build_grading_units, build_group_boxes
from math_grader import _try_math_rule_grading


def _make_oral_question(number: int, x: float, y: float) -> dict:
    return {
        "question_id": f"oral-{number}",
        "question_number": number,
        "question_text": f"{number % 9 + 1}+{number % 7 + 1}=",
        "bbox": [x, y, 42.0, 22.0],
        "answer_bbox": [x + 48.0, y, 22.0, 22.0],
        "answer_bbox_source": "handwritten_zone",
        "student_answer": None,
        "section_title": "口算",
        "section_index": 1,
        "layout_row_index": int((number - 1) / 4) + 1,
    }


def _make_vertical_question(number: int, x: float, y: float) -> dict:
    return {
        "question_id": f"vertical-{number}",
        "question_number": number,
        "question_text": f"{number + 20}-{number}+{number - 10}",
        "bbox": [x, y, 72.0, 24.0],
        "answer_bbox": [x + 70.0, y + 120.0, 28.0, 28.0],
        "answer_bbox_source": "vertical_result",
        "student_answer": str(number),
        "section_title": "竖式",
        "section_index": 2,
        "layout_row_index": number - 24,
    }


def test_math_grader_normalizes_common_ocr_digits():
    assert _try_math_rule_grading("34+46=", "8o")["is_correct"] is True
    assert _try_math_rule_grading("56+34-65", "2e")["is_correct"] is True


def test_build_grading_units_merges_oral_questions_to_target_range():
    questions = []
    number = 1
    for row in range(6):
        for col in range(4):
            questions.append(_make_oral_question(number, 100.0 + (col * 120.0), 380.0 + (row * 42.0)))
            number += 1
    for offset in range(10):
        questions.append(_make_vertical_question(number, 120.0 + ((offset % 4) * 140.0), 820.0 + ((offset // 4) * 120.0)))
        number += 1

    units = build_grading_units(questions, image_width=720, image_height=1280)
    apply_grading_unit_metadata(questions, units, [])
    group_boxes = build_group_boxes(units)

    assert len(questions) == 34
    assert 24 <= len(units) <= 28
    assert len(group_boxes) == len(units)
    assert sum(1 for unit in units if unit["unit_type"] == "vertical") == 10
    assert any(unit["unit_type"] == "oral_pair" for unit in units)
    paired = next(question for question in questions if question["grading_unit_type"] == "oral_pair")
    assert paired["grading_unit_id"]
    assert paired["grading_unit_bbox"]
