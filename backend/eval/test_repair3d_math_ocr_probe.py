from __future__ import annotations

import pytest

from backend.eval.repair3d_math_ocr_probe import (
    answer_extractor,
    compute_metrics,
    layout_grouper,
    math_rule_grader,
    run_probe,
    sub_question_cutter,
)


def _block(
    block_id: str,
    text: str,
    x: int,
    y: int,
    w: int,
    h: int,
    confidence: float = 0.95,
    *,
    line_index: int = 0,
    block_index: int = 0,
) -> dict:
    return {
        "id": block_id,
        "text": text,
        "bbox": [x, y, w, h],
        "confidence": confidence,
        "line_index": line_index,
        "block_index": block_index,
    }


def _vertical_problem(
    number: int,
    y: int,
    top_line: str,
    bottom_line: str,
    *,
    answer: str | None,
    answer_confidence: float = 0.85,
    x: int = 60,
) -> list[dict]:
    blocks = [
        _block(f"q{number}_n", f"{number}.", x - 10, y, 30, 20, 0.96, line_index=0, block_index=0),
        _block(f"q{number}_t", top_line, x, y + 30, 40, 30, 0.95, line_index=1, block_index=1),
        _block(f"q{number}_b", bottom_line, x, y + 65, 40, 30, 0.95, line_index=2, block_index=2),
        _block(f"q{number}_s", "___", x, y + 100, 45, 15, 0.99, line_index=3, block_index=3),
    ]
    if answer is not None:
        blocks.append(
            _block(
                f"q{number}_a",
                answer,
                x + 55,
                y + 98,
                35,
                25,
                answer_confidence,
                line_index=3,
                block_index=4,
            )
        )
    return blocks


def _build_synthetic_vertical_math_blocks() -> list[dict]:
    blocks = [
        _block("h1", "班级：三年级", 40, 20, 120, 24, 0.98),
        _block("h2", "姓名：小米", 180, 20, 100, 24, 0.98),
    ]
    blocks.extend(_vertical_problem(1, 100, "35", "+27", answer="62"))
    blocks.extend(_vertical_problem(2, 270, "46", "+15", answer="61"))
    blocks.extend(_vertical_problem(3, 440, "84", "-29", answer="55"))
    blocks.extend(_vertical_problem(4, 610, "18", "+24", answer="41"))
    blocks.extend(_vertical_problem(5, 780, "90", "-38", answer="52"))
    blocks.extend(_vertical_problem(6, 950, "14", "+27", answer="40"))
    blocks.extend(_vertical_problem(7, 1120, "63", "-17", answer="46"))
    blocks.extend(_vertical_problem(8, 1290, "57", "+16", answer=None))
    blocks.append(_block("a1", "老师批注：第8题需复查", 50, 1465, 180, 24, 0.94))
    blocks.append(_block("f1", "第1页 共1页", 50, 1530, 120, 20, 0.97))
    return blocks


def test_synthetic_vertical_math_blocks():
    blocks = _build_synthetic_vertical_math_blocks()

    report = run_probe(blocks, "verticalmath01.png")

    assert report["total_ocr_blocks"] == len(blocks)
    assert report["blocks_unavailable"] is False
    assert report["usable_regions"] == 8
    assert report["question_regions"] == 8
    assert report["header_annotation_regions"] >= 2
    assert report["sub_questions_cut"] == 8
    assert report["gradable_questions"] == 7
    assert report["correct_count"] == 5
    assert report["incorrect_count"] == 2
    assert report["needs_review"] >= 1
    assert report["speedup_vs_qwen"] > 1.0
    assert report["verdict"] == "feasible"
    assert len(report["stage_outputs"]["sub_questions"]) == 8


def test_layout_grouper():
    blocks = [
        _block("h1", "班级：三年级", 40, 20, 120, 24),
        _block("q1", "9. 计算 8 + 7 =", 60, 140, 160, 28),
        _block("v1", "10.", 50, 280, 30, 20),
        _block("v2", "35", 60, 310, 40, 30),
        _block("v3", "+27", 60, 345, 40, 30),
        _block("v4", "___", 60, 380, 45, 15),
        _block("v5", "62", 115, 378, 35, 25),
        _block("a1", "老师批注：看清进位", 50, 470, 180, 24),
    ]

    region_types = [region["type"] for region in layout_grouper(blocks)]

    assert region_types == ["header", "question_block", "vertical_math", "annotation"]


def test_sub_question_cutter():
    region_blocks = []
    region_blocks.extend(_vertical_problem(1, 100, "35", "+27", answer="62"))
    region_blocks.extend(_vertical_problem(2, 280, "46", "+15", answer="61"))
    regions = [
        {
            "id": "region_1",
            "type": "question_block",
            "bbox": [50, 100, 120, 305],
            "blocks": region_blocks,
            "confidence": 0.94,
            "needs_review": False,
        }
    ]

    sub_questions = sub_question_cutter(regions)

    assert len(sub_questions) == 2
    assert [question["question_number"] for question in sub_questions] == [1, 2]


def test_answer_extraction():
    regions = [
        {
            "id": "region_1",
            "type": "vertical_math",
            "bbox": [50, 100, 120, 125],
            "blocks": _vertical_problem(1, 100, "35", "+27", answer="62"),
            "confidence": 0.94,
            "needs_review": False,
        }
    ]

    sub_questions = sub_question_cutter(regions)
    extracted = answer_extractor(sub_questions, regions[0]["blocks"])

    assert extracted[0]["student_answer"] == "62"
    assert extracted[0]["question_text"] == "1. 35 +27 = ?"


def test_math_rule_grader():
    graded = math_rule_grader(
        [
            {
                "id": "q1",
                "question_text": "1. 35 +27 = ?",
                "student_answer": "62",
                "confidence": 0.95,
                "student_answer_confidence": 0.85,
                "needs_review": False,
            },
            {
                "id": "q2",
                "question_text": "2. 35 +27 = ?",
                "student_answer": "61",
                "confidence": 0.95,
                "student_answer_confidence": 0.85,
                "needs_review": False,
            },
        ]
    )

    assert graded[0]["grade"] == "correct"
    assert graded[1]["grade"] == "incorrect"
    assert graded[0]["grade_confidence"] == pytest.approx(0.85)


def test_needs_review_marking():
    regions = [
        {
            "id": "region_1",
            "type": "vertical_math",
            "bbox": [50, 100, 120, 115],
            "blocks": _vertical_problem(8, 100, "57", "+16", answer=None),
            "confidence": 0.93,
            "needs_review": False,
        }
    ]

    sub_questions = sub_question_cutter(regions)
    extracted = answer_extractor(sub_questions, regions[0]["blocks"])
    graded = math_rule_grader(extracted)

    assert extracted[0]["student_answer"] is None
    assert graded[0]["needs_review"] is True
    assert graded[0]["grade"] == "needs_review"


def test_compute_metrics():
    sub_questions = [
        {"grade": "correct", "needs_review": False},
        {"grade": "incorrect", "needs_review": False},
        {"grade": "needs_review", "needs_review": True},
    ]
    regions = [
        {"type": "question_block"},
        {"type": "vertical_math"},
        {"type": "header"},
        {"type": "annotation"},
    ]

    metrics = compute_metrics(sub_questions, total_blocks=15, elapsed_ms=1000.0, regions=regions)

    assert metrics["usable_regions"] == 2
    assert metrics["question_regions"] == 2
    assert metrics["header_annotation_regions"] == 2
    assert metrics["gradable_questions"] == 2
    assert metrics["needs_review"] == 1
    assert metrics["correct_count"] == 1
    assert metrics["incorrect_count"] == 1
    assert metrics["speedup_vs_qwen"] == pytest.approx(54.898)
    assert metrics["verdict"] == "feasible"


def test_ocr_blocks_unavailable(monkeypatch: pytest.MonkeyPatch):
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("math grader should not run when OCR blocks are unavailable")

    monkeypatch.setattr("backend.eval.repair3d_math_ocr_probe._try_math_rule_grading", fail_if_called)

    report = run_probe([], "verticalmath01.png")

    assert called is False
    assert report["blocks_unavailable"] is True
    assert report["sub_questions_cut"] == 0
    assert report["gradable_questions"] == 0
    assert report["stage_outputs"]["regions"] == []
    assert report["stage_outputs"]["sub_questions"] == []
    assert report["verdict"] == "infeasible"
