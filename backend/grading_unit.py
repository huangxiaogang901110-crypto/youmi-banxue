from __future__ import annotations

import asyncio
import io
import json
import itertools
import re
from typing import Any

from PIL import Image

from db import get_active_pricing
from logger import info, warning
from math_grader import _try_math_rule_grading
from model_logger import make_log_entry
from vision_client import QwenVLClient
import db as _db


_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_ANSWER_TRANSLATION = str.maketrans({
    "O": "0",
    "o": "0",
    "Q": "0",
    "D": "0",
    "I": "1",
    "l": "1",
    "|": "1",
    "S": "5",
    "s": "5",
    "e": "5",
    "E": "5",
    "B": "8",
})


def _qget(question: Any, key: str, default=None):
    if hasattr(question, key):
        return getattr(question, key)
    if isinstance(question, dict):
        return question.get(key, default)
    return default


def _qset(question: Any, key: str, value) -> None:
    if hasattr(question, key):
        setattr(question, key, value)
    elif isinstance(question, dict):
        question[key] = value


def _bbox_union(bboxes: list[list[float] | tuple[float, float, float, float] | None]) -> list[float] | None:
    valid = [bbox for bbox in bboxes if bbox and len(bbox) == 4]
    if not valid:
        return None
    x1 = min(float(b[0]) for b in valid)
    y1 = min(float(b[1]) for b in valid)
    x2 = max(float(b[0]) + float(b[2]) for b in valid)
    y2 = max(float(b[1]) + float(b[3]) for b in valid)
    return [round(x1, 2), round(y1, 2), round(x2 - x1, 2), round(y2 - y1, 2)]


def _expand_bbox(
    bbox: list[float] | None,
    *,
    left: float = 0.0,
    top: float = 0.0,
    right: float = 0.0,
    bottom: float = 0.0,
    image_width: int | None = None,
    image_height: int | None = None,
) -> list[float] | None:
    if not bbox or len(bbox) != 4:
        return None
    x, y, w, h = [float(v) for v in bbox]
    nx = max(0.0, x - left)
    ny = max(0.0, y - top)
    nr = x + w + right
    nb = y + h + bottom
    if image_width:
        nr = min(float(image_width), nr)
    if image_height:
        nb = min(float(image_height), nb)
    return [round(nx, 2), round(ny, 2), round(max(1.0, nr - nx), 2), round(max(1.0, nb - ny), 2)]


def _question_anchor(question: Any) -> tuple[float, float]:
    answer_bbox = _qget(question, "answer_bbox")
    bbox = answer_bbox or _qget(question, "bbox") or [0, 0, 0, 0]
    x, y, w, h = [float(v) for v in bbox]
    return x + (w / 2.0), y + (h / 2.0)


def _question_seed_position(question: Any) -> tuple[float, float]:
    bbox = _qget(question, "bbox") or [0, 0, 0, 0]
    x = float(bbox[0])
    y = float(bbox[1])
    return x, y


def _question_reliable(question: Any) -> bool:
    student_answer = str(_qget(question, "student_answer") or "").strip()
    if not student_answer:
        return False
    source = str(_qget(question, "answer_bbox_source") or "").strip().lower()
    return source in {"equals_right", "embedded", "right_neighbor", "vertical_result"}


def _question_is_vertical(question: Any) -> bool:
    section_title = str(_qget(question, "section_title") or "")
    if "竖" in section_title:
        return True
    if str(_qget(question, "answer_bbox_source") or "") == "vertical_result":
        return True
    text = str(_qget(question, "question_text") or "")
    digit_runs = re.findall(r"\d+", text)
    operator_count = sum(1 for ch in text if ch in "+-×xX")
    max_digits = max((len(run) for run in digit_runs), default=0)
    bbox = _qget(question, "bbox") or [0, 0, 0, 0]
    tall_block = len(bbox) == 4 and float(bbox[3]) >= 95
    if operator_count >= 2:
        return True
    if max_digits >= 2 and len(digit_runs) >= 2:
        return True
    return bool(tall_block and max_digits >= 2)


def _normalize_answer_text(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"null", "none"}:
        return None
    cleaned = text.translate(_ANSWER_TRANSLATION)
    cleaned = cleaned.replace(" ", "")
    cleaned = re.sub(r"^[=＝:：/\\\-()]+", "", cleaned)
    cleaned = re.sub(r"[^0-9+\-]+$", "", cleaned)
    return cleaned or None


def _question_expression(question: Any) -> str:
    return str(_qget(question, "question_text") or "").strip()


def _question_text_suspicious(text: str) -> bool:
    digit_runs = re.findall(r"\d+", str(text or ""))
    operator_count = sum(1 for ch in str(text or "") if ch in "+-×xX÷")
    longest_digits = max((len(run) for run in digit_runs), default=0)
    if longest_digits >= 4:
        return True
    if operator_count == 1 and longest_digits >= 3:
        return True
    return False


def _grade_question(question: Any) -> bool:
    question_text = _question_expression(question)
    student_answer = _normalize_answer_text(_qget(question, "student_answer"))
    if student_answer is not None:
        _qset(question, "student_answer", student_answer)
    result = _try_math_rule_grading(question_text, student_answer or "")
    if result is None:
        _qset(question, "is_correct", None)
        return False
    _qset(question, "is_correct", result["is_correct"])
    _qset(question, "grading_explanation", result["explanation"])
    return True


def _recover_expression_by_target(raw_expression: str, student_answer: str | None) -> str | None:
    normalized_answer = _normalize_answer_text(student_answer)
    if not normalized_answer or not re.fullmatch(r"-?\d+", normalized_answer):
        return None
    digit_runs = re.findall(r"\d+", str(raw_expression or ""))
    if len(digit_runs) < 3 or len(digit_runs) > 4:
        return None
    numbers = [int(item) for item in digit_runs]
    target = int(normalized_answer)
    for ops in itertools.product(["+", "-", "×"], repeat=len(numbers) - 1):
        expr = str(numbers[0])
        total = numbers[0]
        valid = True
        for op, value in zip(ops, numbers[1:]):
            expr += f"{op}{value}"
            if op == "+":
                total += value
            elif op == "-":
                total -= value
            elif op == "×":
                total *= value
            else:
                valid = False
                break
        if valid and total == target:
            return expr
    return None


def _collect_process_bboxes(question: Any, ocr_blocks: list[dict]) -> list[list[float]]:
    bbox = _qget(question, "bbox")
    if not bbox or len(bbox) != 4:
        return []
    x, y, w, h = [float(v) for v in bbox]
    results: list[list[float]] = []
    for block in ocr_blocks or []:
        text = str(block.get("text", "") or "").strip()
        if not text or not re.search(r"\d", text):
            continue
        bx = float(block.get("x", 0))
        by = float(block.get("y", 0))
        bw = float(block.get("w", 0))
        bh = float(block.get("h", 0))
        if bw <= 0 or bh <= 0:
            continue
        cx = bx + (bw / 2.0)
        if cx < (x - 36.0) or cx > (x + w + 90.0):
            continue
        if by < (y + min(20.0, h * 0.2)):
            continue
        if by > (y + h + 240.0):
            continue
        results.append([round(bx, 2), round(by, 2), round(bw, 2), round(bh, 2)])
    results.sort(key=lambda item: (item[1], item[0]))
    return results[:12]


def _cluster_oral_rows(questions: list[Any]) -> list[list[Any]]:
    ordered = sorted(
        questions,
        key=lambda question: (_question_seed_position(question)[1], _question_seed_position(question)[0], int(_qget(question, "question_number") or 0)),
    )
    rows: list[list[Any]] = []
    for question in ordered:
        _, cy = _question_seed_position(question)
        if not rows:
            rows.append([question])
            continue
        prev_center = sum(_question_seed_position(item)[1] for item in rows[-1]) / max(1, len(rows[-1]))
        if abs(cy - prev_center) <= 30.0:
            rows[-1].append(question)
        else:
            rows.append([question])
    for row in rows:
        row.sort(key=lambda question: (_question_seed_position(question)[0], int(_qget(question, "question_number") or 0)))
    return rows


def _make_unit_bbox(
    unit_type: str,
    members: list[Any],
    *,
    image_width: int | None,
    image_height: int | None,
) -> list[float] | None:
    if not members:
        return None
    if unit_type == "vertical":
        base_bbox = _qget(members[0], "bbox") or _qget(members[0], "answer_bbox")
        if not base_bbox or len(base_bbox) != 4:
            return None
        x, y, w, h = [float(v) for v in base_bbox]
        nx = max(0.0, x - 40.0)
        ny = max(0.0, y - 18.0)
        target_w = min(170.0, max(120.0, w + 90.0))
        target_h = min(320.0, max(220.0, h + 160.0))
        nr = nx + target_w
        nb = ny + target_h
        if image_width:
            nr = min(float(image_width), nr)
        if image_height:
            nb = min(float(image_height), nb)
        return [round(nx, 2), round(ny, 2), round(max(1.0, nr - nx), 2), round(max(1.0, nb - ny), 2)]
    union_bbox = _bbox_union([
        _qget(member, "bbox")
        for member in members
    ] + [
        _qget(member, "answer_bbox")
        for member in members
    ])
    return _expand_bbox(
        union_bbox,
        left=18.0,
        top=18.0,
        right=24.0,
        bottom=30.0,
        image_width=image_width,
        image_height=image_height,
    )


def build_grading_units(
    questions: list[Any],
    *,
    image_width: int | None = None,
    image_height: int | None = None,
) -> list[dict]:
    indexed = list(enumerate(questions))
    oral_items = [(idx, question) for idx, question in indexed if not _question_is_vertical(question)]
    vertical_items = [(idx, question) for idx, question in indexed if _question_is_vertical(question)]

    units: list[dict] = []
    unit_count = len(questions)
    oral_rows = _cluster_oral_rows([question for _, question in oral_items])
    oral_lookup = {id(question): idx for idx, question in oral_items}
    pair_candidates: list[tuple[float, int, int, list[Any]]] = []

    for row_index, row in enumerate(oral_rows, start=1):
        for question in row:
            idx = oral_lookup[id(question)]
            units.append({
                "id": f"unit-{idx + 1}",
                "unit_type": "oral_single",
                "row_index": row_index,
                "section_index": _qget(question, "section_index"),
                "question_ids": [_qget(question, "question_id")],
                "question_indexes": [idx],
                "question_numbers": [_qget(question, "question_number")],
                "expression": _question_expression(question),
                "child_answer": _normalize_answer_text(_qget(question, "student_answer")),
                "child_answer_source": str(_qget(question, "answer_bbox_source") or ""),
                "is_correct": _qget(question, "is_correct"),
                "grading_unit_bbox": _make_unit_bbox("oral_single", [question], image_width=image_width, image_height=image_height),
            })
        for left_index in range(len(row) - 1):
            left_q = row[left_index]
            right_q = row[left_index + 1]
            left_anchor = _question_anchor(left_q)[0]
            right_anchor = _question_anchor(right_q)[0]
            dx = max(1.0, right_anchor - left_anchor)
            if dx < 60.0 or dx > 240.0:
                continue
            low_count = int(not _question_reliable(left_q)) + int(not _question_reliable(right_q))
            if low_count <= 0:
                continue
            score = (low_count * 10.0) - min(dx / 20.0, 6.0)
            pair_candidates.append((score, oral_lookup[id(left_q)], oral_lookup[id(right_q)], [left_q, right_q]))

    # Greedy merge until grading units are within the requested range.
    target_min = 24
    target_max = 28
    active_units = {unit["question_indexes"][0]: unit for unit in units}
    used_indexes: set[int] = set()
    for _, left_idx, right_idx, members in sorted(pair_candidates, key=lambda item: (-item[0], item[1], item[2])):
        if unit_count <= target_max:
            break
        if left_idx in used_indexes or right_idx in used_indexes:
            continue
        if left_idx not in active_units or right_idx not in active_units:
            continue
        merged_question_indexes = sorted([left_idx, right_idx])
        merged_members = [questions[idx] for idx in merged_question_indexes]
        merged_unit = {
            "id": f"unit-{merged_question_indexes[0] + 1}-{merged_question_indexes[1] + 1}",
            "unit_type": "oral_pair",
            "row_index": active_units[left_idx]["row_index"],
            "section_index": active_units[left_idx]["section_index"],
            "question_ids": [_qget(member, "question_id") for member in merged_members],
            "question_indexes": merged_question_indexes,
            "question_numbers": [_qget(member, "question_number") for member in merged_members],
            "expression": " | ".join(_question_expression(member) for member in merged_members),
            "child_answer": None,
            "child_answer_source": "",
            "is_correct": None,
            "grading_unit_bbox": _make_unit_bbox("oral_pair", merged_members, image_width=image_width, image_height=image_height),
        }
        active_units[left_idx] = merged_unit
        active_units.pop(right_idx, None)
        used_indexes.add(left_idx)
        used_indexes.add(right_idx)
        unit_count -= 1

    if unit_count > target_max:
        fallback_units = sorted(
            active_units.values(),
            key=lambda unit: (unit.get("row_index") or 0, unit["question_numbers"][0]),
        )
        for left_unit, right_unit in zip(fallback_units, fallback_units[1:]):
            if unit_count <= target_max:
                break
            if left_unit["unit_type"] != "oral_single" or right_unit["unit_type"] != "oral_single":
                continue
            if left_unit.get("row_index") != right_unit.get("row_index"):
                continue
            left_idx = left_unit["question_indexes"][0]
            right_idx = right_unit["question_indexes"][0]
            merged_members = [questions[left_idx], questions[right_idx]]
            active_units[left_idx] = {
                "id": f"unit-{left_idx + 1}-{right_idx + 1}",
                "unit_type": "oral_pair",
                "row_index": left_unit["row_index"],
                "section_index": left_unit["section_index"],
                "question_ids": [_qget(member, "question_id") for member in merged_members],
                "question_indexes": [left_idx, right_idx],
                "question_numbers": [_qget(member, "question_number") for member in merged_members],
                "expression": " | ".join(_question_expression(member) for member in merged_members),
                "child_answer": None,
                "child_answer_source": "",
                "is_correct": None,
                "grading_unit_bbox": _make_unit_bbox("oral_pair", merged_members, image_width=image_width, image_height=image_height),
            }
            active_units.pop(right_idx, None)
            unit_count -= 1

    oral_units = sorted(active_units.values(), key=lambda unit: (unit["row_index"] or 0, unit["question_numbers"][0]))
    for idx, question in vertical_items:
        units_bbox = _make_unit_bbox("vertical", [question], image_width=image_width, image_height=image_height)
        oral_units.append({
            "id": f"unit-{idx + 1}",
            "unit_type": "vertical",
            "row_index": _qget(question, "layout_row_index"),
            "section_index": _qget(question, "section_index"),
            "question_ids": [_qget(question, "question_id")],
            "question_indexes": [idx],
            "question_numbers": [_qget(question, "question_number")],
            "expression": _question_expression(question),
            "child_answer": _normalize_answer_text(_qget(question, "student_answer")),
            "child_answer_source": str(_qget(question, "answer_bbox_source") or ""),
            "is_correct": _qget(question, "is_correct"),
            "grading_unit_bbox": units_bbox,
        })

    oral_units.sort(key=lambda unit: (unit.get("section_index") or 0, unit.get("row_index") or 0, unit["question_numbers"][0]))
    return oral_units


def apply_grading_unit_metadata(questions: list[Any], units: list[dict], ocr_blocks: list[dict] | None = None) -> None:
    question_map = {_qget(question, "question_id"): question for question in questions}
    for unit in units:
        process_bboxes = []
        if unit["unit_type"] == "vertical" and unit["question_ids"]:
            question = question_map.get(unit["question_ids"][0])
            if question is not None:
                process_bboxes = _collect_process_bboxes(question, ocr_blocks or [])
        unit["process_bboxes"] = process_bboxes
        for question_id in unit["question_ids"]:
            question = question_map.get(question_id)
            if question is None:
                continue
            source = str(_qget(question, "answer_bbox_source") or "").strip()
            if source:
                source = f"ocr:{source}"
            _qset(question, "grading_unit_id", unit["id"])
            _qset(question, "grading_unit_type", unit["unit_type"])
            _qset(question, "grading_unit_bbox", unit["grading_unit_bbox"])
            _qset(question, "child_answer_source", source or None)
            _qset(question, "process_bboxes", process_bboxes or None)


def build_group_boxes(units: list[dict]) -> list[dict]:
    boxes = []
    for index, unit in enumerate(units, start=1):
        bbox = unit.get("grading_unit_bbox")
        if not bbox or len(bbox) != 4:
            continue
        boxes.append({
            "group_id": unit["id"],
            "group_index": index,
            "label": str(index),
            "title": "竖式" if unit["unit_type"] == "vertical" else "口算",
            "bbox": [float(v) for v in bbox],
            "question_ids": unit["question_ids"],
        })
    return boxes


def _crop_bytes(image_bytes: bytes, bbox: list[float]) -> bytes | None:
    if not bbox or len(bbox) != 4:
        return None
    x, y, w, h = [max(0, int(round(v))) for v in bbox]
    if w <= 0 or h <= 0:
        return None
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            crop = image.crop((x, y, x + w, y + h))
            buffer = io.BytesIO()
            crop.save(buffer, format="JPEG", quality=92)
            return buffer.getvalue()
    except Exception:
        return None


def _parse_qwen_answers(content: str) -> list[dict]:
    payload = str(content or "").strip()
    payload = re.sub(r"^```json\s*", "", payload)
    payload = re.sub(r"^```", "", payload)
    payload = re.sub(r"\s*```$", "", payload)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        match = _JSON_ARRAY_RE.search(payload) or _JSON_OBJECT_RE.search(payload)
        if not match:
            return []
        data = json.loads(match.group())
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _strict_vertical_prompt(question: Any) -> str:
    return (
        "图中只有一道小学二年级列竖式题，只做识别，不要自己计算。\n"
        "运算只会出现 +、-、×，不要把减号误读成除号。\n"
        "请读取题目算式本身，以及孩子最终写下的答案。\n"
        "优先读取题目右侧或最底部的最终结果；中间过程数字不要当最终答案；看不清就返回 null。\n"
        "只输出 JSON: "
        "{\"expression\":\"算式或null\",\"student_answer\":\"答案或null\","
        "\"answer_source\":\"final_answer|process_final|process_only|unknown\",\"reason\":\"<=12字\"}\n"
        f"OCR题目参考：{_question_expression(question)}"
    )


def _build_unit_prompt(unit: dict, questions: list[Any]) -> str:
    items = []
    for question in questions:
        items.append(
            f"{_qget(question, 'question_number')}. {_question_expression(question)}"
        )
    if unit["unit_type"] == "vertical":
        return (
            "你在批改小学二年级数学作业，只做识别，不要自己计算。\n"
            "图中是一道或少量列竖式题，运算只会出现 +、-、×，不要把减号误读成除号。\n"
            "请同时读取题目算式本身和孩子最终写下的答案。\n"
            "规则：\n"
            "- 优先读取题目右侧或最底部的最终结果。\n"
            "- 中间过程数字不要当最终答案；如果只能看到过程数字，就标 process_only。\n"
            "- 看不清就返回 null，不要猜。\n"
            "只输出 JSON 数组："
            "[{\"question_number\":1,\"expression\":\"算式或null\",\"student_answer\":\"20或null\","
            "\"answer_source\":\"final_answer|process_final|process_only|unknown\",\"reason\":\"<=12字\"}]\n"
            f"题目列表：{' ; '.join(items)}\n"
            f"当前 unit 类型：{unit['unit_type']}"
        )
    return (
        "你在批改小学数学作业，只做答案识别，不要自己计算。\n"
        "规则：\n"
        "- 口算题只抄孩子写下的最终答案。\n"
        "- 如果能看清题目算式，也一起抄下来。\n"
        "- 竖式题优先读取题目右侧或最底部的最终结果。\n"
        "- 中间过程数字不要当最终答案；如果只能看到过程数字，就标 process_only。\n"
        "- 看不清就返回 null，不要猜。\n"
        "只输出 JSON 数组："
        "[{\"question_number\":1,\"expression\":\"算式或null\",\"student_answer\":\"20或null\","
        "\"answer_source\":\"final_answer|process_final|process_only|unknown\",\"reason\":\"<=12字\"}]\n"
        f"题目列表：{' ; '.join(items)}\n"
        f"当前 unit 类型：{unit['unit_type']}"
    )


def _should_qwen_review(question: Any, unit_type: str) -> bool:
    is_correct = _qget(question, "is_correct")
    source = str(_qget(question, "child_answer_source") or _qget(question, "answer_bbox_source") or "")
    if is_correct is None:
        return True
    if unit_type == "vertical" and is_correct is False:
        return True
    if is_correct is False and source in {
        "handwritten_zone",
        "ocr:handwritten_zone",
        "vertical_result",
        "ocr:vertical_result",
        "right_neighbor",
        "ocr:right_neighbor",
    }:
        return True
    return False


async def run_grading_unit_review(
    *,
    jid: str,
    questions: list[Any],
    units: list[dict],
    image_bytes: bytes,
    trace_id: str,
    parent_id: str,
    child_id: str,
) -> float:
    total_cost = 0.0
    question_map = {_qget(question, "question_id"): question for question in questions}
    client = QwenVLClient()
    if not client._available():
        warning(f"[BG] grading-unit review skipped for {jid}: qwen unavailable")
        return 0.0

    pricing = get_active_pricing("aliyun_dashscope", "qwen-vl-max")
    for unit in units:
        member_questions = [question_map[qid] for qid in unit["question_ids"] if qid in question_map]
        if not member_questions:
            continue
        if not any(_should_qwen_review(question, unit["unit_type"]) for question in member_questions):
            continue

        crop_bytes = _crop_bytes(image_bytes, unit.get("grading_unit_bbox"))
        if not crop_bytes:
            continue
        prompt = _build_unit_prompt(unit, member_questions)
        result = await asyncio.to_thread(
            client._call,
            crop_bytes,
            None,
            prompt,
            700,
            30,
        )
        usage = result.get("usage", {}) if result.get("success") else {}
        vlog = make_log_entry(
            task_id=jid,
            provider_name="aliyun_dashscope",
            model_name="qwen-vl-max",
            feature_code="qwen_vl_grade_unit",
            trace_id=trace_id,
            sub_stage="grading_unit",
            latency_ms=int(result.get("latency_ms", 0) or 0),
            success=bool(result.get("success")),
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            image_count=1,
            image_total_bytes=len(crop_bytes),
            parent_user_id=parent_id,
            child_id=child_id,
            error_code=result.get("error"),
            billing_status="paid" if result.get("success") else "failed",
            pricing=pricing,
            question_count=len(member_questions),
            grading_unit_id=unit["id"],
        )
        total_cost += float(vlog.get("cost_cny", 0.0) or 0.0)
        _db.save_model_call(vlog)
        if not result.get("success"):
            continue

        answer_rows = _parse_qwen_answers(result.get("content", ""))
        by_number = {
            int(item.get("question_number")): item
            for item in answer_rows
            if str(item.get("question_number", "")).isdigit()
        }
        single_fallback = answer_rows[0] if len(member_questions) == 1 and len(answer_rows) == 1 else None
        for question in member_questions:
            item = by_number.get(int(_qget(question, "question_number") or 0)) or single_fallback
            if not item:
                continue
            expression = str(item.get("expression") or "").strip()
            normalized_answer = _normalize_answer_text(item.get("student_answer"))
            answer_source = str(item.get("answer_source") or "unknown").strip() or "unknown"
            reason = str(item.get("reason") or "").strip()
            if expression:
                current_text = _question_expression(question)
                temp_answer = normalized_answer or _normalize_answer_text(_qget(question, "student_answer"))
                expression_result = _try_math_rule_grading(expression, temp_answer or "") if temp_answer else None
                if expression_result is not None and (_question_text_suspicious(current_text) or _qget(question, "is_correct") is False):
                    _qset(question, "question_text", expression)
            if normalized_answer:
                _qset(question, "student_answer", normalized_answer)
                _qset(question, "child_answer_source", f"qwen_unit:{answer_source}")
                _qset(question, "grading_explanation", f"Qwen补判：{reason or answer_source}")
                _grade_question(question)
                if _qget(question, "is_correct") is False:
                    recovered_expression = _recover_expression_by_target(_question_expression(question), normalized_answer)
                    if recovered_expression:
                        _qset(question, "question_text", recovered_expression)
                        _grade_question(question)
            elif _qget(question, "is_correct") is False:
                _qset(question, "is_correct", None)
                _qset(question, "child_answer_source", f"qwen_unit:{answer_source}")
                _qset(question, "grading_explanation", f"Qwen未读到最终答案：{reason or answer_source}")

            if unit["unit_type"] == "vertical" and _qget(question, "is_correct") is False:
                strict_result = await asyncio.to_thread(
                    client._call,
                    crop_bytes,
                    None,
                    _strict_vertical_prompt(question),
                    260,
                    30,
                )
                strict_usage = strict_result.get("usage", {}) if strict_result.get("success") else {}
                strict_log = make_log_entry(
                    task_id=jid,
                    provider_name="aliyun_dashscope",
                    model_name="qwen-vl-max",
                    feature_code="qwen_vl_grade_unit_retry",
                    trace_id=trace_id,
                    sub_stage="grading_unit_retry",
                    latency_ms=int(strict_result.get("latency_ms", 0) or 0),
                    success=bool(strict_result.get("success")),
                    input_tokens=int(strict_usage.get("prompt_tokens", 0) or 0),
                    output_tokens=int(strict_usage.get("completion_tokens", 0) or 0),
                    image_count=1,
                    image_total_bytes=len(crop_bytes),
                    parent_user_id=parent_id,
                    child_id=child_id,
                    error_code=strict_result.get("error"),
                    billing_status="paid" if strict_result.get("success") else "failed",
                    pricing=pricing,
                    question_count=1,
                    grading_unit_id=unit["id"],
                )
                total_cost += float(strict_log.get("cost_cny", 0.0) or 0.0)
                _db.save_model_call(strict_log)
                strict_rows = _parse_qwen_answers(strict_result.get("content", ""))
                strict_item = strict_rows[0] if strict_rows else None
                if isinstance(strict_item, dict):
                    strict_expression = str(strict_item.get("expression") or "").strip()
                    strict_answer = _normalize_answer_text(strict_item.get("student_answer"))
                    strict_source = str(strict_item.get("answer_source") or "unknown").strip() or "unknown"
                    strict_reason = str(strict_item.get("reason") or "").strip()
                    if strict_expression:
                        strict_expr_result = _try_math_rule_grading(strict_expression, strict_answer or "")
                        if strict_expr_result is not None:
                            _qset(question, "question_text", strict_expression)
                    if strict_answer:
                        _qset(question, "student_answer", strict_answer)
                        _qset(question, "child_answer_source", f"qwen_unit_strict:{strict_source}")
                        _qset(question, "grading_explanation", f"Qwen严格复核：{strict_reason or strict_source}")
                        _grade_question(question)
                        if _qget(question, "is_correct") is False:
                            recovered_expression = _recover_expression_by_target(_question_expression(question), strict_answer)
                            if recovered_expression:
                                _qset(question, "question_text", recovered_expression)
                                _grade_question(question)
                    elif _question_text_suspicious(_question_expression(question)):
                        _qset(question, "is_correct", None)
                        _qset(question, "child_answer_source", f"qwen_unit_strict:{strict_source}")
                        _qset(question, "grading_explanation", f"Qwen严格复核未确认最终答案：{strict_reason or strict_source}")

        info(
            f"[BG] grading-unit review {unit['id']} "
            f"members={len(member_questions)} success={result.get('success')}"
        )
    return total_cost
