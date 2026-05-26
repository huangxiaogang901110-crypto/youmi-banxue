from __future__ import annotations

import re
import sys
from pathlib import Path
from statistics import median
from time import perf_counter


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from math_grader import _try_math_rule_grading
from question_cutter import cut_questions
from schemas.recognition import RecognitionBlock, normalize_bbox, normalize_confidence


LOW_CONFIDENCE_THRESHOLD = 0.75
QWEN_BASELINE_MS = 54898.0

QUESTION_REGION_TYPES = {"vertical_math", "question_block"}
SEPARATOR_RE = re.compile(r"^[_=\-]{2,}$")
NUMERIC_ANSWER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
QUESTION_ANCHOR_RE = re.compile(r"^(\d+)\s*[.、．)]$|^[（(]\d+[）)]$")
FOOTER_RE = re.compile(r"第\s*\d+\s*页|共\s*\d+\s*页|页码")
HEADER_KEYWORDS = ("班级", "姓名", "日期", "总分", "得分", "学校", "年级")
ANNOTATION_KEYWORDS = ("批注", "订正", "评语", "老师", "家长", "订")
ARITHMETIC_SIGNAL_RE = re.compile(r"\d+\s*[+\-×xX÷*/]\s*\d+|^[+\-×xX÷*/]\s*\d+")


def _sort_blocks(blocks: list[dict]) -> list[dict]:
    return sorted(blocks, key=lambda block: (block["bbox"][1], block["bbox"][0], block["id"]))


def _block_right(block: dict) -> float:
    return block["bbox"][0] + block["bbox"][2]


def _block_bottom(block: dict) -> float:
    return block["bbox"][1] + block["bbox"][3]


def _union_bbox(blocks: list[dict]) -> list[float]:
    x1 = min(block["bbox"][0] for block in blocks)
    y1 = min(block["bbox"][1] for block in blocks)
    x2 = max(_block_right(block) for block in blocks)
    y2 = max(_block_bottom(block) for block in blocks)
    return [round(x1, 3), round(y1, 3), round(x2 - x1, 3), round(y2 - y1, 3)]


def _mean_confidence(blocks: list[dict], default: float = 0.5) -> float:
    values = [block["confidence"] for block in blocks if block.get("confidence") is not None]
    if not values:
        return default
    return round(sum(values) / len(values), 4)


def _looks_like_separator(text: str) -> bool:
    return bool(SEPARATOR_RE.fullmatch(text.strip()))


def _looks_like_numeric_answer(text: str) -> bool:
    return bool(NUMERIC_ANSWER_RE.fullmatch(text.strip()))


def _looks_like_question_anchor(text: str) -> bool:
    return bool(QUESTION_ANCHOR_RE.fullmatch(text.strip()))


def _contains_arithmetic_signal(text: str) -> bool:
    return bool(ARITHMETIC_SIGNAL_RE.search(text))


def _contains_operator(text: str) -> bool:
    return any(op in text for op in ("+", "-", "×", "x", "X", "÷", "*", "/"))


def _prepare_blocks(blocks: list[dict] | None) -> list[dict]:
    prepared: list[dict] = []
    for index, raw_block in enumerate(blocks or []):
        text = str(raw_block.get("text") or "").strip()
        bbox = normalize_bbox(raw_block.get("bbox") or raw_block.get("pos"))
        if not text or bbox is None:
            continue

        payload = {
            "id": str(raw_block.get("id") or f"ocr_block_{index + 1}"),
            "text": text,
            "bbox": bbox,
            "confidence": normalize_confidence(raw_block.get("confidence")),
            "line_index": raw_block.get("line_index"),
            "block_index": raw_block.get("block_index"),
            "kind": raw_block.get("kind") or "ocr_block",
        }
        block = RecognitionBlock(**payload)
        prepared.append(
            {
                "id": block.id,
                "text": block.text,
                "bbox": [float(value) for value in block.bbox or bbox],
                "confidence": block.confidence,
                "line_index": block.line_index,
                "block_index": block.block_index,
                "kind": block.kind,
            }
        )
    return _sort_blocks(prepared)


def _region_type(blocks: list[dict]) -> str:
    joined_text = " ".join(block["text"] for block in blocks)
    lower_joined = joined_text.lower()
    question_anchors = sum(_looks_like_question_anchor(block["text"]) for block in blocks)
    separator_count = sum(_looks_like_separator(block["text"]) for block in blocks)
    arithmetic_count = sum(_contains_arithmetic_signal(block["text"]) or _contains_operator(block["text"]) for block in blocks)
    numeric_count = sum(_looks_like_numeric_answer(block["text"]) for block in blocks)
    bbox = _union_bbox(blocks)

    if FOOTER_RE.search(joined_text):
        return "footer"
    if any(keyword in joined_text for keyword in HEADER_KEYWORDS) and bbox[1] < 120:
        return "header"
    if any(keyword in joined_text for keyword in ANNOTATION_KEYWORDS) or "批改" in lower_joined:
        return "annotation"
    if separator_count and arithmetic_count >= 1 and numeric_count >= 2 and bbox[2] <= 220:
        return "vertical_math"
    if arithmetic_count or question_anchors:
        return "question_block"
    if bbox[1] < 120:
        return "header"
    return "annotation"


def _question_signal(blocks: list[dict]) -> bool:
    return any(
        _looks_like_question_anchor(block["text"])
        or _contains_operator(block["text"])
        or _looks_like_separator(block["text"])
        for block in blocks
    )


def _compose_question_text(blocks: list[dict], skip_block_ids: set[str] | None = None) -> str:
    tokens: list[str] = []
    skip_block_ids = skip_block_ids or set()
    for block in _sort_blocks(blocks):
        if block["id"] in skip_block_ids:
            continue
        text = block["text"].strip()
        if not text or _looks_like_separator(text):
            continue
        tokens.append(text)

    question_text = " ".join(tokens).strip()
    if question_text and _contains_operator(question_text) and "=" not in question_text:
        question_text = f"{question_text} = ?"
    return question_text


def _matches_cutter_block(candidate: dict, cutter_block) -> bool:
    candidate_bbox = [int(round(value)) for value in candidate["bbox"]]
    return (
        candidate["text"] == cutter_block.text
        and candidate_bbox[0] == cutter_block.x
        and candidate_bbox[1] == cutter_block.y
        and candidate_bbox[2] == cutter_block.w
        and candidate_bbox[3] == cutter_block.h
    )


def _pick_answer_block(blocks: list[dict]) -> dict | None:
    candidates = [
        block
        for block in blocks
        if _looks_like_numeric_answer(block["text"])
        and not _looks_like_question_anchor(block["text"])
        and not _contains_operator(block["text"])
    ]
    if not candidates:
        return None

    separator_blocks = [block for block in blocks if _looks_like_separator(block["text"])]
    if separator_blocks:
        separator = max(separator_blocks, key=lambda block: (block["bbox"][1], block["bbox"][0]))
        line_tolerance = max(separator["bbox"][3], 14.0)
        scored: list[tuple[float, dict]] = []
        for candidate in candidates:
            delta_y = abs(candidate["bbox"][1] - separator["bbox"][1])
            if candidate["bbox"][1] < separator["bbox"][1] - line_tolerance:
                continue
            score = (candidate.get("confidence") or 0.5) * 2.0
            if delta_y <= line_tolerance:
                score += 4.0
            if candidate["bbox"][0] >= separator["bbox"][0]:
                score += 2.0
            score -= delta_y / max(line_tolerance, 1.0)
            scored.append((score, candidate))
        if scored:
            return max(scored, key=lambda item: item[0])[1]
        return None

    return max(candidates, key=lambda block: (block["bbox"][1], block["bbox"][0], block.get("confidence") or 0.5))


def layout_grouper(blocks: list[dict]) -> list[dict]:
    prepared_blocks = _prepare_blocks(blocks)
    if not prepared_blocks:
        return []

    heights = [block["bbox"][3] for block in prepared_blocks]
    gap_threshold = max(18.0, median(heights) * 1.4)

    grouped_blocks: list[list[dict]] = []
    current_group: list[dict] = []
    previous_block: dict | None = None
    for block in prepared_blocks:
        if previous_block is None:
            current_group = [block]
        else:
            gap = block["bbox"][1] - _block_bottom(previous_block)
            if gap > gap_threshold:
                grouped_blocks.append(current_group)
                current_group = [block]
            else:
                current_group.append(block)
        previous_block = block

    if current_group:
        grouped_blocks.append(current_group)

    regions: list[dict] = []
    for index, region_blocks in enumerate(grouped_blocks, start=1):
        region_confidence = _mean_confidence(region_blocks)
        region_type = _region_type(region_blocks)
        regions.append(
            {
                "id": f"region_{index}",
                "type": region_type,
                "bbox": _union_bbox(region_blocks),
                "blocks": region_blocks,
                "confidence": region_confidence,
                "needs_review": region_confidence < LOW_CONFIDENCE_THRESHOLD,
            }
        )
    return regions


def sub_question_cutter(regions: list[dict]) -> list[dict]:
    sub_questions: list[dict] = []
    fallback_number = 1

    for region in regions:
        if region.get("type") not in QUESTION_REGION_TYPES:
            continue

        region_blocks = _sort_blocks(region.get("blocks") or [])
        if not region_blocks:
            continue

        cutter_input = [{"text": block["text"], "pos": block["bbox"]} for block in region_blocks]
        groups = cut_questions(cutter_input)

        cursor = 0
        for group in groups:
            group_blocks: list[dict] = []
            for cutter_block in group.blocks:
                while cursor < len(region_blocks):
                    candidate = region_blocks[cursor]
                    cursor += 1
                    if _matches_cutter_block(candidate, cutter_block):
                        group_blocks.append(candidate)
                        break

            if not group_blocks:
                continue

            question_number = group.question_number if group.question_number > 0 else fallback_number
            fallback_number = max(fallback_number, question_number + 1)
            question_confidence = _mean_confidence(group_blocks)
            sub_questions.append(
                {
                    "id": f"sub_question_{len(sub_questions) + 1}",
                    "region_id": region["id"],
                    "region_type": region["type"],
                    "question_number": question_number,
                    "question_text": _compose_question_text(group_blocks),
                    "bbox": _union_bbox(group_blocks),
                    "blocks": group_blocks,
                    "confidence": question_confidence,
                    "needs_review": question_confidence < LOW_CONFIDENCE_THRESHOLD or not _question_signal(group_blocks),
                }
            )

    return sub_questions


def answer_extractor(sub_questions: list[dict], blocks: list[dict]) -> list[dict]:
    prepared_index = {block["id"]: block for block in _prepare_blocks(blocks)}
    extracted: list[dict] = []

    for sub_question in sub_questions:
        question_blocks = [
            prepared_index.get(block.get("id"), block)
            for block in _sort_blocks(sub_question.get("blocks") or [])
        ]
        answer_block = _pick_answer_block(question_blocks)
        updated = dict(sub_question)
        updated["blocks"] = question_blocks

        if answer_block is None:
            updated["student_answer"] = None
            updated["answer_bbox"] = None
            updated["student_answer_confidence"] = None
            updated["needs_review"] = True
            updated["question_text"] = _compose_question_text(question_blocks)
        else:
            updated["student_answer"] = answer_block["text"].strip()
            updated["answer_bbox"] = answer_block["bbox"]
            updated["student_answer_confidence"] = answer_block.get("confidence")
            updated["needs_review"] = updated["needs_review"] or (
                (answer_block.get("confidence") or 0.0) < LOW_CONFIDENCE_THRESHOLD
            )
            updated["question_text"] = _compose_question_text(question_blocks, {answer_block["id"]})

        extracted.append(updated)

    return extracted


def math_rule_grader(sub_questions: list[dict]) -> list[dict]:
    graded: list[dict] = []
    for sub_question in sub_questions:
        updated = dict(sub_question)
        result = None
        student_answer = updated.get("student_answer")
        question_text = updated.get("question_text") or ""
        if student_answer:
            result = _try_math_rule_grading(question_text, student_answer)

        if result is None:
            updated["grade"] = "needs_review"
            updated["grade_confidence"] = 0.0
            updated["grading_explanation"] = "规则判题不可用"
            updated["needs_review"] = True
        else:
            question_confidence = updated.get("confidence") or 0.8
            answer_confidence = updated.get("student_answer_confidence") or question_confidence
            updated["grade"] = "correct" if result["is_correct"] else "incorrect"
            updated["grade_confidence"] = round(min(question_confidence, answer_confidence), 4)
            updated["grading_explanation"] = result["explanation"]

        graded.append(updated)
    return graded


def compute_metrics(
    sub_questions: list[dict],
    total_blocks: int,
    elapsed_ms: float,
    *,
    regions: list[dict] | None = None,
) -> dict:
    regions = regions or []
    usable_regions = sum(region["type"] in QUESTION_REGION_TYPES for region in regions)
    question_regions = usable_regions
    header_annotation_regions = sum(region["type"] in {"header", "footer", "annotation"} for region in regions)
    gradable_questions = sum(question.get("grade") in {"correct", "incorrect"} for question in sub_questions)
    needs_review = sum(bool(question.get("needs_review") or question.get("grade") == "needs_review") for question in sub_questions)
    correct_count = sum(question.get("grade") == "correct" for question in sub_questions)
    incorrect_count = sum(question.get("grade") == "incorrect" for question in sub_questions)
    blocks_unavailable = total_blocks == 0

    if blocks_unavailable or question_regions == 0 or not sub_questions:
        verdict = "infeasible"
    elif gradable_questions == 0 or needs_review > max(1, len(sub_questions) // 2):
        verdict = "partial"
    else:
        verdict = "feasible"

    effective_elapsed_ms = max(elapsed_ms, 0.001)
    return {
        "total_ocr_blocks": total_blocks,
        "usable_regions": usable_regions,
        "question_regions": question_regions,
        "header_annotation_regions": header_annotation_regions,
        "sub_questions_cut": len(sub_questions),
        "gradable_questions": gradable_questions,
        "needs_review": needs_review,
        "correct_count": correct_count,
        "incorrect_count": incorrect_count,
        "elapsed_ms": round(elapsed_ms, 3),
        "pipeline_stages": {},
        "qwen_baseline_ms": int(QWEN_BASELINE_MS),
        "speedup_vs_qwen": round(QWEN_BASELINE_MS / effective_elapsed_ms, 3),
        "blocks_unavailable": blocks_unavailable,
        "verdict": verdict,
    }


def run_probe(blocks: list[dict] | None, image_path: str | None) -> dict:
    total_blocks = len(blocks or [])
    if not blocks:
        report = compute_metrics([], total_blocks, 0.0, regions=[])
        report["pipeline_stages"] = {
            "layout_grouper_ms": 0.0,
            "sub_question_cutter_ms": 0.0,
            "answer_extractor_ms": 0.0,
            "math_rule_grader_ms": 0.0,
        }
        report["stage_outputs"] = {"regions": [], "sub_questions": []}
        report["image_path"] = image_path
        return report

    prepared_blocks = _prepare_blocks(blocks)
    if not prepared_blocks:
        report = compute_metrics([], total_blocks, 0.0, regions=[])
        report["blocks_unavailable"] = True
        report["pipeline_stages"] = {
            "layout_grouper_ms": 0.0,
            "sub_question_cutter_ms": 0.0,
            "answer_extractor_ms": 0.0,
            "math_rule_grader_ms": 0.0,
        }
        report["stage_outputs"] = {"regions": [], "sub_questions": []}
        report["image_path"] = image_path
        return report

    stage_timings: dict[str, float] = {}
    probe_started_at = perf_counter()

    stage_started_at = perf_counter()
    regions = layout_grouper(prepared_blocks)
    stage_timings["layout_grouper_ms"] = round((perf_counter() - stage_started_at) * 1000, 3)

    stage_started_at = perf_counter()
    sub_questions = sub_question_cutter(regions)
    stage_timings["sub_question_cutter_ms"] = round((perf_counter() - stage_started_at) * 1000, 3)

    stage_started_at = perf_counter()
    answered_questions = answer_extractor(sub_questions, prepared_blocks)
    stage_timings["answer_extractor_ms"] = round((perf_counter() - stage_started_at) * 1000, 3)

    stage_started_at = perf_counter()
    graded_questions = math_rule_grader(answered_questions)
    stage_timings["math_rule_grader_ms"] = round((perf_counter() - stage_started_at) * 1000, 3)

    elapsed_ms = (perf_counter() - probe_started_at) * 1000
    report = compute_metrics(graded_questions, total_blocks, elapsed_ms, regions=regions)
    report["blocks_unavailable"] = not bool(prepared_blocks)
    report["pipeline_stages"] = stage_timings
    report["stage_outputs"] = {
        "regions": regions,
        "sub_questions": graded_questions,
    }
    report["image_path"] = image_path
    return report


__all__ = [
    "answer_extractor",
    "compute_metrics",
    "layout_grouper",
    "math_rule_grader",
    "run_probe",
    "sub_question_cutter",
]
