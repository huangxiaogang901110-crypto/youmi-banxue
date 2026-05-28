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
    re.compile(r"练竖式算"),
    re.compile(r"课间活动"),
    re.compile(r"日期"),
    re.compile(r"姓名"),
    re.compile(r"直接写出得数"),
    re.compile(r"用竖式计算"),
    re.compile(r"改正"),
]
ORAL_SECTION_PATTERNS = [
    re.compile(r"直接写"),
    re.compile(r"练口算"),
]
VERTICAL_SECTION_PATTERNS = [
    re.compile(r"竖式"),
]
RIGHT_PAGE_HINT_PATTERNS = [
    re.compile(r"任务"),
    re.compile(r"直接写"),
    re.compile(r"竖"),
]
MATH_SIGNAL = re.compile(r"(?:\d|[+\-×xX*/÷=<>＜＞≤≥≦≧○])")
WORD_PROBLEM = re.compile(r"(？|\?|多少|几)")
ARITHMETIC_EXPR = re.compile(r"\d+\s*[+\-×xX*/÷]\s*\d")
SEED_BINARY = re.compile(
    r"(\d+\s*[+\-×xX*/÷]\s*\d+|"
    r"\d\s*[+\-×xX*/÷]\s*\d\s*[+\-×xX*/÷]\s*\d+|"
    r"\d\s*[+\-×xX*/÷]\s*\d+\s*[=＝：:])"
)
DIGIT_RE = re.compile(r"\d")
OCR_DIGITISH = re.compile(r"^[\d\s,.·'′″%oOIlZzSsBbGgQqD]+$")
PURE_CJK = re.compile(r"^[\u4e00-\u9fff，。；：、（）《》\"'？！\s]+$")
LEADING_ANSWER_NOISE = re.compile(r"^[=＝：:(（/\\\-]+")
TRAILING_QUESTION_NOISE = re.compile(r"[=＝：:/\\\-\s]+$")
TOKEN_RE = re.compile(r"\d+|[+\-×xX*/÷]")
OCR_NORMALIZE = str.maketrans({
    "O": "0",
    "o": "0",
    "I": "1",
    "l": "1",
    "Z": "2",
    "z": "2",
    "S": "5",
    "s": "5",
    "B": "8",
    "G": "6",
    "Q": "0",
    "D": "0",
})


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
    rights = []
    for index, raw_block in enumerate(ocr_blocks or []):
        text = str(raw_block.get("text", "") or "").strip()
        if not text:
            continue
        bbox = _bbox_from_block(raw_block)
        if bbox:
            rights.append(bbox[0] + bbox[2])
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
    page_width = max(rights) if rights else 1.0
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
    return normalized, page_width, page_height


def _has_math_signal(text):
    return bool(MATH_SIGNAL.search(text or ""))


def _bbox_area(bbox):
    if not bbox:
        return 0.0
    return max(0.0, bbox[2]) * max(0.0, bbox[3])


def _bbox_overlap_ratio(a, b):
    if not a or not b:
        return 0.0
    overlap_w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    overlap_h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    if overlap_w <= 0 or overlap_h <= 0:
        return 0.0
    overlap_area = overlap_w * overlap_h
    return overlap_area / max(1.0, min(_bbox_area(a), _bbox_area(b)))


def _bbox_inside_bounds(bbox, bounds):
    if not bbox or not bounds:
        return False
    x, y, w, h = bbox
    bx, by, bw, bh = bounds
    return x >= bx and y >= by and (x + w) <= (bx + bw) and (y + h) <= (by + bh)


def _distance_from_seed(seed, bbox):
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
    return max(dx, dy)


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


def _contains_operator(text):
    return bool(re.search(r"[+\-×xX*/÷]", str(text or "")))


def _is_base_candidate_seed(block):
    text = block["text"]
    if not block["bbox"] or block["is_meta"] or block["is_instruction"]:
        return False
    if text.startswith(("=", "＝", "：", ":")):
        return False
    if WORD_PROBLEM.search(text) and _has_math_signal(text):
        return True
    if not _contains_operator(text):
        return False
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
    ocr_normalized = cleaned.translate(OCR_NORMALIZE)
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
        "tail_text": tail_text,
        "answer_length": _answer_text_length(tail_text),
        "block_index": block["index"],
        "block_text": block["text"],
        "flow_is_vertical": flow_is_vertical,
    }


def _normalize_question_text(text):
    raw = str(text or "").strip()
    extracted = _extract_last_answer_tail(raw)
    if extracted is not None:
        marker_char, tail_text = extracted
        marker_index = raw.rfind(marker_char)
        if _answer_value_confidence(tail_text) >= 1 or re.fullmatch(r"[=/\\\-\s]+", tail_text or ""):
            return raw[: marker_index + 1].strip()
    return TRAILING_QUESTION_NOISE.sub("", raw) or raw


def _parse_expected_answer_text(question_text):
    normalized = _normalize_question_text(question_text)
    tokens = TOKEN_RE.findall(normalized)
    if len(tokens) < 3 or len(tokens) % 2 == 0:
        return None
    if not all(token.isdigit() for token in tokens[::2]):
        return None
    numbers = [int(token) for token in tokens[::2]]
    operators = tokens[1::2]
    if len(numbers) != (len(operators) + 1):
        return None
    total = numbers[0]
    for operator, number in zip(operators, numbers[1:]):
        if operator == "+":
            total += number
        elif operator == "-":
            total -= number
        elif operator in ("×", "x", "X", "*"):
            total *= number
        elif operator == "÷":
            if number == 0 or (total % number) != 0:
                return None
            total //= number
        else:
            return None
    return str(total)


def _expected_answer_length(question_text):
    expected = _parse_expected_answer_text(question_text)
    return len(expected) if expected is not None else None


def _expand_bbox_for_expected_length(bbox, observed_length, expected_length, *, vertical_flow=False, bounds=None):
    if not bbox or not expected_length or observed_length >= expected_length or observed_length <= 0:
        return bbox
    x, y, w, h = bbox
    if vertical_flow:
        extra_h = max(8.0, (h / max(1.0, observed_length)) * (expected_length - observed_length))
        y = y - extra_h
        h = h + extra_h
    else:
        extra_w = max(8.0, (w / max(1.0, observed_length)) * (expected_length - observed_length))
        x = x - extra_w
        w = w + extra_w
    if bounds:
        left, top, right, bottom = bounds
        x = max(left, x)
        y = max(top, y)
        w = min(right - x, w)
        h = min(bottom - y, h)
    if w <= 0 or h <= 0:
        return bbox
    return [round(x, 2), round(y, 2), round(w, 2), round(h, 2)]


def _y_overlap_ratio(seed, block):
    overlap = min(seed["bottom"], block["bottom"]) - max(seed["top"], block["top"])
    if overlap <= 0:
        return 0.0
    return overlap / min(seed["bbox"][3], block["bbox"][3])


def _bbox_is_near_seed(seed, bbox):
    return _distance_from_seed(seed, bbox) < 100.0


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
        if gap < -15.0 or gap > 80.0:
            continue
        if block["center_x"] <= seed["center_x"]:
            continue
        if _y_overlap_ratio(seed, block) < 0.40:
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
                "flow_is_vertical": False,
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
        if gap < -15.0 or gap > 60.0:
            continue
        if block["center_x"] <= seed["center_x"]:
            continue
        if _y_overlap_ratio(seed, block) < 0.40:
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
                "flow_is_vertical": tail["flow_is_vertical"],
            }
        )
    options.sort(key=lambda item: (-item["confidence"], -item["answer_length"], item["gap"]))
    return options[0] if options else None


def _find_vertical_last_row_answer(seed, blocks, candidate_seeds, expected_length=None):
    local_blocks = []
    for block in blocks:
        if block["index"] == seed["index"] or not block["bbox"] or block["is_meta"] or block["is_instruction"]:
            continue
        if any(block["index"] == candidate_seed["index"] for candidate_seed in candidate_seeds):
            continue
        confidence = _answer_value_confidence(block["text"])
        if confidence < 1 or _answer_text_length(block["text"]) < 1:
            continue
        if abs(block["center_x"] - seed["center_x"]) > 30.0:
            continue
        if block["top"] < seed["bottom"] - 10.0:
            continue
        if block["top"] > seed["bottom"] + 160.0:
            continue
        local_blocks.append(block)
    if not local_blocks:
        return None
    rows = _cluster_by_gap(local_blocks, key_name="top", gap_threshold=24.0)
    best = None
    for row_blocks in rows:
        row_blocks.sort(key=lambda block: (block["left"], block["top"]))
        row_bbox = _union_bbox([block["bbox"] for block in row_blocks])
        row_center_x = sum(block["center_x"] for block in row_blocks) / len(row_blocks)
        row_text = "".join(_clean_answer_text(block["text"]) for block in row_blocks) or row_blocks[-1]["text"]
        row_length = max(_answer_text_length(row_text), max(_answer_text_length(block["text"]) for block in row_blocks))
        row_top = min(block["top"] for block in row_blocks)
        score = max(0.0, 1.2 - (abs(row_center_x - seed["center_x"]) / 40.0))
        vertical_gap = max(0.0, row_top - seed["bottom"])
        score += max(0.0, 1.2 - (abs(vertical_gap - 70.0) / 70.0))
        if expected_length and row_length == expected_length:
            score += 1.0
        elif expected_length and row_length < expected_length:
            score -= 0.6 * (expected_length - row_length)
        option = {
            "bbox": row_bbox or row_blocks[-1]["bbox"],
            "confidence": 2,
            "answer_length": row_length,
            "answer_text": row_text,
            "block_index": row_blocks[-1]["index"],
            "block_text": row_blocks[-1]["text"],
            "source": "vertical_result",
            "gap": 0.0,
            "flow_is_vertical": False,
            "_row_score": score,
        }
        if best is None or option["_row_score"] > best["_row_score"]:
            best = option
    if best is None:
        return None
    return {
        "bbox": best["bbox"],
        "confidence": best["confidence"],
        "answer_length": best["answer_length"],
        "answer_text": best["answer_text"],
        "block_index": best["block_index"],
        "block_text": best["block_text"],
        "source": best["source"],
        "gap": best["gap"],
        "flow_is_vertical": best["flow_is_vertical"],
    }


def _section_kind_from_text(text):
    if any(pattern.search(text) for pattern in ORAL_SECTION_PATTERNS):
        return "oral"
    if any(pattern.search(text) for pattern in VERTICAL_SECTION_PATTERNS):
        return "vertical"
    return None


def _cluster_by_gap(items, *, key_name, gap_threshold):
    clusters = []
    for item in sorted(items, key=lambda entry: entry[key_name]):
        if not clusters or (item[key_name] - clusters[-1][-1][key_name]) > gap_threshold:
            clusters.append([item])
        else:
            clusters[-1].append(item)
    return clusters


def _derive_effective_roi(blocks, candidate_seeds, page_width, page_height):
    right_instruction_blocks = [
        block
        for block in blocks
        if block["bbox"]
        and block["left"] >= (page_width * 0.84)
        and any(pattern.search(block["text"]) for pattern in RIGHT_PAGE_HINT_PATTERNS)
    ]
    roi_right = page_width
    reject_rules = []
    rejected_block_indices = []
    if right_instruction_blocks:
        roi_right = min(block["left"] for block in right_instruction_blocks) - 12.0
        reject_rules.append("exclude_right_adjacent_page_cluster")
        rejected_block_indices.extend(block["index"] for block in right_instruction_blocks)

    core_blocks = [
        block
        for block in blocks
        if block["bbox"]
        and block["left"] < roi_right
        and (
            block["is_instruction"]
            or block["is_math"]
            or any(seed["index"] == block["index"] for seed in candidate_seeds)
        )
    ]
    if not core_blocks:
        core_blocks = [block for block in blocks if block["bbox"] and block["left"] < roi_right]
    if not core_blocks:
        return {
            "bbox": [0.0, 0.0, round(page_width, 2), round(page_height, 2)],
            "reject_rules": reject_rules,
            "rejected_block_indices": rejected_block_indices,
        }

    roi_left = max(0.0, min(block["left"] for block in core_blocks) - 12.0)
    roi_top = max(0.0, min(block["top"] for block in core_blocks) - 12.0)
    roi_bottom = min(page_height, max(block["bottom"] for block in core_blocks) + 12.0)
    roi_right = min(page_width, max(roi_left + 20.0, roi_right))
    return {
        "bbox": [
            round(roi_left, 2),
            round(roi_top, 2),
            round(roi_right - roi_left, 2),
            round(roi_bottom - roi_top, 2),
        ],
        "reject_rules": reject_rules,
        "rejected_block_indices": rejected_block_indices,
    }


def _block_in_roi(block, roi_bbox):
    if not block["bbox"]:
        return False
    return _bbox_inside_bounds(block["bbox"], roi_bbox)


def _build_sections(blocks, roi_bbox):
    roi_top = roi_bbox[1]
    roi_bottom = roi_bbox[1] + roi_bbox[3]
    anchors = []
    for block in blocks:
        if not block["bbox"] or not _block_in_roi(block, roi_bbox):
            continue
        kind = _section_kind_from_text(block["text"])
        if not kind:
            continue
        anchors.append(
            {
                "kind": kind,
                "title": "口算" if kind == "oral" else "竖式",
                "top": block["top"],
                "index": block["index"],
            }
        )
    anchors.sort(key=lambda item: item["top"])

    deduped = []
    for anchor in anchors:
        if deduped and deduped[-1]["kind"] == anchor["kind"] and abs(deduped[-1]["top"] - anchor["top"]) <= 40.0:
            continue
        deduped.append(anchor)

    if not deduped:
        return [
            {
                "kind": "math",
                "title": "数学",
                "top": roi_top,
                "bottom": roi_bottom,
                "index": 1,
            }
        ]

    sections = []
    for index, anchor in enumerate(deduped, start=1):
        next_anchor_top = deduped[index]["top"] if index < len(deduped) else None
        section_bottom = (next_anchor_top + 32.0) if next_anchor_top is not None else roi_bottom
        sections.append(
            {
                "kind": anchor["kind"],
                "title": anchor["title"],
                "top": max(roi_top, anchor["top"] - 8.0),
                "bottom": min(roi_bottom, section_bottom),
                "index": index,
            }
        )
    return sections


def _seed_sort_y(seed):
    same_block_tail = _answer_tail_bbox_from_block(seed)
    if same_block_tail is not None:
        return same_block_tail["bbox"][1]
    return seed["top"]


def _build_layout_context(candidate_seeds, all_blocks, roi_bbox):
    if not candidate_seeds:
        return {}

    sections = _build_sections(all_blocks, roi_bbox)
    context = {}
    group_counter = 0

    for section in sections:
        section_seeds = [
            seed
            for seed in candidate_seeds
            if seed["top"] >= section["top"] and seed["top"] <= section["bottom"]
        ]
        if not section_seeds:
            continue

        x_gap = 70.0 if section["kind"] == "oral" else 80.0
        columns = _cluster_by_gap(section_seeds, key_name="left", gap_threshold=x_gap)
        column_centers = [
            sum(seed["center_x"] for seed in column) / len(column)
            for column in columns
        ]
        row_points = sorted(_seed_sort_y(seed) for seed in section_seeds)
        row_gap = 18.0 if section["kind"] == "oral" else 34.0
        row_clusters = []
        for point in row_points:
            if not row_clusters or point - row_clusters[-1][-1] > row_gap:
                row_clusters.append([point])
            else:
                row_clusters[-1].append(point)
        row_anchors = [sum(cluster) / len(cluster) for cluster in row_clusters]

        for column_index, column in enumerate(columns):
            left_bound = roi_bbox[0] if column_index == 0 else (column_centers[column_index - 1] + column_centers[column_index]) / 2.0
            right_bound = (
                roi_bbox[0] + roi_bbox[2]
                if column_index == (len(columns) - 1)
                else (column_centers[column_index] + column_centers[column_index + 1]) / 2.0
            )
            ordered = sorted(column, key=lambda seed: (_seed_sort_y(seed), seed["top"], seed["left"], seed["index"]))
            ordered_points = [_seed_sort_y(seed) for seed in ordered]
            for position, seed in enumerate(ordered):
                group_counter += 1
                previous_point = ordered_points[position - 1] if position > 0 else None
                next_point = ordered_points[position + 1] if position + 1 < len(ordered) else None
                group_top = (
                    max(section["top"], previous_point + 10.0)
                    if previous_point is not None
                    else max(section["top"], seed["top"] - 12.0)
                )
                group_bottom = (
                    min(section["bottom"], next_point - 12.0)
                    if next_point is not None
                    else section["bottom"]
                )
                row_index = 1
                if row_anchors:
                    row_index = min(
                        range(len(row_anchors)),
                        key=lambda idx: abs(row_anchors[idx] - ordered_points[position]),
                    ) + 1
                context[seed["index"]] = {
                    "section_kind": section["kind"],
                    "section_title": section["title"],
                    "section_index": section["index"],
                    "column_index": column_index + 1,
                    "row_index": row_index,
                    "group_index": group_counter,
                    "column_bounds": (left_bound, right_bound),
                    "group_bounds": (
                        left_bound,
                        group_top,
                        right_bound - left_bound,
                        max(20.0, group_bottom - group_top),
                    ),
                    "sort_y": ordered_points[position],
                }
    return context


def _candidate_bounds(seed_context):
    left, right = seed_context["column_bounds"]
    top = seed_context["group_bounds"][1]
    bottom = top + seed_context["group_bounds"][3]
    return left, top, right, bottom


def _local_blocks_for_seed(seed, all_blocks, candidate_seeds, seed_context, roi_bbox):
    if not seed_context:
        return [block for block in all_blocks if _block_in_roi(block, roi_bbox)]
    left, top, right, bottom = _candidate_bounds(seed_context)
    local = []
    for block in all_blocks:
        if not block["bbox"] or block["is_meta"] or block["is_instruction"]:
            continue
        if not _block_in_roi(block, roi_bbox):
            continue
        if block["center_x"] < (left - 14.0) or block["center_x"] > (right + 14.0):
            continue
        if block["bottom"] < (top - 14.0) or block["top"] > (bottom + 14.0):
            continue
        local.append(block)
    local_candidate_seeds = [
        candidate_seed
        for candidate_seed in candidate_seeds
        if candidate_seed["index"] == seed["index"]
        or (
            candidate_seed["center_x"] >= (left - 14.0)
            and candidate_seed["center_x"] <= (right + 14.0)
            and candidate_seed["bottom"] >= (top - 14.0)
            and candidate_seed["top"] <= (bottom + 14.0)
        )
    ]
    return local, local_candidate_seeds


def _make_handwritten_zone(seed, seed_context, roi_bbox, expected_length):
    if not seed_context:
        return None
    left, top, right, bottom = _candidate_bounds(seed_context)
    if seed_context["section_kind"] == "vertical":
        width = min(right - left - 8.0, max(24.0, (expected_length or 2) * 18.0))
        height = 32.0
        x = min(right - width - 4.0, max(left + 4.0, seed["left"] - 6.0))
        y = max(seed["bottom"] + 8.0, bottom - height - 8.0)
    else:
        width = min(right - seed["right"] - 4.0, max(18.0, (expected_length or 1) * 18.0))
        if width < 14.0:
            return None
        height = min(36.0, max(20.0, seed["bbox"][3] * 0.75))
        x = seed["right"] + 4.0
        y = seed["top"] if seed["bbox"][3] <= (seed["bbox"][2] * 2.2) else max(seed["top"], seed["bottom"] - height)
    bbox = [round(x, 2), round(y, 2), round(width, 2), round(height, 2)]
    if not _bbox_inside_bounds(bbox, roi_bbox):
        return None
    return {
        "bbox": bbox,
        "confidence": 1,
        "answer_length": expected_length or 1,
        "answer_text": "",
        "block_index": None,
        "block_text": None,
        "source": "handwritten_zone",
        "gap": 0.0,
        "flow_is_vertical": seed_context["section_kind"] == "vertical",
    }


def _candidate_rejection_reasons(seed, option, seed_context, roi_bbox, page_area):
    bbox = option.get("bbox")
    if not bbox:
        return ["missing_bbox"]
    x, y, w, h = bbox
    reasons = []
    if w <= 0 or h <= 0:
        reasons.append("non_positive_bbox")
    if not _bbox_inside_bounds(bbox, roi_bbox):
        reasons.append("roi_outside")
    if _bbox_area(bbox) > (page_area * 0.10):
        reasons.append("area_too_large")
    if option.get("source") != "equals_right" and _bbox_overlap_ratio(seed["bbox"], bbox) > 0.92:
        reasons.append("overlaps_question")
    if option.get("source") == "handwritten_zone":
        return reasons
    if seed_context:
        left, top, right, bottom = _candidate_bounds(seed_context)
        if x < (left - 18.0) or (x + w) > (right + 18.0):
            reasons.append("column_mismatch")
        bottom_slack = 42.0 if option.get("source") == "vertical_result" else 18.0
        if y < (top - 18.0) or (y + h) > (bottom + bottom_slack):
            reasons.append("group_mismatch")
        if option.get("source") in {"right_neighbor", "embedded"} and abs((y + (h / 2.0)) - seed_context["sort_y"]) > 30.0:
            reasons.append("row_mismatch")
        if option.get("source") == "vertical_result" and y < (seed["bottom"] - 8.0):
            reasons.append("not_below_seed")
    if _distance_from_seed(seed, bbox) > 150.0:
        reasons.append("distance_too_far")
    return reasons


def _score_candidate(seed, option, seed_context, expected_length):
    source_score = {
        "equals_right": 6.0,
        "right_neighbor": 5.3,
        "embedded": 4.8,
        "vertical_result": 5.4,
        "handwritten_zone": 2.4,
    }.get(option["source"], 0.0)
    score = source_score + (option.get("confidence", 0) * 1.3)
    score += min(option.get("answer_length", 0), 3) * 0.35
    if expected_length:
        answer_length = option.get("answer_length", 0)
        if answer_length == expected_length:
            score += 0.9
        elif answer_length and answer_length < expected_length:
            score -= 1.0 * (expected_length - answer_length)
    if seed_context:
        if option["source"] in {"right_neighbor", "embedded"}:
            score += max(0.0, 1.0 - (abs((option["bbox"][1] + (option["bbox"][3] / 2.0)) - seed_context["sort_y"]) / 32.0))
        if option["source"] == "vertical_result" and seed_context["section_kind"] == "vertical":
            score += 2.4
        if option["source"] == "right_neighbor" and seed_context["section_kind"] == "vertical":
            score -= 1.6
    score -= _distance_from_seed(seed, option["bbox"]) / 120.0
    score -= option.get("gap", 0.0) / 80.0
    return round(score, 4)


def _annotate_selected_candidate(seed, option):
    seed["answer_confidence"] = option.get("confidence", 0)
    seed["student_answer"] = option.get("answer_text", "") or ""
    seed["support_block_index"] = option.get("block_index")
    seed["support_block_text"] = option.get("block_text")
    seed["answer_score"] = option.get("score", 0.0)
    seed["answer_source"] = option.get("source")


def _infer_answer_bbox(candidate, all_blocks, candidate_seeds, *, roi_bbox=None, layout_context=None, page_area=1.0):
    seed = candidate["seed"]
    seed["answer_confidence"] = 0
    seed["student_answer"] = ""
    seed["support_block_index"] = None
    seed["support_block_text"] = None
    seed["answer_score"] = 0.0
    seed["answer_source"] = None
    seed["rejected_candidates"] = []

    seed_context = (layout_context or {}).get(seed["index"])
    local_blocks, local_candidate_seeds = _local_blocks_for_seed(
        seed,
        all_blocks,
        candidate_seeds,
        seed_context,
        roi_bbox,
    ) if roi_bbox and seed_context else (all_blocks, candidate_seeds)
    expected_length = _expected_answer_length(seed["text"])
    candidate_bounds = _candidate_bounds(seed_context) if seed_context else None

    options = []
    same_block_tail = _answer_tail_bbox_from_block(seed)
    if same_block_tail and _bbox_is_near_seed(seed, same_block_tail["bbox"]):
        same_block_tail["source"] = "equals_right" if same_block_tail["marker"] in ("=", "＝") else "embedded"
        same_block_tail["bbox"] = _expand_bbox_for_expected_length(
            same_block_tail["bbox"],
            same_block_tail["answer_length"],
            expected_length,
            vertical_flow=same_block_tail["flow_is_vertical"],
            bounds=candidate_bounds,
        )
        options.append(same_block_tail)

    row_option = _find_row_value_answer(seed, local_blocks, local_candidate_seeds)
    if row_option and _bbox_is_near_seed(seed, row_option["bbox"]):
        row_option["bbox"] = _expand_bbox_for_expected_length(
            row_option["bbox"],
            row_option["answer_length"],
            expected_length,
            vertical_flow=False,
            bounds=candidate_bounds,
        )
        options.append(row_option)

    embedded_option = _find_embedded_answer(seed, local_blocks, local_candidate_seeds)
    if embedded_option and _bbox_is_near_seed(seed, embedded_option["bbox"]):
        embedded_option["bbox"] = _expand_bbox_for_expected_length(
            embedded_option["bbox"],
            embedded_option["answer_length"],
            expected_length,
            vertical_flow=embedded_option["flow_is_vertical"],
            bounds=candidate_bounds,
        )
        options.append(embedded_option)

    vertical_option = _find_vertical_last_row_answer(seed, local_blocks, local_candidate_seeds, expected_length=expected_length)
    if vertical_option and _bbox_is_near_seed(seed, vertical_option["bbox"]):
        vertical_option["bbox"] = _expand_bbox_for_expected_length(
            vertical_option["bbox"],
            vertical_option["answer_length"],
            expected_length,
            vertical_flow=False,
            bounds=candidate_bounds,
        )
        options.append(vertical_option)

    handwritten_zone = _make_handwritten_zone(seed, seed_context, roi_bbox, expected_length) if roi_bbox else None
    if handwritten_zone is not None:
        options.append(handwritten_zone)

    accepted = []
    for option in options:
        reasons = _candidate_rejection_reasons(seed, option, seed_context, roi_bbox, page_area)
        if reasons:
            seed["rejected_candidates"].append(
                {
                    "source": option.get("source"),
                    "bbox": option.get("bbox"),
                    "block_index": option.get("block_index"),
                    "reason": reasons,
                }
            )
            continue
        option["score"] = _score_candidate(seed, option, seed_context, expected_length)
        accepted.append(option)

    if not accepted:
        return None, None, True

    accepted.sort(key=lambda item: (-item["score"], -item.get("confidence", 0), item.get("gap", 0.0)))
    selected = accepted[0]
    _annotate_selected_candidate(seed, selected)
    needs_review = selected["source"] == "handwritten_zone" or selected.get("confidence", 0) < 2
    return selected["bbox"], selected["source"], needs_review


def _union_bbox(bboxes):
    valid = [bbox for bbox in bboxes if bbox]
    if not valid:
        return None
    x1 = min(bbox[0] for bbox in valid)
    y1 = min(bbox[1] for bbox in valid)
    x2 = max(bbox[0] + bbox[2] for bbox in valid)
    y2 = max(bbox[1] + bbox[3] for bbox in valid)
    return [round(x1, 2), round(y1, 2), round(x2 - x1, 2), round(y2 - y1, 2)]


def _make_question(seed, answer_bbox, needs_review, confidence, layout_info):
    index = seed["question_index"]
    question_text = _normalize_question_text(seed["text"])
    union_bbox = _union_bbox([seed["bbox"], answer_bbox])
    student_answer = seed.get("student_answer", "") if answer_bbox else ""
    question = {
        "question_id": f"mathocr_{index}",
        "question_number": index + 1,
        "question_text": question_text,
        "bbox": union_bbox,
        "answer_bbox": answer_bbox,
        "answer_bbox_source": seed.get("answer_source"),
        "answer_bbox_score": seed.get("answer_score", 0.0),
        "student_answer": student_answer,
        "is_correct": None,
        "needs_review": needs_review,
        "source": "math_ocr_first",
        "confidence": confidence,
        "number": index + 1,
        "content": question_text,
        "section_title": layout_info.get("section_title") if layout_info else None,
        "section_index": layout_info.get("section_index") if layout_info else None,
        "sub_index": layout_info.get("group_index") if layout_info else None,
        "layout_row_index": layout_info.get("row_index") if layout_info else None,
        "layout_column_index": layout_info.get("column_index") if layout_info else None,
        "layout_group_index": layout_info.get("group_index") if layout_info else None,
    }
    return question


def _confidence_to_float(confidence):
    if confidence >= 2:
        return 0.95
    if confidence == 1:
        return 0.65
    return 0.0


def math_ocr_first_extract(ocr_blocks, document_classification):
    normalized, page_width, page_height = _normalize_blocks(ocr_blocks)
    meta_blocks = []
    content_blocks = []
    for block in normalized:
        block["is_meta"] = _is_meta_block(block, page_height)
        block["is_instruction"] = _is_instruction_block(block)
        block["is_math"] = _has_math_signal(block["text"])
        if block["is_meta"]:
            meta_blocks.append(block)

    initial_candidate_seeds = sorted(
        [block for block in normalized if _is_base_candidate_seed(block)],
        key=lambda block: (block["top"], block["left"], block["index"]),
    )
    roi_result = _derive_effective_roi(normalized, initial_candidate_seeds, page_width, page_height)
    roi_bbox = roi_result["bbox"]

    for block in normalized:
        block["in_roi"] = _block_in_roi(block, roi_bbox)
        if block["in_roi"] and not block["is_meta"] and not block["is_instruction"]:
            content_blocks.append(block)

    candidate_seeds = sorted(
        [block for block in content_blocks if _is_base_candidate_seed(block)],
        key=lambda block: (block["top"], block["left"], block["index"]),
    )
    header_as_question = sum(
        1 for block in candidate_seeds if any(pattern.search(block["text"]) for pattern in HEADER_TEXT_PATTERNS)
    )
    layout_context = _build_layout_context(candidate_seeds, normalized, roi_bbox)

    questions = []
    answer_bbox_count = 0
    page_area = max(1.0, page_width * page_height)
    for index, seed in enumerate(candidate_seeds):
        seed["question_index"] = index
        layout_info = layout_context.get(seed["index"], {})
        answer_bbox, source, needs_review = _infer_answer_bbox(
            {"seed": seed},
            content_blocks,
            candidate_seeds,
            roi_bbox=roi_bbox,
            layout_context=layout_context,
            page_area=page_area,
        )
        if answer_bbox is not None:
            answer_bbox_count += 1
        confidence = _confidence_to_float(seed.get("answer_confidence", 0))
        question = _make_question(seed, answer_bbox, needs_review, confidence, layout_info)
        question["answer_bbox_source"] = source
        questions.append(question)

    answer_bbox_ratio = (answer_bbox_count / len(candidate_seeds)) if candidate_seeds else 0.0
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
        "roi_result": roi_result,
        "roi_bbox": roi_bbox,
        "section_count": len({info["section_index"] for info in layout_context.values()}),
        "group_box_count": len(questions),
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
