import json
import re
import sys
from pathlib import Path


META_PATTERNS = (
    re.compile(r"年级"),
    re.compile(r"课时"),
    re.compile(r"单元"),
    re.compile(r"第.{0,8}页"),
    re.compile(r"练习.{0,12}"),
)
HEADER_TEXT_PATTERNS = (
    re.compile(r"2年级B上"),
    re.compile(r"第3课时"),
)
QUESTION_NUMBER = re.compile(
    r"^\s*(?:\(?\d{1,3}\)?[.、．)]|[①②③④⑤⑥⑦⑧⑨⑩]|[一二三四五六七八九十]+[、．.])"
)
MATH_SIGNAL = re.compile(r"(?:\d|[+\-×xX*/÷=<>＜＞≤≥≦≧○])")
WORD_PROBLEM = re.compile(r"(？|\?|多少|几)")
PURE_CJK = re.compile(r"^[\u4e00-\u9fff，。；：、（）《》“”‘’？！\s]+$")
SEED_BINARY = re.compile(r"[\dA-Za-z][+\-×xX*/÷<>＜＞≤≥≦≧][\dA-Za-z]")
ANSWER_MARKER = re.compile(r"[=＝：:<>＜＞≤≥≦≧]")
DIGIT_RE = re.compile(r"\d")
LEADING_ANSWER_NOISE = re.compile(r"^[=＝(（/\\-]+")
OCR_DIGITISH = re.compile(r"^[0-9OoIlZzSsBbGgQqD]+$")
LONG_DIGIT_RUN = re.compile(r"\d{4,}")
INSTRUCTION_PATTERNS = (
    re.compile(r"直接写出得数"),
    re.compile(r"用竖式计算"),
    re.compile(r"练竖式算"),
    re.compile(r"用时"),
    re.compile(r"分秒"),
    re.compile(r"打卡"),
    re.compile(r"任务"),
    re.compile(r"日期"),
    re.compile(r"改正"),
    re.compile(r"注意划出的易混易错"),
    re.compile(r"课间活动"),
)


def _load_blocks(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        blocks = payload
    elif isinstance(payload, dict):
        blocks = payload.get("blocks")
        if blocks is None:
            blocks = payload.get("ocr_blocks")
    else:
        blocks = None
    if not isinstance(blocks, list):
        raise ValueError("blocks payload must be a list or dict with blocks")
    return payload, blocks


def _bbox_from_block(block):
    bbox = block.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4:
        raw = bbox
    elif all(key in block for key in ("x", "y", "w", "h")):
        raw = [block.get("x", 0), block.get("y", 0), block.get("w", 0), block.get("h", 0)]
    elif isinstance(block.get("pos"), list) and len(block["pos"]) == 4:
        raw = block["pos"]
    else:
        return None
    try:
        x, y, w, h = (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return [x, y, w, h]


def _union_bbox(bboxes):
    valid = [bbox for bbox in bboxes if bbox]
    if not valid:
        return None
    x1 = min(bbox[0] for bbox in valid)
    y1 = min(bbox[1] for bbox in valid)
    x2 = max(bbox[0] + bbox[2] for bbox in valid)
    y2 = max(bbox[1] + bbox[3] for bbox in valid)
    return [round(x1, 2), round(y1, 2), round(x2 - x1, 2), round(y2 - y1, 2)]


def _normalize_blocks(raw_blocks):
    normalized = []
    bottoms = []
    for index, raw_block in enumerate(raw_blocks):
        text = str(raw_block.get("text", "") or "").strip()
        if not text:
            continue
        bbox = _bbox_from_block(raw_block)
        if bbox:
            bottoms.append(bbox[1] + bbox[3])
        normalized.append(
            {
                "index": index,
                "text": text,
                "bbox": bbox,
                "raw": raw_block,
            }
        )
    page_height = max(bottoms) if bottoms else 1.0
    for block in normalized:
        bbox = block["bbox"]
        if bbox:
            block["top"] = bbox[1]
            block["bottom"] = bbox[1] + bbox[3]
            block["left"] = bbox[0]
            block["right"] = bbox[0] + bbox[2]
            block["center_y"] = bbox[1] + bbox[3] / 2.0
            block["center_x"] = bbox[0] + bbox[2] / 2.0
            block["top_ratio"] = block["top"] / page_height if page_height else 0.0
        else:
            block["top"] = 0.0
            block["bottom"] = 0.0
            block["left"] = 0.0
            block["right"] = 0.0
            block["center_y"] = 0.0
            block["center_x"] = 0.0
            block["top_ratio"] = 0.0
    return normalized, page_height


def _has_math_signal(text: str):
    return bool(MATH_SIGNAL.search(text))


def _looks_like_question(text: str):
    return bool(QUESTION_NUMBER.match(text) or _has_math_signal(text) or WORD_PROBLEM.search(text))


def _is_meta_block(block, page_height: float):
    text = block["text"]
    if any(pattern.search(text) for pattern in META_PATTERNS):
        return True
    if block["bbox"] and page_height and block["top_ratio"] <= 0.10 and len(text) < 10:
        return True
    if len(text) > 200 and not _has_math_signal(text):
        return True
    return False


def _is_instruction_block(block):
    text = block["text"]
    if any(pattern.search(text) for pattern in INSTRUCTION_PATTERNS):
        return True
    return bool(PURE_CJK.match(text)) and not re.search(r"\d", text) and len(text) >= 4


def _clean_answer_text(text: str):
    stripped = LEADING_ANSWER_NOISE.sub("", text.strip())
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff%]", "", stripped)


def _answer_text_length(text: str):
    return len(_clean_answer_text(text))


def _answer_value_confidence(text: str):
    raw = text.strip()
    if not DIGIT_RE.search(raw):
        return 0
    stripped = LEADING_ANSWER_NOISE.sub("", raw)
    if not stripped:
        return 0
    if re.search(r"[+×xX*/÷=＝：:<>＜＞≤≥≦≧]", stripped):
        return 0
    cleaned = _clean_answer_text(raw)
    if not cleaned:
        return 0
    if OCR_DIGITISH.fullmatch(cleaned) or re.fullmatch(r"\d+[A-Za-z\u4e00-\u9fff%]?$", cleaned):
        return 2
    if len(cleaned) <= 3:
        return 1
    return 0


def _extract_last_answer_tail(text: str):
    positions = [(text.rfind(char), char) for char in "=＝<>＜＞≤≥≦≧：:"]
    marker_index, marker_char = max(positions, key=lambda item: item[0])
    if marker_index < 0:
        return None
    return marker_char, text[marker_index + 1 :].strip()


def _answer_tail_bbox_from_block(block):
    extracted = _extract_last_answer_tail(block["text"])
    if extracted is None or not block["bbox"]:
        return None
    marker_char, tail_text = extracted
    confidence = _answer_value_confidence(tail_text)
    if confidence < 1:
        return None
    x, y, w, h = block["bbox"]
    flow_is_vertical = h > (w * 2.2)
    if flow_is_vertical:
        fraction = max(0.25, min(0.60, len(tail_text) / max(1, len(block["text"]))))
        answer_h = max(12.0, h * fraction)
        bbox = [round(x, 2), round(y + h - answer_h, 2), round(w, 2), round(answer_h, 2)]
    else:
        fraction = max(0.22, min(0.60, len(tail_text) / max(1, len(block["text"]))))
        answer_w = max(12.0, w * fraction)
        bbox = [round(x + w - answer_w, 2), round(y, 2), round(answer_w, 2), round(h, 2)]
    return {
        "bbox": bbox,
        "confidence": confidence,
        "marker": marker_char,
        "answer_length": _answer_text_length(tail_text),
        "block_index": block["index"],
        "block_text": block["text"],
    }


def _y_overlap_ratio(seed, block):
    overlap = min(seed["bottom"], block["bottom"]) - max(seed["top"], block["top"])
    if overlap <= 0:
        return 0.0
    return overlap / min(seed["bbox"][3], block["bbox"][3])


def _bbox_is_near_seed(seed, bbox):
    dx = 0.0
    if bbox[0] + bbox[2] < seed["left"]:
        dx = seed["left"] - (bbox[0] + bbox[2])
    elif bbox[0] > seed["right"]:
        dx = bbox[0] - seed["right"]

    dy = 0.0
    if bbox[1] + bbox[3] < seed["top"]:
        dy = seed["top"] - (bbox[1] + bbox[3])
    elif bbox[1] > seed["bottom"]:
        dy = bbox[1] - seed["bottom"]
    return max(dx, dy) < 100.0


def _is_base_candidate_seed(block):
    return (
        bool(block["bbox"])
        and not block["is_meta"]
        and not block["is_instruction"]
        and not block["text"].startswith(("=", "＝"))
        and bool(SEED_BINARY.search(block["text"]))
    )


def _has_intervening_candidate_seed(seed, target, candidate_seed_blocks):
    for other in candidate_seed_blocks:
        if other["index"] in (seed["index"], target["index"]):
            continue
        if other["left"] < seed["right"] - 5.0:
            continue
        if other["right"] > target["left"] + 5.0:
            continue
        if _y_overlap_ratio(seed, other) >= 0.35:
            return True
    return False


def _find_row_value_answer(seed, blocks, candidate_seed_blocks):
    options = []
    for block in blocks:
        if block["index"] == seed["index"] or not block["bbox"] or block["is_meta"] or block["is_instruction"]:
            continue
        if any(block["index"] == candidate_seed["index"] for candidate_seed in candidate_seed_blocks):
            continue
        if block["text"].startswith(("=", "＝")):
            continue
        confidence = _answer_value_confidence(block["text"])
        if confidence < 1:
            continue
        answer_length = _answer_text_length(block["text"])
        if seed["top"] >= 700.0 and answer_length < 2:
            continue
        gap = block["left"] - seed["right"]
        if gap < -15.0 or gap > 75.0:
            continue
        if block["center_x"] <= seed["center_x"]:
            continue
        if _y_overlap_ratio(seed, block) < 0.45:
            continue
        if _has_intervening_candidate_seed(seed, block, candidate_seed_blocks):
            continue
        options.append(
            {
                "bbox": block["bbox"],
                "confidence": confidence,
                "answer_length": answer_length,
                "block_index": block["index"],
                "block_text": block["text"],
                "source": "right_neighbor",
                "gap": max(0.0, gap),
            }
        )
    options.sort(key=lambda item: (-item["confidence"], -item["answer_length"], item["gap"]))
    return options[0] if options else None


def _find_embedded_answer(seed, blocks, candidate_seed_blocks):
    options = []
    for block in blocks:
        if block["index"] == seed["index"] or not block["bbox"] or block["is_meta"] or block["is_instruction"]:
            continue
        if any(block["index"] == candidate_seed["index"] for candidate_seed in candidate_seed_blocks):
            continue
        tail = _answer_tail_bbox_from_block(block)
        if tail is None:
            continue
        gap = block["left"] - seed["right"]
        if gap < -15.0 or gap > 50.0:
            continue
        if block["center_x"] <= seed["center_x"]:
            continue
        if _y_overlap_ratio(seed, block) < 0.45:
            continue
        if _has_intervening_candidate_seed(seed, block, candidate_seed_blocks):
            continue
        options.append(
            {
                "bbox": tail["bbox"],
                "confidence": tail["confidence"],
                "answer_length": tail["answer_length"],
                "block_index": block["index"],
                "block_text": block["text"],
                "source": "embedded",
                "gap": max(0.0, gap),
            }
        )
    options.sort(key=lambda item: (-item["confidence"], -item["answer_length"], item["gap"]))
    return options[0] if options else None


def _find_vertical_last_row_answer(seed, blocks, candidate_seed_blocks):
    if seed["top"] < 700.0:
        return None
    if seed["bbox"][3] > 100.0 and ANSWER_MARKER.search(seed["text"]):
        return None

    local_blocks = []
    for block in blocks:
        if block["index"] == seed["index"] or not block["bbox"] or block["is_meta"] or block["is_instruction"]:
            continue
        if any(block["index"] == candidate_seed["index"] for candidate_seed in candidate_seed_blocks):
            continue
        confidence = _answer_value_confidence(block["text"])
        if confidence < 2 or _answer_text_length(block["text"]) < 2:
            continue
        if abs(block["center_x"] - seed["center_x"]) > 55.0:
            continue
        if block["top"] < seed["bottom"] - 10.0 or block["top"] > seed["bottom"] + 100.0:
            continue
        local_blocks.append(block)

    if not local_blocks:
        return None

    max_top = max(block["top"] for block in local_blocks)
    row_blocks = [block for block in local_blocks if max_top - block["top"] <= 24.0]
    row_blocks.sort(key=lambda block: (block["left"], block["top"]))
    selected = row_blocks[-1]
    return {
        "bbox": selected["bbox"],
        "confidence": 2,
        "answer_length": _answer_text_length(selected["text"]),
        "block_index": selected["index"],
        "block_text": selected["text"],
        "source": "vertical_last_row",
        "gap": 0.0,
    }


def _candidate_has_answer_anchor(seed, blocks, base_seed_blocks):
    if _answer_tail_bbox_from_block(seed) is not None:
        return True
    if _find_embedded_answer(seed, blocks, base_seed_blocks) is not None:
        return True
    if _find_row_value_answer(seed, blocks, base_seed_blocks) is not None:
        return True
    if _find_vertical_last_row_answer(seed, blocks, base_seed_blocks) is not None:
        return True
    if seed["top"] < 700.0:
        for block in blocks:
            if block["index"] == seed["index"] or not block["bbox"] or block["is_meta"] or block["is_instruction"]:
                continue
            if any(block["index"] == base_seed["index"] for base_seed in base_seed_blocks):
                continue
            if _answer_tail_bbox_from_block(block) is None:
                continue
            if abs(block["center_x"] - seed["center_x"]) > 55.0:
                continue
            if block["top"] < seed["top"] - 10.0 or block["top"] > seed["bottom"] + 80.0:
                continue
            return True
    return False


def _make_candidate(seed):
    question_type = "arithmetic"
    if re.search(r"[<>＜＞≤≥≦≧]", seed["text"]):
        question_type = "comparison"
    return {
        "seed_index": seed["index"],
        "block_indices": [seed["index"]],
        "text": seed["text"],
        "bbox": seed["bbox"],
        "question_type": question_type,
        "answer_bbox": None,
        "answer_bbox_source": None,
        "needs_review": True,
    }


def _infer_answer_bbox(candidate, blocks, candidate_seed_blocks):
    seed = next(block for block in candidate_seed_blocks if block["index"] == candidate["seed_index"])
    suspicious_seed = bool(LONG_DIGIT_RUN.search(seed["text"]))

    same_block_tail = _answer_tail_bbox_from_block(seed)
    if same_block_tail and _bbox_is_near_seed(seed, same_block_tail["bbox"]):
        source = "equals_right" if same_block_tail["marker"] in ("=", "＝") else "embedded"
        return {
            "answer_bbox": same_block_tail["bbox"],
            "answer_bbox_source": source,
            "needs_review": suspicious_seed or same_block_tail["confidence"] < 2,
            "support_block_index": same_block_tail["block_index"],
            "support_block_text": same_block_tail["block_text"],
        }

    right_neighbor = _find_row_value_answer(seed, blocks, candidate_seed_blocks)
    embedded = _find_embedded_answer(seed, blocks, candidate_seed_blocks)
    vertical = _find_vertical_last_row_answer(seed, blocks, candidate_seed_blocks)

    options = []
    if right_neighbor and _bbox_is_near_seed(seed, right_neighbor["bbox"]):
        options.append(right_neighbor)
    if embedded and _bbox_is_near_seed(seed, embedded["bbox"]):
        options.append(embedded)
    if vertical and _bbox_is_near_seed(seed, vertical["bbox"]):
        options.append(vertical)

    if not options:
        return {
            "answer_bbox": None,
            "answer_bbox_source": None,
            "needs_review": True,
            "support_block_index": None,
            "support_block_text": None,
        }

    if vertical and right_neighbor and right_neighbor["answer_length"] < 2:
        selected = vertical
    else:
        source_rank = {"right_neighbor": 0, "embedded": 1, "vertical_last_row": 2}
        options.sort(
            key=lambda item: (
                source_rank[item["source"]],
                -item["confidence"],
                -item["answer_length"],
                item["gap"],
            )
        )
        selected = options[0]

    needs_review = suspicious_seed or selected["confidence"] < 2
    if selected["source"] == "vertical_last_row" and selected["answer_length"] > 2 and suspicious_seed:
        needs_review = True

    return {
        "answer_bbox": selected["bbox"],
        "answer_bbox_source": selected["source"],
        "needs_review": needs_review,
        "support_block_index": selected["block_index"],
        "support_block_text": selected["block_text"],
    }


def _count_suspected_meta_false_positives(meta_blocks):
    return sum(
        1
        for block in meta_blocks
        if _looks_like_question(block["text"])
        and not any(pattern.search(block["text"]) for pattern in HEADER_TEXT_PATTERNS)
    )


def _make_report(blocks_path: Path):
    _, raw_blocks = _load_blocks(blocks_path)
    blocks, page_height = _normalize_blocks(raw_blocks)

    total_blocks = len(blocks)
    meta_blocks = []
    content_blocks = []
    math_blocks = []
    for block in blocks:
        block["is_meta"] = _is_meta_block(block, page_height)
        block["is_instruction"] = _is_instruction_block(block)
        block["is_math"] = _has_math_signal(block["text"])
        if block["is_meta"]:
            meta_blocks.append(block)
            continue
        content_blocks.append(block)
        if block["is_math"]:
            math_blocks.append(block)

    base_seed_blocks = [block for block in content_blocks if _is_base_candidate_seed(block)]
    candidate_seed_blocks = [
        block for block in base_seed_blocks if _candidate_has_answer_anchor(block, content_blocks, base_seed_blocks)
    ]

    candidates = []
    used_block_indices = set()
    answer_bbox_count = 0
    for seed in candidate_seed_blocks:
        candidate = _make_candidate(seed)
        inference = _infer_answer_bbox(candidate, content_blocks, candidate_seed_blocks)
        candidate.update(inference)
        if candidate["support_block_index"] is not None:
            candidate["block_indices"].append(candidate["support_block_index"])
        used_block_indices.update(candidate["block_indices"])
        if candidate["answer_bbox"] and not candidate["needs_review"]:
            answer_bbox_count += 1
        candidates.append(candidate)

    candidates.sort(key=lambda candidate: candidate["seed_index"])
    candidate_count = len(candidates)
    answer_ratio = (answer_bbox_count / candidate_count) if candidate_count else 0.0
    suspected_meta_false_positive_count = _count_suspected_meta_false_positives(meta_blocks)
    header_as_question = any(
        pattern.search(candidate["text"])
        for candidate in candidates
        for pattern in HEADER_TEXT_PATTERNS
    )
    ungrouped_math_blocks = sum(1 for block in math_blocks if block["index"] not in used_block_indices)

    quality_gate = {
        "candidate_questions_gte_28": candidate_count >= 28,
        "suspected_meta_false_positive_count_lte_2": suspected_meta_false_positive_count <= 2,
        "answer_bbox_ratio_gte_80pct": answer_ratio >= 0.8,
        "header_not_treated_as_question": not header_as_question,
    }
    quality_gate_passed = all(quality_gate.values())

    answer_examples = [
        {
            "text": candidate["text"],
            "source": candidate["answer_bbox_source"],
            "answer_bbox": candidate["answer_bbox"],
        }
        for candidate in candidates
        if candidate["answer_bbox"] and not candidate["needs_review"]
    ][:5]

    return {
        "input_path": str(blocks_path),
        "total_blocks": total_blocks,
        "math_blocks": len(math_blocks),
        "meta_blocks_filtered": len(meta_blocks),
        "candidate_questions": candidate_count,
        "answer_bbox_count": answer_bbox_count,
        "answer_bbox_ratio": round(answer_ratio, 4),
        "suspected_meta_false_positive_count": suspected_meta_false_positive_count,
        "ungrouped_math_blocks": ungrouped_math_blocks,
        "header_not_treated_as_question": not header_as_question,
        "quality_gate_passed": quality_gate_passed,
        "quality_gate": quality_gate,
        "samples": {
            "answer_bbox_examples": answer_examples,
        },
        "candidates": [
            {
                "seed_index": candidate["seed_index"],
                "block_indices": candidate["block_indices"],
                "text": candidate["text"],
                "bbox": candidate["bbox"],
                "question_type": candidate["question_type"],
                "answer_bbox": candidate["answer_bbox"],
                "answer_bbox_source": candidate["answer_bbox_source"],
                "needs_review": candidate["needs_review"],
            }
            for candidate in candidates
        ],
    }


def _render_markdown(report):
    gate = report["quality_gate"]
    lines = [
        "# Repair3D 13B-0 Math OCR Probe",
        "",
        f"- input_path: `{report['input_path']}`",
        f"- total_blocks: {report['total_blocks']}",
        f"- math_blocks: {report['math_blocks']}",
        f"- meta_blocks_filtered: {report['meta_blocks_filtered']}",
        f"- candidate_questions: {report['candidate_questions']}",
        f"- answer_bbox_count: {report['answer_bbox_count']}",
        f"- answer_bbox_ratio: {report['answer_bbox_ratio']}",
        f"- suspected_meta_false_positive_count: {report['suspected_meta_false_positive_count']}",
        f"- ungrouped_math_blocks: {report['ungrouped_math_blocks']}",
        f"- header_not_treated_as_question: {report['header_not_treated_as_question']}",
        f"- quality_gate_passed: {report['quality_gate_passed']}",
        "",
        "## Quality Gate",
        "",
        f"- candidate_questions >= 28: {gate['candidate_questions_gte_28']}",
        f"- suspected_meta_false_positive_count <= 2: {gate['suspected_meta_false_positive_count_lte_2']}",
        f"- answer_bbox_ratio >= 80%: {gate['answer_bbox_ratio_gte_80pct']}",
        f"- header_not_treated_as_question: {gate['header_not_treated_as_question']}",
        "",
        "## Answer BBox Examples",
        "",
    ]
    examples = report["samples"]["answer_bbox_examples"]
    if not examples:
        lines.append("- none")
    else:
        for example in examples:
            lines.append(
                f"- `{example['text']}` -> `{example['source']}` `{example['answer_bbox']}`"
            )
    lines.extend(["", "## Candidate Notes", "", "- candidate_questions only count question seeds with local answer-anchor clues.", ""])
    return "\n".join(lines)


def main(argv):
    if len(argv) != 2:
        print("usage: python backend/eval/repair3d_13b0_math_ocr_probe.py <blocks_json_path>", file=sys.stderr)
        return 1

    blocks_path = Path(argv[1]).expanduser().resolve()
    if not blocks_path.is_file():
        print(f"blocks json not found: {blocks_path}", file=sys.stderr)
        return 1

    report = _make_report(blocks_path)
    report_path = Path(__file__).resolve().parent / "repair3d13b0_math_ocr_probe_report.md"
    report_path.write_text(_render_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report_written={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
