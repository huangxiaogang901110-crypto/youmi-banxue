import re


META_PATTERNS = [
    re.compile(r"年级"),
    re.compile(r"课时"),
    re.compile(r"单元"),
    re.compile(r"第.{0,8}页"),
    re.compile(r"练习.{0,12}"),
]
HEADER_TEXT_PATTERNS = [
    re.compile(r"2年级B上"),
    re.compile(r"第3课时"),
]
INSTRUCTION_PATTERNS = [
    re.compile(r"打卡"),
    re.compile(r"任务"),
    re.compile(r"练口算"),
    re.compile(r"课间活动"),
    re.compile(r"日期"),
    re.compile(r"姓名"),
    re.compile(r"直接写出得数"),
    re.compile(r"用竖式计算"),
    re.compile(r"改正"),
]
MATH_SIGNAL = re.compile(r"(?:\d|[+\-×xX*/÷=<>＜＞≤≥≦≧○])")
WORD_PROBLEM = re.compile(r"(？|\?|多少|几)")
ARITHMETIC_EXPR = re.compile(r"\d+\s*[=＝]\s*\d|\d+\s*[+\-×xX*/÷]\s*\d")
SEED_BINARY = re.compile(
    r"(\d+\s*[+\-×xX*/÷]\s*\d+|"
    r"\d\s*[+\-×xX*/÷]\s*\d\s*[+\-×xX*/÷]\s*\d+|"
    r"\d\s*[+\-×xX*/÷]\s*\d+\s*=)"
)
DIGIT_RE = re.compile(r"\d")
OCR_DIGITISH = re.compile(r"^[\d\s,.·'′″%oO]+$")
PURE_CJK = re.compile(r"^[\u4e00-\u9fff，。；：、（）《》\"'？！\s]+$")
LEADING_ANSWER_NOISE = re.compile(r"^[=＝：:]\s*")


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


def _normalize_blocks(ocr_blocks):
    normalized = []
    bottoms = []
    for index, raw_block in enumerate(ocr_blocks or []):
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
        if not bbox:
            block["left"] = 0.0
            block["right"] = 0.0
            block["top"] = 0.0
            block["bottom"] = 0.0
            block["center_x"] = 0.0
            block["center_y"] = 0.0
            block["top_ratio"] = 0.0
            continue
        x, y, w, h = bbox
        block["left"] = x
        block["right"] = x + w
        block["top"] = y
        block["bottom"] = y + h
        block["center_x"] = x + (w / 2.0)
        block["center_y"] = y + (h / 2.0)
        block["top_ratio"] = y / page_height if page_height else 0.0
    return normalized, page_height


def _has_math_signal(text):
    return bool(MATH_SIGNAL.search(text or ""))


def _is_meta_block(block, page_height):
    text = block["text"]
    if any(pattern.search(text) for pattern in HEADER_TEXT_PATTERNS):
        return True
    if any(pattern.search(text) for pattern in META_PATTERNS):
        return True
    if block["bbox"] and page_height and block["top_ratio"] <= 0.12 and len(text) <= 12:
        return True
    if len(text) > 180 and not _has_math_signal(text):
        return True
    return False


def _is_instruction_block(block):
    text = block["text"]
    if any(pattern.search(text) for pattern in INSTRUCTION_PATTERNS):
        return True
    return bool(PURE_CJK.fullmatch(text)) and not DIGIT_RE.search(text) and len(text) >= 4


def _is_base_candidate_seed(block):
    text = block["text"]
    if not block["bbox"] or block["is_meta"] or block["is_instruction"]:
        return False
    if text.startswith(("=", "＝", "：", ":")):
        return False
    if WORD_PROBLEM.search(text) and _has_math_signal(text):
        return True
    return bool(SEED_BINARY.search(text) or ARITHMETIC_EXPR.search(text))


def _clean_answer_text(text):
    stripped = LEADING_ANSWER_NOISE.sub("", str(text or "").strip())
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff%]", "", stripped)


def _answer_text_length(text):
    return len(_clean_answer_text(text))


def _answer_value_confidence(text):
    raw = str(text or "").strip()
    if not raw or not DIGIT_RE.search(raw):
        return 0
    stripped = LEADING_ANSWER_NOISE.sub("", raw)
    if not stripped:
        return 0
    if re.search(r"[+\-×xX*/÷=＝<>＜＞≤≥≦≧]", stripped):
        return 0
    cleaned = _clean_answer_text(stripped)
    if not cleaned:
        return 0
    if OCR_DIGITISH.fullmatch(stripped) or OCR_DIGITISH.fullmatch(cleaned):
        return 2
    ocr_normalized = cleaned.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1"}))
    if ocr_normalized.isdigit():
        return 2
    if re.fullmatch(r"\d+[A-Za-z\u4e00-\u9fff%]?$", cleaned):
        return 2
    if len(cleaned) <= 3:
        return 1
    return 0


def _extract_last_answer_tail(text):
    text = str(text or "")
    positions = [(text.rfind(char), char) for char in "=＝<>＜＞≤≥≦≧：:"]
    marker_index, marker_char = max(positions, key=lambda item: item[0])
    if marker_index < 0:
        return None
    tail = text[marker_index + 1 :].strip()
    if not tail:
        return None
    return marker_char, tail


def _answer_tail_bbox_from_block(block):
    extracted = _extract_last_answer_tail(block["text"])
    if extracted is None or not block["bbox"]:
        return None
    marker_char, tail_text = extracted
    confidence = _answer_value_confidence(tail_text)
    if confidence < 1:
        return None
    x, y, w, h = block["bbox"]
    if h > (w * 2.2):
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
        "tail_text": tail_text,
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


def _has_intervening_candidate_seed(seed, target, candidate_seeds):
    for other in candidate_seeds:
        if other["index"] in (seed["index"], target["index"]):
            continue
        if other["left"] < seed["right"] - 5.0:
            continue
        if other["right"] > target["left"] + 5.0:
            continue
        if _y_overlap_ratio(seed, other) >= 0.35:
            return True
    return False


def _find_row_value_answer(seed, blocks, candidate_seeds):
    options = []
    for block in blocks:
        if block["index"] == seed["index"] or not block["bbox"] or block["is_meta"] or block["is_instruction"]:
            continue
        if any(block["index"] == candidate_seed["index"] for candidate_seed in candidate_seeds):
            continue
        if block["text"].startswith(("=", "＝", "：", ":")):
            continue
        confidence = _answer_value_confidence(block["text"])
        if confidence < 1:
            continue
        answer_length = _answer_text_length(block["text"])
        gap = block["left"] - seed["right"]
        if gap < -15.0 or gap > 75.0:
            continue
        if block["center_x"] <= seed["center_x"]:
            continue
        if _y_overlap_ratio(seed, block) < 0.45:
            continue
        if _has_intervening_candidate_seed(seed, block, candidate_seeds):
            continue
        options.append(
            {
                "bbox": block["bbox"],
                "confidence": confidence,
                "answer_length": answer_length,
                "answer_text": block["text"],
                "block_index": block["index"],
                "block_text": block["text"],
                "source": "right_neighbor",
                "gap": max(0.0, gap),
            }
        )
    options.sort(key=lambda item: (-item["confidence"], -item["answer_length"], item["gap"]))
    return options[0] if options else None


def _find_embedded_answer(seed, blocks, candidate_seeds):
    options = []
    for block in blocks:
        if block["index"] == seed["index"] or not block["bbox"] or block["is_meta"] or block["is_instruction"]:
            continue
        if any(block["index"] == candidate_seed["index"] for candidate_seed in candidate_seeds):
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
        if _has_intervening_candidate_seed(seed, block, candidate_seeds):
            continue
        options.append(
            {
                "bbox": tail["bbox"],
                "confidence": tail["confidence"],
                "answer_length": tail["answer_length"],
                "answer_text": tail["tail_text"],
                "block_index": tail["block_index"],
                "block_text": tail["block_text"],
                "source": "embedded",
                "gap": max(0.0, gap),
            }
        )
    options.sort(key=lambda item: (-item["confidence"], -item["answer_length"], item["gap"]))
    return options[0] if options else None


def _find_vertical_last_row_answer(seed, blocks, candidate_seeds):
    local_blocks = []
    for block in blocks:
        if block["index"] == seed["index"] or not block["bbox"] or block["is_meta"] or block["is_instruction"]:
            continue
        if any(block["index"] == candidate_seed["index"] for candidate_seed in candidate_seeds):
            continue
        confidence = _answer_value_confidence(block["text"])
        if confidence < 2 or _answer_text_length(block["text"]) < 1:
            continue
        if abs(block["center_x"] - seed["center_x"]) > 60.0:
            continue
        if block["top"] < seed["bottom"] - 10.0 or block["top"] > seed["bottom"] + 110.0:
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
        "answer_text": selected["text"],
        "block_index": selected["index"],
        "block_text": selected["text"],
        "source": "vertical_last_row",
        "gap": 0.0,
    }


def _infer_answer_bbox(candidate, all_blocks, candidate_seeds):
    seed = candidate["seed"]
    seed["answer_confidence"] = 0
    seed["student_answer"] = ""
    seed["support_block_index"] = None
    seed["support_block_text"] = None

    same_block_tail = _answer_tail_bbox_from_block(seed)
    if same_block_tail and _bbox_is_near_seed(seed, same_block_tail["bbox"]):
        seed["answer_confidence"] = same_block_tail["confidence"]
        seed["student_answer"] = same_block_tail["tail_text"]
        seed["support_block_index"] = same_block_tail["block_index"]
        seed["support_block_text"] = same_block_tail["block_text"]
        source = "equals_right" if same_block_tail["marker"] in ("=", "＝") else "embedded"
        if same_block_tail["confidence"] < 2:
            return None, source, True
        return same_block_tail["bbox"], source, False

    options = []
    for option in (
        _find_row_value_answer(seed, all_blocks, candidate_seeds),
        _find_embedded_answer(seed, all_blocks, candidate_seeds),
        _find_vertical_last_row_answer(seed, all_blocks, candidate_seeds),
    ):
        if option and _bbox_is_near_seed(seed, option["bbox"]):
            options.append(option)
    if not options:
        return None, None, True

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
    seed["answer_confidence"] = selected["confidence"]
    seed["student_answer"] = selected["answer_text"]
    seed["support_block_index"] = selected["block_index"]
    seed["support_block_text"] = selected["block_text"]
    if selected["confidence"] < 2:
        return None, selected["source"], True
    return selected["bbox"], selected["source"], False


def _union_bbox(bboxes):
    valid = [bbox for bbox in bboxes if bbox]
    if not valid:
        return None
    x1 = min(bbox[0] for bbox in valid)
    y1 = min(bbox[1] for bbox in valid)
    x2 = max(bbox[0] + bbox[2] for bbox in valid)
    y2 = max(bbox[1] + bbox[3] for bbox in valid)
    return [round(x1, 2), round(y1, 2), round(x2 - x1, 2), round(y2 - y1, 2)]


def _make_question(seed, answer_bbox, source, needs_review, confidence):
    index = seed["question_index"]
    union_bbox = _union_bbox([seed["bbox"], answer_bbox])
    student_answer = seed.get("student_answer", "") if answer_bbox else ""
    question = {
        "question_id": f"mathocr_{index}",
        "question_number": index + 1,
        "question_text": seed["text"],
        "bbox": union_bbox,
        "answer_bbox": answer_bbox,
        "student_answer": student_answer,
        "is_correct": None,
        "needs_review": needs_review,
        "source": source,
        "confidence": confidence,
        "number": index + 1,
        "content": seed["text"],
    }
    return question


def _confidence_to_float(confidence):
    if confidence >= 2:
        return 0.95
    if confidence == 1:
        return 0.65
    return 0.0


def math_ocr_first_extract(ocr_blocks, document_classification):
    normalized, page_height = _normalize_blocks(ocr_blocks)
    meta_blocks = []
    content_blocks = []
    for block in normalized:
        block["is_meta"] = _is_meta_block(block, page_height)
        block["is_instruction"] = _is_instruction_block(block)
        block["is_math"] = _has_math_signal(block["text"])
        if block["is_meta"]:
            meta_blocks.append(block)
        if not block["is_meta"] and not block["is_instruction"]:
            content_blocks.append(block)

    candidate_seeds = sorted(
        [block for block in content_blocks if _is_base_candidate_seed(block)],
        key=lambda block: (block["top"], block["left"], block["index"]),
    )
    header_as_question = sum(
        1 for block in candidate_seeds if any(pattern.search(block["text"]) for pattern in HEADER_TEXT_PATTERNS)
    )

    questions = []
    answer_bbox_count = 0
    for index, seed in enumerate(candidate_seeds):
        seed["question_index"] = index
        answer_bbox, source, needs_review = _infer_answer_bbox({"seed": seed}, content_blocks, candidate_seeds)
        if answer_bbox is not None:
            answer_bbox_count += 1
        confidence = _confidence_to_float(seed.get("answer_confidence", 0))
        questions.append(_make_question(seed, answer_bbox, source, needs_review, confidence))

    answer_bbox_ratio = (
        answer_bbox_count / len(candidate_seeds) if candidate_seeds else 0.0
    )
    quality_gate_reasons = []
    if len(candidate_seeds) < 2:
        quality_gate_reasons.append("candidate_seed_count_lt_2")
    if answer_bbox_ratio < 0.8:
        quality_gate_reasons.append("answer_bbox_ratio_lt_0.8")
    if header_as_question:
        quality_gate_reasons.append("header_as_question")
    quality_gate_passed = not quality_gate_reasons

    stats = {
        "total_blocks": len(normalized),
        "meta_block_count": len(meta_blocks),
        "content_block_count": len(content_blocks),
        "candidate_seed_count": len(candidate_seeds),
        "answer_bbox_count": answer_bbox_count,
        "answer_bbox_ratio": round(answer_bbox_ratio, 4),
        "header_as_question": header_as_question,
        "doc_family": str((document_classification or {}).get("doc_family", "")).strip(),
        "page_type": str((document_classification or {}).get("page_type", "unknown")).strip(),
    }
    if not quality_gate_passed:
        return {
            "success": False,
            "quality_gate_passed": False,
            "questions": [],
            "stats": stats,
            "reason": ",".join(quality_gate_reasons),
        }
    return {
        "success": True,
        "quality_gate_passed": True,
        "questions": questions,
        "stats": stats,
        "reason": "ok",
    }
