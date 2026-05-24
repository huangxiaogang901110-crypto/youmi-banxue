from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, Field


ARITHMETIC_SIGNS = re.compile(r"[+\-×xX*/÷=]")
ARITHMETIC_EXPR = re.compile(r"\d+\s*[+\-×xX*/÷]\s*\d+")
QUESTION_NUMBER = re.compile(
    r"^\s*(?:\(?\d{1,3}\)?[.、．)]|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|[一二三四五六七八九十]+[、．.])"
)
SECTION_HEADER = re.compile(r"^\s*[★☆]?\s*[一二三四五六七八九十]+[、．.)）]")
ENGLISH_TOKEN = re.compile(r"\b[A-Za-z]{3,}\b")
PINYIN_TOKEN = re.compile(r"\b[a-z]{1,6}\b")
META_FIELDS = re.compile(r"(日期|班级|姓名|学号|老师|家长签字|用时|得分|总分|分数)")
TITLE_KEYWORDS = re.compile(
    r"(寒假作业|暑假作业|每日一练|同步练习|专项练习|自主学习|课时|单元|口算训练|课间活动|需要几个轮子)"
)
INSTRUCTION_KEYWORDS = re.compile(
    r"(请|要求|完成|根据题意|照样子|温馨提示|在○里|在括号里|把.*?圈起来|直接写出得数|列竖式计算|打卡)"
)
FOOTER_KEYWORDS = re.compile(r"(第\d+页|共\d+页|出版社|印刷|定价|二维码|微信|抖音)")
HOMEWORK_KEYWORDS = re.compile(r"(作业|练习|题|计算|口算|列竖式|应用题|比大小|填一填|判断|选择)")
NON_HOMEWORK_KEYWORDS = re.compile(
    r"(营养成分|配料|净含量|保质期|生产日期|登录|验证码|隐私政策|注册|收银|快递|面单|listen and order)"
)
MATH_KEYWORDS = re.compile(
    r"(计算|口算|加减|乘法|除法|列竖式|应用题|比大小|填一填|判断题|选择题|得数|算式|答案)"
)
CHINESE_KEYWORDS = re.compile(
    r"(拼音|读音|组词|造句|同音字|近义词|反义词|课文|阅读|根据课文|照样子|仿写|口罩|蜘蛛开店|大象的耳朵)"
)
ENGLISH_KEYWORDS = re.compile(
    r"(listen|read|write|match|english|fill in the blanks|look and choose|circle the|order the)"
)
ANSWER_CUES = re.compile(r"(答[:：]|答案|写出|填空|括号|田字格|横线)")

SECTION_KEYWORDS = re.compile(
    r"(拼音|读音|组词|造句|同音字|近义词|反义词|阅读|课文|判断|选择|填空|写词语|仿写|连线|看图|应用题|计算|口算|竖式|比大小|写句子|补全|改错)"
)
PASSAGE_HINTS = re.compile(r"(阅读|短文|材料|根据课文|根据短文|看图|照样子|例[:：])")
BLANK_HINTS = re.compile(r"(?:_{2,}|（\s*）|\(\s*\)|□|○|横线)")
OPTION_HINTS = re.compile(r"(?:^[A-DＡ-Ｄ][.．、)]\s*|[（(][A-DＡ-Ｄ][)）])")
CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")


@dataclass
class BlockInfo:
    index: int
    text: str
    bbox: list[float] | None
    center_y: float
    center_x: float
    width_ratio: float
    height_ratio: float
    area_ratio: float
    top_ratio: float
    bottom_ratio: float
    is_meta: bool = False
    is_instruction: bool = False
    is_footer: bool = False
    is_title: bool = False
    is_question_like: bool = False
    is_answer_like: bool = False


class LayoutRegion(BaseModel):
    id: str
    label: str
    bbox: list[float]
    block_indices: list[int] = Field(default_factory=list)
    reason: str = Field(default="")


class SectionHeaderHint(BaseModel):
    section_title: str
    section_index: int | None = None
    bbox: list[float] | None = None
    block_indices: list[int] = Field(default_factory=list)


class DocumentClassification(BaseModel):
    doc_family: str = Field(default="unknown")
    page_type: str = Field(default="unknown")
    subject: str = Field(default="unknown")
    support_level: str = Field(default="partial")
    route_hint: str = Field(default="general_review")
    confidence: float = Field(default=0.0)
    reason: str = Field(default="")
    layout_regions: list[dict[str, Any]] = Field(default_factory=list)
    section_headers: list[dict[str, Any]] = Field(default_factory=list)
    meta_block_indices: list[int] = Field(default_factory=list)
    question_block_indices: list[int] = Field(default_factory=list)
    answer_block_indices: list[int] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    major_failure_reason: str = Field(default="")


def _merge_text(raw_text: str | None, question_texts: Iterable[str] | None) -> str:
    merged = []
    if raw_text:
        merged.append(raw_text)
    if question_texts:
        merged.extend(text for text in question_texts if text)
    return "\n".join(merged)


def _normalize_bbox(raw_bbox: Any) -> list[float] | None:
    if not isinstance(raw_bbox, Sequence) or len(raw_bbox) != 4:
        return None
    try:
        x, y, w, h = (float(raw_bbox[0]), float(raw_bbox[1]), float(raw_bbox[2]), float(raw_bbox[3]))
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return [x, y, w, h]


def _bbox_from_block(block: dict[str, Any]) -> list[float] | None:
    bbox = block.get("bbox")
    if bbox is None and all(key in block for key in ("x", "y", "w", "h")):
        bbox = [block.get("x", 0), block.get("y", 0), block.get("w", 0), block.get("h", 0)]
    if bbox is None and isinstance(block.get("pos"), list) and len(block["pos"]) == 4:
        bbox = block["pos"]
    return _normalize_bbox(bbox)


def _union_bboxes(bboxes: Iterable[list[float] | None]) -> list[float] | None:
    valid = [bbox for bbox in bboxes if bbox]
    if not valid:
        return None
    x1 = min(bbox[0] for bbox in valid)
    y1 = min(bbox[1] for bbox in valid)
    x2 = max(bbox[0] + bbox[2] for bbox in valid)
    y2 = max(bbox[1] + bbox[3] for bbox in valid)
    return [round(x1, 2), round(y1, 2), round(x2 - x1, 2), round(y2 - y1, 2)]


def _clamp_bbox(bbox: list[float] | None, width: float, height: float) -> list[float] | None:
    normalized = _normalize_bbox(bbox)
    if not normalized:
        return None
    x, y, w, h = normalized
    x = min(max(0.0, x), max(width - 1.0, 0.0))
    y = min(max(0.0, y), max(height - 1.0, 0.0))
    max_w = max(width - x, 1.0)
    max_h = max(height - y, 1.0)
    w = min(max(w, 1.0), max_w)
    h = min(max(h, 1.0), max_h)
    return [round(x, 2), round(y, 2), round(w, 2), round(h, 2)]


def _count_blanks(text: str) -> int:
    stripped = str(text or "")
    underscores = len(re.findall(r"_{2,}", stripped))
    empty_parens = len(re.findall(r"(?:（\s*）|\(\s*\)|\[\s*\])", stripped))
    boxes = stripped.count("□") + stripped.count("○")
    return underscores + empty_parens + boxes


def _count_cjk(text: str) -> int:
    return len(CJK_CHAR.findall(str(text or "")))


def _extract_options(text_lines: list[str]) -> list[str]:
    options = [line.strip() for line in text_lines if OPTION_HINTS.search(line.strip())]
    deduped: list[str] = []
    for option in options:
        if option and option not in deduped:
            deduped.append(option[:80])
    return deduped


def _is_marker_only(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return True
    without_marker = QUESTION_NUMBER.sub("", stripped, count=1).strip()
    return not without_marker


def _has_structural_signal(text: str, page_type: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    if _is_meta_like(stripped) or _is_footer_like(stripped):
        return False
    if BLANK_HINTS.search(stripped) or OPTION_HINTS.search(stripped):
        return True
    if PASSAGE_HINTS.search(stripped):
        return True
    if page_type == "chinese_homework":
        return _count_cjk(stripped) >= 6
    if page_type == "english_homework":
        english_tokens = ENGLISH_TOKEN.findall(stripped)
        return len(english_tokens) >= 3
    return len(stripped) >= 12


def _pick_structural_lines(lines: list[str], page_type: str) -> list[str]:
    picked: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or _is_marker_only(stripped):
            continue
        if _has_structural_signal(stripped, page_type):
            picked.append(stripped)
    return picked

def _is_question_like(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if QUESTION_NUMBER.match(stripped):
        return True
    if ARITHMETIC_EXPR.search(stripped):
        return True
    if any(token in stripped for token in ("?", "？", "＝", "=", "填空", "判断", "选择", "应用题", "比大小")):
        return True
    return False


def _is_title_like(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if TITLE_KEYWORDS.search(stripped):
        return True
    if SECTION_HEADER.match(stripped) and SECTION_KEYWORDS.search(stripped):
        return True
    if len(stripped) <= 24 and "《" in stripped:
        return True
    return False


def _is_instruction_like(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if INSTRUCTION_KEYWORDS.search(stripped):
        return True
    if PASSAGE_HINTS.search(stripped) and not QUESTION_NUMBER.match(stripped):
        return True
    return False


def _is_footer_like(text: str) -> bool:
    return bool(FOOTER_KEYWORDS.search(text.strip()))


def _is_meta_like(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if META_FIELDS.search(stripped):
        return True
    if stripped.startswith("第") and "页" in stripped:
        return True
    if TITLE_KEYWORDS.search(stripped) and len(stripped) <= 36:
        return True
    return False


def _is_non_homework_text(text: str) -> bool:
    return bool(NON_HOMEWORK_KEYWORDS.search(text.strip()))


def _build_blocks(
    ocr_blocks: list[dict[str, Any]] | None,
    *,
    image_width: float | None,
    image_height: float | None,
) -> list[BlockInfo]:
    width = float(image_width or 1000.0)
    height = float(image_height or 1400.0)
    blocks: list[BlockInfo] = []
    for index, raw_block in enumerate(ocr_blocks or []):
        text = str(raw_block.get("text", "") or "").strip()
        if not text:
            continue
        bbox = _bbox_from_block(raw_block)
        if bbox:
            x, y, w, h = bbox
        else:
            x, y, w, h = 0.0, 0.0, width, height / max(len(ocr_blocks or []), 1)
        block = BlockInfo(
            index=index,
            text=text,
            bbox=bbox,
            center_y=y + h / 2.0,
            center_x=x + w / 2.0,
            width_ratio=min(1.0, max(0.0, w / width)),
            height_ratio=min(1.0, max(0.0, h / height)),
            area_ratio=min(1.0, max(0.0, (w * h) / max(width * height, 1.0))),
            top_ratio=min(1.0, max(0.0, y / height)),
            bottom_ratio=min(1.0, max(0.0, (y + h) / height)),
        )
        block.is_title = _is_title_like(text)
        block.is_instruction = _is_instruction_like(text)
        block.is_footer = _is_footer_like(text) or block.bottom_ratio >= 0.93
        block.is_meta = _is_meta_like(text) or (block.top_ratio <= 0.18 and META_FIELDS.search(text))
        block.is_question_like = _is_question_like(text)
        block.is_answer_like = bool(ANSWER_CUES.search(text)) or ("____" in text or "（" in text or "()" in text)
        blocks.append(block)
    return blocks


def _score_subjects(text: str) -> dict[str, int]:
    return {
        "math": len(MATH_KEYWORDS.findall(text)) + len(ARITHMETIC_EXPR.findall(text)) * 2,
        "chinese": len(CHINESE_KEYWORDS.findall(text)),
        "english": len(ENGLISH_KEYWORDS.findall(text.lower())) + len(ENGLISH_TOKEN.findall(text)) // 6,
    }


def _derive_math_doc_family(text: str) -> str:
    if any(keyword in text for keyword in ("应用题", "答：", "答:", "多少本", "多少个", "多少元")):
        return "math_word_problem"
    if "竖式" in text or "列竖式" in text:
        return "math_vertical"
    if "比大小" in text or "判断题" in text or "选择题" in text:
        return "math_comparison_logic"
    if "一半" in text or "平均分" in text or "看图" in text:
        return "math_visual_concept"
    return "math_arithmetic"


def _collect_section_headers(blocks: list[BlockInfo]) -> list[SectionHeaderHint]:
    hints: list[SectionHeaderHint] = []
    seq = 0
    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        if SECTION_HEADER.match(text) and SECTION_KEYWORDS.search(text):
            seq += 1
            hints.append(
                SectionHeaderHint(
                    section_title=text[:60],
                    section_index=seq,
                    bbox=block.bbox,
                    block_indices=[block.index],
                )
            )
        elif block.is_title and (block.is_question_like or SECTION_KEYWORDS.search(text)):
            seq += 1
            hints.append(
                SectionHeaderHint(
                    section_title=text[:60],
                    section_index=seq,
                    bbox=block.bbox,
                    block_indices=[block.index],
                )
            )
    return hints


def _build_layout_regions(blocks: list[BlockInfo]) -> list[LayoutRegion]:
    if not blocks:
        return []
    grouped: dict[str, list[BlockInfo]] = {
        "header": [],
        "instruction": [],
        "question_region": [],
        "answer_region": [],
        "footer": [],
        "unknown_region": [],
    }
    for block in blocks:
        if block.is_footer:
            grouped["footer"].append(block)
        elif block.is_meta or block.is_title or block.top_ratio <= 0.12 and not block.is_question_like:
            grouped["header"].append(block)
        elif block.is_instruction and not block.is_question_like:
            grouped["instruction"].append(block)
        elif block.is_question_like:
            grouped["question_region"].append(block)
        elif block.is_answer_like:
            grouped["answer_region"].append(block)
        else:
            grouped["unknown_region"].append(block)

    regions: list[LayoutRegion] = []
    for label, region_blocks in grouped.items():
        bbox = _union_bboxes(block.bbox for block in region_blocks)
        if not bbox:
            continue
        regions.append(
            LayoutRegion(
                id=f"layout-{label}",
                label=label,
                bbox=bbox,
                block_indices=[block.index for block in region_blocks],
                reason=label,
            )
        )
    return regions


def _trim_to_question_anchor(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    kept: list[str] = []
    for line in lines:
        if _is_meta_like(line) or _is_instruction_like(line) or _is_footer_like(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def _bbox_overlaps_region(bbox: list[float] | None, region_bbox: list[float] | None) -> bool:
    if not bbox or not region_bbox:
        return False
    x1, y1, w1, h1 = bbox
    x2, y2, w2, h2 = region_bbox
    l1, t1, r1, b1 = x1, y1, x1 + w1, y1 + h1
    l2, t2, r2, b2 = x2, y2, x2 + w2, y2 + h2
    return max(l1, l2) < min(r1, r2) and max(t1, t2) < min(b1, b2)


def classify_document(
    raw_text: str | None = None,
    question_texts: Iterable[str] | None = None,
    *,
    ocr_blocks: list[dict[str, Any]] | None = None,
    image_width: int | None = None,
    image_height: int | None = None,
) -> DocumentClassification:
    merged = _merge_text(raw_text, question_texts)
    text = merged.strip()
    lowered = text.lower()
    sign_count = len(ARITHMETIC_SIGNS.findall(text))
    blocks = _build_blocks(ocr_blocks, image_width=image_width, image_height=image_height)
    layout_regions = _build_layout_regions(blocks)
    section_headers = _collect_section_headers(blocks)

    non_homework_hits = sum(1 for block in blocks if _is_non_homework_text(block.text))
    meta_indices = [block.index for block in blocks if block.is_meta or block.is_title]
    question_indices = [block.index for block in blocks if block.is_question_like]
    answer_indices = [block.index for block in blocks if block.is_answer_like]
    subject_scores = _score_subjects(text)
    subject = max(subject_scores, key=subject_scores.get) if any(subject_scores.values()) else "unknown"
    page_type = "unknown"
    support_level = "partial"
    route_hint = "general_review"
    doc_family = "unknown"
    confidence = 0.35
    reason = "未命中稳定模板，按保守模式处理。"
    major_failure_reason = ""

    if non_homework_hits >= 2 or ("营养成分" in text and sign_count == 0):
        page_type = "non_homework"
        support_level = "unsupported"
        route_hint = "reject_non_homework"
        confidence = 0.92
        reason = "命中非作业内容特征，按非作业页处理。"
        major_failure_reason = "non_homework_page"
    elif len(question_indices) == 0 and (len(meta_indices) >= max(2, len(blocks) // 3) or TITLE_KEYWORDS.search(text)):
        page_type = "cover_or_instruction_page"
        support_level = "unsupported"
        route_hint = "reject_cover_page"
        confidence = 0.88
        reason = "页面以标题、页眉或说明为主，按封面/说明页处理。"
        major_failure_reason = "cover_or_instruction_page"
    elif subject_scores["math"] > 0 and subject_scores["chinese"] > 0:
        page_type = "mixed_homework"
        subject = "mixed"
        support_level = "partial"
        route_hint = "mixed_review"
        doc_family = "unknown"
        confidence = 0.56
        reason = "同时命中数学与语文特征，先按混合作业页处理。"
    elif subject_scores["math"] > 0 and subject_scores["english"] > 0:
        page_type = "mixed_homework"
        subject = "mixed"
        support_level = "partial"
        route_hint = "mixed_review"
        doc_family = "unknown"
        confidence = 0.56
        reason = "同时命中数学与英语特征，先按混合作业页处理。"
    elif subject == "math" and (sign_count >= 4 or len(question_indices) >= 3 or HOMEWORK_KEYWORDS.search(text)):
        page_type = "math_homework"
        support_level = "full"
        route_hint = "math_rule_first"
        doc_family = _derive_math_doc_family(text)
        confidence = 0.84
        reason = "命中数学计算/题号/作业关键词，按数学作业页处理。"
    elif subject == "chinese" and subject_scores["chinese"] > 0:
        page_type = "chinese_homework"
        support_level = "partial"
        route_hint = "language_review"
        doc_family = "chinese_language"
        confidence = 0.76
        reason = "命中语文练习关键词，按语文作业页分流。"
    elif subject == "english" and subject_scores["english"] > 0:
        page_type = "english_homework"
        support_level = "partial"
        route_hint = "language_review"
        doc_family = "english_language"
        confidence = 0.73
        reason = "命中英语练习关键词，按英语作业页分流。"
    elif not text:
        page_type = "unknown"
        support_level = "unsupported"
        route_hint = "empty_page"
        confidence = 0.1
        reason = "OCR 未得到有效文本，按未知页处理。"
        major_failure_reason = "empty_text"
    elif len(question_indices) == 0:
        page_type = "unknown"
        support_level = "unsupported"
        route_hint = "general_review"
        confidence = 0.28
        reason = "未识别到稳定题目区，按未知页保守处理。"
        major_failure_reason = "no_question_region"
    else:
        page_type = "unknown"
        support_level = "partial"
        route_hint = "general_review"
        confidence = 0.42
        reason = "识别到部分题目特征，但页型仍不稳定。"

    stats = {
        "block_count": len(blocks),
        "meta_block_count": len(meta_indices),
        "question_block_count": len(question_indices),
        "answer_block_count": len(answer_indices),
    }
    return DocumentClassification(
        doc_family=doc_family,
        page_type=page_type,
        subject=subject,
        support_level=support_level,
        route_hint=route_hint,
        confidence=confidence,
        reason=reason,
        layout_regions=[region.model_dump() for region in layout_regions],
        section_headers=[header.model_dump() for header in section_headers],
        meta_block_indices=meta_indices,
        question_block_indices=question_indices,
        answer_block_indices=answer_indices,
        stats=stats,
        major_failure_reason=major_failure_reason,
    )


def should_extract_questions(document_classification: dict[str, Any] | DocumentClassification | None) -> bool:
    if not document_classification:
        return False
    page_type = (
        document_classification.page_type
        if isinstance(document_classification, DocumentClassification)
        else str(document_classification.get("page_type", "unknown"))
    )
    return page_type == "math_homework"


def should_extract_structural_questions(
    document_classification: dict[str, Any] | DocumentClassification | None,
) -> bool:
    if not document_classification:
        return False
    page_type = (
        document_classification.page_type
        if isinstance(document_classification, DocumentClassification)
        else str(document_classification.get("page_type", "unknown"))
    )
    return page_type in {"chinese_homework", "english_homework"}


def _get_region_bbox(
    document_classification: dict[str, Any] | DocumentClassification | None,
    label: str,
) -> list[float] | None:
    if not document_classification:
        return None
    regions = (
        document_classification.layout_regions
        if isinstance(document_classification, DocumentClassification)
        else document_classification.get("layout_regions", [])
    ) or []
    return next((_normalize_bbox(region.get("bbox")) for region in regions if region.get("label") == label), None)


def _build_structural_candidate_blocks(
    raw_blocks: list[dict[str, Any]],
    document_classification: dict[str, Any] | DocumentClassification | None,
    *,
    image_width: int | None,
    image_height: int | None,
) -> list[BlockInfo]:
    blocks = _build_blocks(raw_blocks, image_width=image_width, image_height=image_height)
    if not blocks:
        return []
    question_region = _get_region_bbox(document_classification, "question_region")
    answer_region = _get_region_bbox(document_classification, "answer_region")
    unknown_region = _get_region_bbox(document_classification, "unknown_region")
    header_region = _get_region_bbox(document_classification, "header")
    instruction_region = _get_region_bbox(document_classification, "instruction")
    footer_region = _get_region_bbox(document_classification, "footer")
    page_type = (
        document_classification.page_type
        if isinstance(document_classification, DocumentClassification)
        else str((document_classification or {}).get("page_type", "unknown"))
    )
    meta_indices = set(
        document_classification.meta_block_indices
        if isinstance(document_classification, DocumentClassification)
        else (document_classification or {}).get("meta_block_indices", [])
    )

    def _collect(region_labels: set[str] | None = None) -> list[BlockInfo]:
        collected: list[BlockInfo] = []
        for block in blocks:
            if block.index in meta_indices:
                continue
            if _bbox_overlaps_region(block.bbox, header_region) or _bbox_overlaps_region(block.bbox, instruction_region) or _bbox_overlaps_region(block.bbox, footer_region):
                continue
            if block.is_footer:
                continue
            if region_labels:
                in_question = "question_region" in region_labels and _bbox_overlaps_region(block.bbox, question_region)
                in_answer = "answer_region" in region_labels and _bbox_overlaps_region(block.bbox, answer_region)
                in_unknown = "unknown_region" in region_labels and _bbox_overlaps_region(block.bbox, unknown_region)
                if not (in_question or in_answer or in_unknown):
                    continue
            collected.append(block)
        collected.sort(key=lambda item: (item.center_y, item.center_x))
        return collected

    primary_regions = {"question_region", "answer_region"} if (question_region or answer_region) else None
    candidates = _collect(primary_regions)
    candidate_char_count = sum(len(block.text.strip()) for block in candidates)
    meaningful_count = sum(1 for block in candidates if _has_structural_signal(block.text, page_type))
    region_too_tight = len(candidates) < 3 or candidate_char_count < 32 or meaningful_count == 0
    if region_too_tight and unknown_region:
        widened = _collect({"question_region", "answer_region", "unknown_region"})
        widened_char_count = sum(len(block.text.strip()) for block in widened)
        widened_meaningful = sum(1 for block in widened if _has_structural_signal(block.text, page_type))
        if len(widened) > len(candidates) and (widened_char_count > candidate_char_count or widened_meaningful > meaningful_count):
            candidates = widened
            candidate_char_count = widened_char_count
            meaningful_count = widened_meaningful
    if (len(candidates) < 3 or candidate_char_count < 32 or meaningful_count == 0) and page_type in {"chinese_homework", "english_homework"}:
        candidates = _collect(None)
    return candidates


def _split_structural_sections(
    candidate_blocks: list[BlockInfo],
    section_headers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not candidate_blocks:
        return []
    normalized_headers: list[dict[str, Any]] = []
    for raw_header in section_headers or []:
        bbox = _normalize_bbox(raw_header.get("bbox"))
        if not bbox:
            continue
        normalized_headers.append(
            {
                "section_title": str(raw_header.get("section_title") or "").strip(),
                "section_index": raw_header.get("section_index"),
                "block_indices": set(raw_header.get("block_indices") or []),
                "top": bbox[1],
            }
        )
    normalized_headers.sort(key=lambda header: header["top"])
    if not normalized_headers:
        return [{
            "section_title": None,
            "section_index": 1,
            "blocks": candidate_blocks,
            "header_block_indices": set(),
        }]

    sections: list[dict[str, Any]] = []
    for index, header in enumerate(normalized_headers):
        top = header["top"]
        next_top = normalized_headers[index + 1]["top"] if index + 1 < len(normalized_headers) else float("inf")
        section_blocks = [
            block
            for block in candidate_blocks
            if block.center_y >= top and block.center_y < next_top and block.index not in header["block_indices"]
        ]
        if not section_blocks:
            continue
        sections.append(
            {
                "section_title": header["section_title"] or None,
                "section_index": header["section_index"] or (index + 1),
                "blocks": section_blocks,
                "header_block_indices": header["block_indices"],
            }
        )

    if sections:
        return sections
    return [{
        "section_title": None,
        "section_index": 1,
        "blocks": candidate_blocks,
        "header_block_indices": set(),
    }]


def _extract_passage_text(lines: list[str], page_type: str) -> str | None:
    passage_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or QUESTION_NUMBER.match(stripped):
            continue
        if PASSAGE_HINTS.search(stripped) or (_has_structural_signal(stripped, page_type) and len(stripped) >= 14):
            passage_lines.append(stripped)
    if not passage_lines:
        return None
    return "\n".join(passage_lines[:4])[:240]


def extract_structured_questions_from_ocr(
    raw_blocks: list[dict[str, Any]],
    document_classification: dict[str, Any] | DocumentClassification | None,
    *,
    image_width: int | None = None,
    image_height: int | None = None,
) -> list[dict[str, Any]]:
    if not should_extract_structural_questions(document_classification):
        return []
    candidate_blocks = _build_structural_candidate_blocks(
        raw_blocks,
        document_classification,
        image_width=image_width,
        image_height=image_height,
    )
    if not candidate_blocks:
        return []
    section_headers = (
        document_classification.section_headers
        if isinstance(document_classification, DocumentClassification)
        else (document_classification or {}).get("section_headers", [])
    ) or []
    sections = _split_structural_sections(candidate_blocks, section_headers)
    page_type = (
        document_classification.page_type
        if isinstance(document_classification, DocumentClassification)
        else str((document_classification or {}).get("page_type", "unknown"))
    )
    width = float(image_width or 1000.0)
    height = float(image_height or 1400.0)
    results: list[dict[str, Any]] = []
    running_number = 1

    for section in sections:
        section_title = section.get("section_title")
        section_index = section.get("section_index")
        section_blocks: list[BlockInfo] = section.get("blocks") or []
        if not section_blocks:
            continue
        if section_title and (_is_meta_like(section_title) or _is_instruction_like(section_title) or _is_footer_like(section_title)):
            continue
        marker_indices = [
            idx
            for idx, block in enumerate(section_blocks)
            if QUESTION_NUMBER.match(block.text.strip()) and not SECTION_HEADER.match(block.text.strip())
        ]
        section_lines = [block.text.strip() for block in section_blocks if block.text.strip()]
        context_text = _extract_passage_text(section_lines, page_type)

        if marker_indices:
            boundaries = marker_indices + [len(section_blocks)]
            for boundary_index, marker_idx in enumerate(marker_indices):
                next_idx = boundaries[boundary_index + 1]
                span_blocks = section_blocks[marker_idx:next_idx]
                span_lines = [block.text.strip() for block in span_blocks if block.text.strip()]
                prompt_lines = _pick_structural_lines(span_lines, page_type)
                if section_title and prompt_lines and prompt_lines[0] == section_title:
                    prompt_lines = prompt_lines[1:]
                question_text = clean_question_text("\n".join(prompt_lines), document_classification)
                bbox = _clamp_bbox(_union_bboxes(block.bbox for block in span_blocks), width, height)
                if _is_marker_only(question_text):
                    continue
                if not question_text:
                    continue
                if should_drop_candidate_question(question_text, bbox, document_classification):
                    continue
                options = _extract_options(span_lines)
                context_for_question = context_text
                if prompt_lines and context_text and prompt_lines[0] == context_text:
                    context_for_question = None
                results.append(
                    {
                        "question_number": running_number,
                        "question_text": question_text[:240],
                        "question_role": "subquestion",
                        "context_text": context_for_question,
                        "options": options or None,
                        "blank_count": _count_blanks(question_text),
                        "bbox": bbox,
                        "section_title": section_title,
                        "section_index": section_index,
                        "sub_index": boundary_index + 1,
                    }
                )
                running_number += 1
            if results:
                continue

        content_lines = _pick_structural_lines(section_lines, page_type)
        if section_title and content_lines and content_lines[0] == section_title:
            content_lines = content_lines[1:]
        question_text = clean_question_text("\n".join(content_lines), document_classification)
        if not question_text and section_title and SECTION_KEYWORDS.search(section_title):
            question_text = section_title
        if not question_text:
            continue
        bbox = _clamp_bbox(_union_bboxes(block.bbox for block in section_blocks), width, height)
        if _is_marker_only(question_text):
            continue
        if should_drop_candidate_question(question_text, bbox, document_classification):
            continue
        options = _extract_options(content_lines)
        results.append(
            {
                "question_number": running_number,
                "question_text": question_text[:240],
                "question_role": "section_prompt",
                "context_text": context_text,
                "options": options or None,
                "blank_count": _count_blanks(question_text),
                "bbox": bbox,
                "section_title": section_title,
                "section_index": section_index,
                "sub_index": 1 if section_index is not None else None,
            }
        )
        running_number += 1

    return results



def filter_ocr_blocks_for_question_region(
    raw_blocks: list[dict[str, Any]],
    document_classification: dict[str, Any] | DocumentClassification | None,
) -> list[dict[str, Any]]:
    if not raw_blocks:
        return []
    if not document_classification:
        return raw_blocks
    regions = (
        document_classification.layout_regions
        if isinstance(document_classification, DocumentClassification)
        else document_classification.get("layout_regions", [])
    ) or []
    question_region = next((region for region in regions if region.get("label") == "question_region"), None)
    if not question_region:
        return raw_blocks
    question_bbox = _normalize_bbox(question_region.get("bbox"))
    meta_indices = set(
        document_classification.meta_block_indices
        if isinstance(document_classification, DocumentClassification)
        else document_classification.get("meta_block_indices", [])
    )
    filtered: list[dict[str, Any]] = []
    for index, block in enumerate(raw_blocks):
        if index in meta_indices:
            continue
        bbox = _bbox_from_block(block)
        if not bbox or _bbox_overlaps_region(bbox, question_bbox):
            filtered.append(block)
    return filtered


def clean_question_text(text: str, document_classification: dict[str, Any] | DocumentClassification | None) -> str:
    cleaned = _trim_to_question_anchor(str(text or ""))
    if not document_classification:
        return cleaned
    section_headers = (
        document_classification.section_headers
        if isinstance(document_classification, DocumentClassification)
        else document_classification.get("section_headers", [])
    ) or []
    for header in section_headers:
        title = str(header.get("section_title") or "").strip()
        if title and cleaned.replace(" ", "") == title.replace(" ", ""):
            return ""
    return cleaned.strip()


def should_drop_candidate_question(
    text: str,
    bbox: list[float] | None,
    document_classification: dict[str, Any] | DocumentClassification | None,
) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return True
    if _is_meta_like(stripped) or _is_instruction_like(stripped) or _is_footer_like(stripped):
        return True
    if document_classification:
        regions = (
            document_classification.layout_regions
            if isinstance(document_classification, DocumentClassification)
            else document_classification.get("layout_regions", [])
        ) or []
        header_bbox = next((_normalize_bbox(region.get("bbox")) for region in regions if region.get("label") == "header"), None)
        instruction_bbox = next((_normalize_bbox(region.get("bbox")) for region in regions if region.get("label") == "instruction"), None)
        footer_bbox = next((_normalize_bbox(region.get("bbox")) for region in regions if region.get("label") == "footer"), None)
        if _bbox_overlaps_region(bbox, header_bbox) or _bbox_overlaps_region(bbox, instruction_bbox) or _bbox_overlaps_region(bbox, footer_bbox):
            return True
    return False


def assign_question_sections(
    questions: list[Any],
    document_classification: dict[str, Any] | DocumentClassification | None,
) -> list[Any]:
    if not questions or not document_classification:
        return questions
    section_headers = (
        document_classification.section_headers
        if isinstance(document_classification, DocumentClassification)
        else document_classification.get("section_headers", [])
    ) or []
    if not section_headers:
        return questions

    normalized_headers = []
    for header in section_headers:
        bbox = _normalize_bbox(header.get("bbox"))
        if not bbox:
            continue
        normalized_headers.append(
            {
                "section_title": str(header.get("section_title") or "").strip(),
                "section_index": header.get("section_index"),
                "top": bbox[1],
            }
        )
    normalized_headers.sort(key=lambda header: header["top"])
    if not normalized_headers:
        return questions

    section_counts: dict[int, int] = {}
    for question in sorted(questions, key=lambda item: (getattr(item, "bbox", None) or [0, 10**9, 0, 0])[1]):
        bbox = getattr(question, "bbox", None)
        q_top = bbox[1] if bbox else 10**9
        chosen = normalized_headers[0]
        for header in normalized_headers:
            if header["top"] <= q_top:
                chosen = header
            else:
                break
        section_index = chosen.get("section_index")
        if section_index is None:
            continue
        section_counts[section_index] = section_counts.get(section_index, 0) + 1
        setattr(question, "section_title", chosen["section_title"] or getattr(question, "section_title", None))
        setattr(question, "section_index", section_index)
        setattr(question, "sub_index", section_counts[section_index])
    return questions


def summarize_question_alignment(
    questions: list[Any],
    document_classification: dict[str, Any] | DocumentClassification | None,
    *,
    image_width: int | None = None,
    image_height: int | None = None,
) -> dict[str, Any]:
    if not document_classification:
        return {
            "meta_like_question_count": 0,
            "header_question_count": 0,
            "instruction_question_count": 0,
            "footer_question_count": 0,
            "question_region_hit_count": 0,
            "section_nonempty_count": 0,
            "bbox_negative_count": 0,
            "bbox_giant_count": 0,
        }
    regions = (
        document_classification.layout_regions
        if isinstance(document_classification, DocumentClassification)
        else document_classification.get("layout_regions", [])
    ) or []
    question_bbox = next((_normalize_bbox(region.get("bbox")) for region in regions if region.get("label") == "question_region"), None)
    header_bbox = next((_normalize_bbox(region.get("bbox")) for region in regions if region.get("label") == "header"), None)
    instruction_bbox = next((_normalize_bbox(region.get("bbox")) for region in regions if region.get("label") == "instruction"), None)
    footer_bbox = next((_normalize_bbox(region.get("bbox")) for region in regions if region.get("label") == "footer"), None)
    width = float(image_width or 1000.0)
    height = float(image_height or 1400.0)
    stats = {
        "meta_like_question_count": 0,
        "header_question_count": 0,
        "instruction_question_count": 0,
        "footer_question_count": 0,
        "question_region_hit_count": 0,
        "section_nonempty_count": 0,
        "bbox_negative_count": 0,
        "bbox_giant_count": 0,
    }
    for question in questions:
        text = str(getattr(question, "question_text", "") or "").strip()
        bbox = getattr(question, "bbox", None)
        if _is_meta_like(text) or _is_instruction_like(text) or _is_footer_like(text):
            stats["meta_like_question_count"] += 1
        if _bbox_overlaps_region(bbox, header_bbox):
            stats["header_question_count"] += 1
        if _bbox_overlaps_region(bbox, instruction_bbox):
            stats["instruction_question_count"] += 1
        if _bbox_overlaps_region(bbox, footer_bbox):
            stats["footer_question_count"] += 1
        if _bbox_overlaps_region(bbox, question_bbox):
            stats["question_region_hit_count"] += 1
        if getattr(question, "section_title", None) or getattr(question, "section_index", None) is not None or getattr(question, "sub_index", None) is not None:
            stats["section_nonempty_count"] += 1
        if bbox:
            x, y, w, h = bbox
            if x < 0 or y < 0:
                stats["bbox_negative_count"] += 1
            if (w * h) > (width * height * 0.6):
                stats["bbox_giant_count"] += 1
    return stats
