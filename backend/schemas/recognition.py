from __future__ import annotations

import math
import re
from typing import Any, Optional

from pydantic import BaseModel, Field


NON_QUESTION_KINDS = {
    "meta", "section", "ignored", "ignore", "header", "footer",
    "title", "summary", "instruction", "instructions", "note",
}
SECTION_LIKE_TEXT = re.compile(r"^\s*[一二三四五六七八九十]+[、．.]")
SECTION_KEYWORDS = re.compile(r"填一填|选择|口算|计算|列式|连一连|判断|看图|写出|根据题意|班级|姓名|日期|总分|评分")


def identity_transform_matrix() -> list[list[float]]:
    return [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ]


def normalize_bbox(raw_bbox) -> list[float] | None:
    if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
        return None
    try:
        bbox = [float(v) for v in raw_bbox]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in bbox):
        return None
    _, _, w, h = bbox
    if w <= 0 or h <= 0:
        return None
    if bbox == [0.0, 0.0, 0.0, 0.0]:
        return None
    return bbox


def normalize_confidence(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(conf):
        return None
    if conf < 0:
        return 0.0
    if conf > 1:
        return 1.0
    return conf


def should_drop_question_payload(raw_question: dict) -> bool:
    kind = str(raw_question.get("kind") or raw_question.get("type") or raw_question.get("question_type") or "").strip().lower()
    if kind in NON_QUESTION_KINDS:
        return True

    text = str(raw_question.get("content") or raw_question.get("question_text") or "").strip()
    if not text:
        return True

    section_title = raw_question.get("section_title")
    if isinstance(section_title, str):
        normalized_section = section_title.strip().replace(" ", "")
        if normalized_section and text.replace(" ", "") == normalized_section:
            return True

    if SECTION_LIKE_TEXT.match(text) and SECTION_KEYWORDS.search(text):
        return True

    return False


class RecognitionNode(BaseModel):
    id: str
    text: Optional[str] = None
    bbox: Optional[list[float]] = None
    confidence: Optional[float] = None
    source: str = Field(default="unknown")
    kind: str = Field(default="unknown")
    parent_id: Optional[str] = None
    status: str = Field(default="pending")
    error_code: Optional[str] = None


class PreprocessVersion(RecognitionNode):
    kind: str = Field(default="preprocess_version")
    status: str = Field(default="completed")
    image_path: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    transform_matrix: list[list[float]] = Field(default_factory=identity_transform_matrix)
    operations: list[dict[str, Any]] = Field(default_factory=list)
    notes: Optional[str] = None


class RecognitionImage(RecognitionNode):
    kind: str = Field(default="image")
    status: str = Field(default="completed")
    file_path: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fingerprint: Optional[str] = None
    preprocess_versions: list[PreprocessVersion] = Field(default_factory=list)


class RecognitionBlock(RecognitionNode):
    kind: str = Field(default="ocr_block")
    text: str = Field(default="")
    line_index: Optional[int] = None
    block_index: Optional[int] = None


class RecognitionRegion(RecognitionNode):
    kind: str = Field(default="region")
    label: Optional[str] = None


class RecognitionQuestionGroup(RecognitionNode):
    kind: str = Field(default="question_group")
    title: Optional[str] = None
    question_ids: list[str] = Field(default_factory=list)


class RecognitionQuestionContract(BaseModel):
    id: Optional[str] = None
    question_id: str
    question_number: int
    question_text: str
    kind: str = Field(default="question")
    question_role: Optional[str] = None
    context_text: Optional[str] = None
    options: Optional[list[str]] = None
    blank_count: Optional[int] = None
    bbox: Optional[list[float]] = None
    answer_bbox: Optional[list[float]] = None
    image_url: Optional[str] = None
    crop_url: Optional[str] = None
    visual_description: Optional[str] = None
    status: str = Field(default="completed")
    student_answer: Optional[str] = None
    is_correct: Optional[bool] = None
    grading_explanation: Optional[str] = None
    section_title: Optional[str] = None
    section_index: Optional[int] = None
    sub_index: Optional[int] = None
    source: str = Field(default="unknown")
    confidence: Optional[float] = None
    parent_id: Optional[str] = None
    error_code: Optional[str] = None

    def model_post_init(self, __context) -> None:
        if self.id is None:
            self.id = self.question_id
        self.bbox = normalize_bbox(self.bbox)
        self.answer_bbox = normalize_bbox(self.answer_bbox)
        self.confidence = normalize_confidence(self.confidence)
        if self.student_answer is not None:
            student_answer = str(self.student_answer).strip()
            self.student_answer = student_answer or None


class RecognitionAnswer(RecognitionNode):
    kind: str = Field(default="answer")
    question_id: str
    student_answer: Optional[str] = None
    standard_answer: Optional[str] = None
    answer_bbox: Optional[list[float]] = None
    is_correct: Optional[bool] = None

    def model_post_init(self, __context) -> None:
        self.answer_bbox = normalize_bbox(self.answer_bbox)
        self.confidence = normalize_confidence(self.confidence)


class RecognitionGrading(RecognitionNode):
    kind: str = Field(default="grading")
    question_id: str
    result: Optional[str] = None
    explanation: Optional[str] = None
    grader_source: Optional[str] = None


class RecognitionOverlayMark(RecognitionNode):
    kind: str = Field(default="overlay")
    question_id: str
    mark_type: str = Field(default="unknown")
    mark_bbox: Optional[list[float]] = None

    def model_post_init(self, __context) -> None:
        self.mark_bbox = normalize_bbox(self.mark_bbox or self.bbox)
        self.bbox = self.mark_bbox


class RecognitionDocument(BaseModel):
    image: RecognitionImage
    blocks: list[RecognitionBlock] = Field(default_factory=list)
    regions: list[RecognitionRegion] = Field(default_factory=list)
    question_groups: list[RecognitionQuestionGroup] = Field(default_factory=list)
    questions: list[RecognitionQuestionContract] = Field(default_factory=list)
    answers: list[RecognitionAnswer] = Field(default_factory=list)
    grading: list[RecognitionGrading] = Field(default_factory=list)
    overlay: list[RecognitionOverlayMark] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


def _union_bboxes(bboxes: list[list[float]]) -> list[float] | None:
    valid = [bbox for bbox in (normalize_bbox(bbox) for bbox in bboxes) if bbox is not None]
    if not valid:
        return None
    x1 = min(bbox[0] for bbox in valid)
    y1 = min(bbox[1] for bbox in valid)
    x2 = max(bbox[0] + bbox[2] for bbox in valid)
    y2 = max(bbox[1] + bbox[3] for bbox in valid)
    return [x1, y1, x2 - x1, y2 - y1]


def build_question_contract(
    *,
    question_id: str,
    question_number: int,
    question_text: str,
    bbox=None,
    answer_bbox=None,
    image_url: str | None = None,
    student_answer: str | None = None,
    source: str = "unknown",
    confidence=None,
    parent_id: str | None = None,
    section_title: str | None = None,
    section_index: int | None = None,
    sub_index: int | None = None,
    visual_description: str | None = None,
    status: str = "completed",
) -> RecognitionQuestionContract:
    return RecognitionQuestionContract(
        question_id=question_id,
        question_number=question_number,
        question_text=question_text,
        bbox=bbox,
        answer_bbox=answer_bbox,
        image_url=image_url,
        student_answer=student_answer,
        source=source,
        confidence=confidence,
        parent_id=parent_id,
        section_title=section_title,
        section_index=section_index,
        sub_index=sub_index,
        visual_description=visual_description,
        status=status,
    )


def build_overlay_mark(question: RecognitionQuestionContract) -> RecognitionOverlayMark | None:
    overlay_bbox = question.answer_bbox or question.bbox
    overlay_bbox = normalize_bbox(overlay_bbox)
    if overlay_bbox is None:
        return None
    mark_type = "unknown"
    if question.is_correct is True:
        mark_type = "correct"
    elif question.is_correct is False:
        mark_type = "incorrect"
    return RecognitionOverlayMark(
        id=f"overlay-{question.question_id}",
        question_id=question.question_id,
        text=question.question_text,
        bbox=overlay_bbox,
        mark_bbox=overlay_bbox,
        confidence=question.confidence,
        source=question.source,
        mark_type=mark_type,
        parent_id=question.id,
        status=question.status,
        error_code=question.error_code,
    )


def build_blocks_from_ocr_blocks(
    image_id: str,
    raw_blocks: list[dict[str, Any]],
    *,
    source: str = "ocr_unknown",
) -> list[RecognitionBlock]:
    blocks: list[RecognitionBlock] = []
    for index, raw_block in enumerate(raw_blocks):
        bbox = raw_block.get("bbox")
        if bbox is None and all(key in raw_block for key in ("x", "y", "w", "h")):
            bbox = [raw_block.get("x", 0), raw_block.get("y", 0), raw_block.get("w", 0), raw_block.get("h", 0)]
        if bbox is None and isinstance(raw_block.get("pos"), list) and len(raw_block["pos"]) == 4:
            bbox = raw_block["pos"]
        blocks.append(RecognitionBlock(
            id=f"{image_id}-block-{index}",
            text=str(raw_block.get("text", "")),
            bbox=normalize_bbox(bbox),
            confidence=normalize_confidence(raw_block.get("confidence")),
            source=source,
            kind="ocr_block",
            parent_id=image_id,
            status="completed",
            block_index=index,
        ))
    return blocks


def build_question_groups(questions: list[RecognitionQuestionContract]) -> list[RecognitionQuestionGroup]:
    groups: list[RecognitionQuestionGroup] = []
    grouped: dict[str, list[RecognitionQuestionContract]] = {}
    for question in questions:
        key = question.section_title or "ungrouped"
        grouped.setdefault(key, []).append(question)

    for index, (title, group_questions) in enumerate(grouped.items(), start=1):
        group_bbox = _union_bboxes([
            question.bbox for question in group_questions if question.bbox is not None
        ])
        groups.append(RecognitionQuestionGroup(
            id=f"group-{index}",
            text=title if title != "ungrouped" else "默认题组",
            title=None if title == "ungrouped" else title,
            bbox=group_bbox,
            confidence=None,
            source="derived",
            kind="question_group",
            parent_id=group_questions[0].parent_id if group_questions else None,
            status="completed",
            question_ids=[question.question_id for question in group_questions],
        ))
    return groups


def build_answers(questions: list[RecognitionQuestionContract]) -> list[RecognitionAnswer]:
    answers: list[RecognitionAnswer] = []
    for question in questions:
        answers.append(RecognitionAnswer(
            id=f"answer-{question.question_id}",
            text=question.student_answer,
            bbox=question.answer_bbox,
            confidence=question.confidence,
            source=question.source,
            kind="answer",
            parent_id=question.question_id,
            status=question.status,
            error_code=question.error_code,
            question_id=question.question_id,
            student_answer=question.student_answer,
            standard_answer=None,
            answer_bbox=question.answer_bbox,
            is_correct=question.is_correct,
        ))
    return answers


def build_grading(questions: list[RecognitionQuestionContract]) -> list[RecognitionGrading]:
    gradings: list[RecognitionGrading] = []
    for question in questions:
        result = None
        if question.is_correct is True:
            result = "correct"
        elif question.is_correct is False:
            result = "incorrect"
        gradings.append(RecognitionGrading(
            id=f"grading-{question.question_id}",
            text=question.grading_explanation,
            bbox=question.answer_bbox or question.bbox,
            confidence=question.confidence,
            source=question.source,
            kind="grading",
            parent_id=question.question_id,
            status=question.status,
            error_code=question.error_code,
            question_id=question.question_id,
            result=result,
            explanation=question.grading_explanation,
            grader_source=question.source,
        ))
    return gradings


def build_regions(
    image_id: str,
    questions: list[RecognitionQuestionContract],
    layout_regions: list[dict[str, Any]] | None = None,
) -> list[RecognitionRegion]:
    question_bbox = _union_bboxes([
        question.bbox for question in questions if question.bbox is not None
    ])
    answer_bbox = _union_bboxes([
        question.answer_bbox for question in questions if question.answer_bbox is not None
    ])
    regions: list[RecognitionRegion] = []
    for index, region in enumerate(layout_regions or []):
        label = str(region.get("label") or "unknown_region")
        bbox = normalize_bbox(region.get("bbox"))
        if bbox is None:
            continue
        regions.append(RecognitionRegion(
            id=region.get("id") or f"{image_id}-region-{label}-{index}",
            text=label,
            bbox=bbox,
            confidence=normalize_confidence(region.get("confidence")),
            source=region.get("source", "layout"),
            kind="region",
            parent_id=image_id,
            status=region.get("status", "completed"),
            label=label,
        ))
    if question_bbox is not None:
        regions.append(RecognitionRegion(
            id=f"{image_id}-region-question-area",
            text="question_area",
            bbox=question_bbox,
            confidence=None,
            source="derived",
            kind="region",
            parent_id=image_id,
            status="completed",
            label="question_area",
        ))
    if answer_bbox is not None:
        regions.append(RecognitionRegion(
            id=f"{image_id}-region-answer-area",
            text="answer_area",
            bbox=answer_bbox,
            confidence=None,
            source="derived",
            kind="region",
            parent_id=image_id,
            status="completed",
            label="answer_area",
        ))
    return regions


def build_recognition_document(
    *,
    image: RecognitionImage | dict[str, Any],
    raw_questions: list[Any],
    raw_blocks: list[dict[str, Any]] | None = None,
    block_source: str = "ocr_unknown",
    layout_regions: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
) -> RecognitionDocument:
    image_model = image if isinstance(image, RecognitionImage) else RecognitionImage(**image)
    questions: list[RecognitionQuestionContract] = []
    for raw_question in raw_questions:
        if isinstance(raw_question, RecognitionQuestionContract):
            questions.append(raw_question)
            continue
        payload = raw_question.model_dump() if hasattr(raw_question, "model_dump") else dict(raw_question)
        questions.append(RecognitionQuestionContract(
            question_id=payload.get("question_id") or payload.get("id"),
            question_number=payload.get("question_number", 0),
            question_text=payload.get("question_text", ""),
            kind=payload.get("kind", "question"),
            question_role=payload.get("question_role"),
            context_text=payload.get("context_text"),
            options=payload.get("options"),
            blank_count=payload.get("blank_count"),
            bbox=payload.get("bbox"),
            answer_bbox=payload.get("answer_bbox"),
            image_url=payload.get("image_url"),
            crop_url=payload.get("crop_url"),
            visual_description=payload.get("visual_description"),
            status=payload.get("status", "completed"),
            student_answer=payload.get("student_answer"),
            is_correct=payload.get("is_correct"),
            grading_explanation=payload.get("grading_explanation"),
            section_title=payload.get("section_title"),
            section_index=payload.get("section_index"),
            sub_index=payload.get("sub_index"),
            source=payload.get("source", "unknown"),
            confidence=payload.get("confidence"),
            parent_id=payload.get("parent_id"),
            error_code=payload.get("error_code"),
        ))

    overlay = [mark for mark in (build_overlay_mark(question) for question in questions) if mark is not None]
    return RecognitionDocument(
        image=image_model,
        blocks=build_blocks_from_ocr_blocks(image_model.id, raw_blocks or [], source=block_source),
        regions=build_regions(image_model.id, questions, layout_regions=layout_regions),
        question_groups=build_question_groups(questions),
        questions=questions,
        answers=build_answers(questions),
        grading=build_grading(questions),
        overlay=overlay,
        meta=meta or {},
    )
