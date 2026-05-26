#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image

from document_classifier import (
    classify_document,
    clean_question_text,
    is_meta_instruction_or_footer_text,
    is_pseudo_or_garbled_question,
    should_drop_candidate_question,
)
from schemas.recognition import (
    RecognitionDocument,
    RecognitionQuestionContract,
    build_recognition_document,
    normalize_bbox,
    should_drop_question_payload,
)

ROOT = Path(__file__).resolve().parent.parent
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
DEFAULT_SAMPLE_LIMIT = 50
SKIPPED_NO_CACHE_REASON = "SKIPPEDNOCACHE"
DEFAULT_GAP_REPORT_PATH = ROOT / "backend" / "eval" / "repair3d_fixture_gap_report.json"
DEFAULT_REVIEW_TAGS_PATH = ROOT / "backend" / "eval" / "repair3d_fixture_review_tags.json"
DEFAULT_FIXTURE_DIRS = [
    ROOT / "local_eval_samples",
    ROOT / "e2e" / "fixtures",
    ROOT / "backend" / "tests" / "fixtures",
]
OPTION_MARKER = re.compile(r"(?:^|[\s(（])([A-DＡ-Ｄ])(?:[.．、)]|[)）])")
BLANK_UNDERSCORE = re.compile(r"_{2,}")
BLANK_PARENS = re.compile(r"(?:（\s*）|\(\s*\)|\[\s*\])")
NON_TEXT_RE = re.compile(r"^[\W_]+$", re.UNICODE)
ARITHMETIC_SIGNAL = re.compile(r"\d+\s*[+\-×xX*/÷=]\s*\d+")
MISSING = object()
PORTRAIT_LAYOUT_RATIO = 1.35
COMPLEX_LAYOUT_MIN_QUESTIONS = 3
PINYIN_DIACRITIC_RE = re.compile(r"[āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜüĀÁǍÀĒÉĚÈĪÍǏÌŌÓǑÒŪÚǓÙǕǗǙǛÜ]")
LATIN_TOKEN_RE = re.compile(r"[A-Za-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜüĀÁǍÀĒÉĚÈĪÍǏÌŌÓǑÒŪÚǓÙǕǗǙǛÜ]+")
ENGLISH_SIGNAL_RE = re.compile(
    r"\b(?:english|student|school|apple|banana|cherry|date|fill|blank|go|goes|am|is|are)\b",
    re.IGNORECASE,
)
GAP_CATEGORY_LABELS = {
    "mixed_pinyin_english": "拼音/英文混合题",
    "complex_chinese_page": "复杂语文页",
    "standalone_multiple_choice_page": "选择题独立页",
    "landscape_or_tilted_photo": "横拍/斜拍",
    "dense_multi_column_page": "多栏密集题",
    "non_homework_image": "非作业图",
    "cover_or_instruction_page": "封面/说明页",
    "teacher_markup": "涂改/老师批改痕迹",
}
QUALITY_GATE_EXPECTATIONS = {
    "only_json_fixtures": {
        "effective_sample_count": 10,
        "skipped_count": 0,
        "effective_source_kind_counts": {"json_fixture": 10},
        "skipped_source_kind_counts": {},
        "skipped_reason_counts": {},
        "violation_total_count": 0,
        "unknown_effective_count": 0,
        "fixture_only_effective_count": 0,
    },
    "no_paid": {
        "effective_sample_count": 11,
        "skipped_count": 18,
        "effective_source_kind_counts": {"json_fixture": 10, "fixture_cache": 1},
        "skipped_source_kind_counts": {"fixture_only": 18},
        "skipped_reason_counts": {SKIPPED_NO_CACHE_REASON: 18},
        "violation_total_count": 0,
        "unknown_effective_count": 0,
        "fixture_only_effective_count": 0,
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Zero-paid cached/fixture evaluator for Repair-2 structure checks."
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Optional SQLite db path. Defaults to backend/yomi.db, then known backups.",
    )
    parser.add_argument(
        "--sample-dir",
        action="append",
        dest="sample_dirs",
        type=Path,
        default=[],
        help="Optional fixture directory. Can be passed multiple times.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_SAMPLE_LIMIT,
        help="Maximum sample rows to print.",
    )
    parser.add_argument(
        "--no-paid",
        action="store_true",
        help="Safety no-op. This evaluator never calls paid OCR/Qwen/DeepSeek APIs.",
    )
    parser.add_argument(
        "--only-json-fixtures",
        action="store_true",
        help="Only evaluate JSON fixture sidecar samples. "
        "Excludes cached_db, fixture_cache, fixture_only, and any non-json_fixture source.",
    )
    parser.add_argument(
        "--gap-report-path",
        type=Path,
        default=DEFAULT_GAP_REPORT_PATH,
        help="Machine-readable Repair-3D fixture gap report output path.",
    )
    return parser.parse_args()


def iter_db_candidates(explicit: Path | None) -> list[Path]:
    if explicit:
        return [explicit]
    candidates = [ROOT / "backend" / "yomi.db"]
    candidates.extend(sorted((ROOT / "backend").glob("yomi.db.migrated_backup_*"), reverse=True))
    candidates.extend(sorted(ROOT.glob("yomi.db.public_migration_backup_*"), reverse=True))
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def resolve_db_path(explicit: Path | None) -> Path | None:
    fallback: Path | None = None
    for candidate in iter_db_candidates(explicit):
        if candidate.exists() and candidate.is_file():
            fallback = fallback or candidate
            try:
                with connect_readonly(candidate) as conn:
                    if not table_exists(conn, "parse_jobs"):
                        continue
                    cols = column_names(conn, "parse_jobs")
                    if "status" in cols:
                        row = conn.execute(
                            "SELECT 1 FROM parse_jobs WHERE status = 'completed' LIMIT 1"
                        ).fetchone()
                    else:
                        row = conn.execute("SELECT 1 FROM parse_jobs LIMIT 1").fetchone()
                    if row:
                        return candidate
            except sqlite3.Error:
                continue
    return fallback


def resolve_sample_dirs(explicit_dirs: list[Path]) -> list[Path]:
    candidates = explicit_dirs or DEFAULT_FIXTURE_DIRS
    resolved: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        if not path.exists() or not path.is_dir():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    return resolved


def load_fixture_images(sample_dirs: list[Path], limit: int | None = None) -> list[Path]:
    images: list[Path] = []
    for directory in sample_dirs:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                images.append(path)
                if limit is not None and len(images) >= limit:
                    return images
    return images


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def column_names(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def safe_json_loads(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw in (None, ""):
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def normalize_alias_key(key: Any) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(key or "").lower())


def alias_value(mapping: dict[str, Any], *aliases: str) -> Any:
    wanted = {normalize_alias_key(alias) for alias in aliases}
    for key, value in mapping.items():
        if normalize_alias_key(key) in wanted:
            return value
    return MISSING


def normalize_fixture_question(question: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(question)
    question_text = alias_value(question, "question_text")
    if question_text is not MISSING:
        normalized["question_text"] = question_text
    section_title = alias_value(question, "section_title")
    if section_title is not MISSING:
        normalized["section_title"] = section_title
    return normalized


def _question_payload(raw_question: Any) -> dict[str, Any]:
    if isinstance(raw_question, RecognitionQuestionContract):
        payload = raw_question.model_dump()
    elif hasattr(raw_question, "model_dump"):
        payload = raw_question.model_dump()
    elif isinstance(raw_question, dict):
        payload = dict(raw_question)
    else:
        return {}
    return normalize_fixture_question(payload)


def _coerce_question_number(value: Any, *, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _build_evaluator_recognition_document(
    raw_questions: list[Any],
    *,
    sample_name: str,
) -> tuple[RecognitionDocument, int]:
    question_contracts: list[RecognitionQuestionContract] = []
    legacy_field_break_count = 0

    for index, raw_question in enumerate(raw_questions, start=1):
        payload = _question_payload(raw_question)
        question_id = payload.get("question_id") or payload.get("id")
        question_number = payload.get("question_number")
        if not question_id or question_number is None:
            legacy_field_break_count += 1

        question_contracts.append(
            RecognitionQuestionContract(
                question_id=str(question_id or f"{sample_name}-question-{index}"),
                question_number=_coerce_question_number(question_number, fallback=index),
                question_text=str(payload.get("question_text") or payload.get("content") or ""),
                kind=str(payload.get("kind") or payload.get("type") or payload.get("question_type") or "question"),
                question_role=payload.get("question_role"),
                context_text=payload.get("context_text"),
                options=payload.get("options") if isinstance(payload.get("options"), list) else None,
                blank_count=payload.get("blank_count")
                if isinstance(payload.get("blank_count"), int) and payload.get("blank_count") >= 0
                else None,
                bbox=normalize_bbox(payload.get("bbox")),
                answer_bbox=normalize_bbox(payload.get("answer_bbox")),
                image_url=payload.get("image_url"),
                crop_url=payload.get("crop_url"),
                visual_description=payload.get("visual_description"),
                status=str(payload.get("status") or "completed"),
                student_answer=payload.get("student_answer"),
                is_correct=payload.get("is_correct"),
                grading_explanation=payload.get("grading_explanation"),
                section_title=payload.get("section_title"),
                section_index=payload.get("section_index"),
                sub_index=payload.get("sub_index"),
                source=str(payload.get("source") or "unknown"),
                confidence=payload.get("confidence"),
                parent_id=payload.get("parent_id"),
                error_code=payload.get("error_code"),
            )
        )

    recognition_document = build_recognition_document(
        image={
            "id": f"eval-image-{sample_name or 'sample'}",
            "file_path": sample_name,
            "source": "eval_repair2_cached",
        },
        raw_questions=question_contracts,
        meta={"source_kind": "evaluator"},
    )
    return recognition_document, legacy_field_break_count


def sample_path_label(image_path: Path | None) -> str:
    if image_path is None:
        return ""
    try:
        return str(image_path.relative_to(ROOT))
    except ValueError:
        return image_path.name


def read_image_metadata(image_path: Path | None) -> dict[str, Any]:
    if image_path is None:
        return {
            "sample_origin": "",
            "has_json_sidecar": False,
            "image_width": None,
            "image_height": None,
            "aspect_ratio": None,
            "layout_hint": "unknown",
        }

    width: int | None = None
    height: int | None = None
    try:
        with Image.open(image_path) as image:
            width, height = image.size
    except (OSError, ValueError):
        width = None
        height = None

    if width and height:
        if height / max(width, 1) >= PORTRAIT_LAYOUT_RATIO:
            layout_hint = "portrait"
        elif width / max(height, 1) >= PORTRAIT_LAYOUT_RATIO:
            layout_hint = "landscape"
        else:
            layout_hint = "squareish"
        aspect_ratio: float | None = round(height / max(width, 1), 3)
    else:
        layout_hint = "unknown"
        aspect_ratio = None

    return {
        "sample_origin": sample_path_label(image_path),
        "has_json_sidecar": image_path.with_suffix(".json").is_file(),
        "image_width": width,
        "image_height": height,
        "aspect_ratio": aspect_ratio,
        "layout_hint": layout_hint,
    }


def infer_coverage_flags(
    *,
    page_type: str,
    question_count: int,
    section_count: int,
    options_count: int,
    layout_hint: str,
) -> list[str]:
    flags: list[str] = []
    if page_type == "chinese_homework":
        flags.append("chinese_questions")
    if page_type == "cover_or_instruction_page":
        flags.append("cover_or_instruction_page")
    if page_type == "non_homework":
        flags.append("non_homework_image")
    if page_type == "mixed_homework":
        flags.append("mixed_homework")
    if options_count > 0:
        flags.append("multiple_choice")
    if options_count >= max(question_count, 1) * 2 and page_type != "mixed_homework":
        flags.append("choice_page")
    # Heuristic tag only: portrait layout + dense content => likely camera-style complex layout.
    if layout_hint == "portrait" and (
        question_count >= COMPLEX_LAYOUT_MIN_QUESTIONS or section_count >= 2 or options_count >= 3
    ):
        flags.append("complex_photo_layout")
    return flags


def fixture_image_sort_key(image_path: Path) -> tuple[int, str]:
    if image_path.with_suffix(".json").is_file():
        return (0, sample_path_label(image_path))
    try:
        relative = image_path.relative_to(ROOT)
    except ValueError:
        relative = image_path
    if str(relative).startswith("backend/tests/fixtures/"):
        return (1, str(relative))
    return (2, str(relative))


def normalize_fixture_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    raw_questions = payload.get("questions")
    if isinstance(raw_questions, list):
        normalized["questions"] = [
            normalize_fixture_question(question)
            for question in raw_questions
            if isinstance(question, dict)
        ]
    else:
        normalized["questions"] = []

    raw_document = payload.get("document_classification")
    document_classification = dict(raw_document) if isinstance(raw_document, dict) else {}

    page_type = alias_value(document_classification, "page_type")
    if page_type is MISSING:
        page_type = alias_value(payload, "page_type")
    if page_type is not MISSING:
        document_classification["page_type"] = page_type

    document_type = alias_value(document_classification, "doc_family", "document_type")
    if document_type is MISSING:
        document_type = alias_value(payload, "doc_family", "document_type")
    if document_type is not MISSING:
        document_classification["doc_family"] = document_type
        document_classification.setdefault("document_type", document_type)

    if document_classification:
        normalized["document_classification"] = document_classification

    return normalized


def load_json_fixture_payload(image_path: Path) -> dict[str, Any] | None:
    json_path = image_path.with_suffix(".json")
    if not json_path.exists() or not json_path.is_file():
        return None
    try:
        raw = json_path.read_text(encoding="utf-8")
    except OSError:
        return None
    return normalize_fixture_payload(safe_json_loads(raw))


def load_fixture_review_tags(path: Path = DEFAULT_REVIEW_TAGS_PATH) -> dict[str, dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for sample_origin, payload in raw.items():
        if isinstance(payload, dict):
            normalized[str(sample_origin)] = dict(payload)
    return normalized


def get_fixture_payload_for_sample(sample: dict[str, Any]) -> dict[str, Any] | None:
    sample_origin = str(sample.get("sample_origin") or "").strip()
    if not sample_origin:
        return None
    image_path = ROOT / sample_origin
    if not image_path.exists() or sample.get("source_kind") != "json_fixture":
        return None
    return load_json_fixture_payload(image_path)


def payload_has_pinyin_and_english(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    questions = payload.get("questions")
    if not isinstance(questions, list):
        return False

    parts: list[str] = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        section_title = str(question.get("section_title") or "").strip()
        question_text = str(question.get("question_text") or "").strip()
        if section_title:
            parts.append(section_title)
        if question_text:
            parts.append(question_text)

    merged = "\n".join(parts)
    if not merged:
        return False

    latin_tokens = LATIN_TOKEN_RE.findall(merged)
    short_latin_tokens = [
        token
        for token in latin_tokens
        if 2 <= len(token) <= 6 and token.isascii()
    ]
    has_pinyin_signal = (
        "拼音" in merged
        or bool(PINYIN_DIACRITIC_RE.search(merged))
        or (len(short_latin_tokens) >= 3 and any(marker in merged for marker in ("——", "___", "（", "(")))
    )
    has_english_signal = bool(ENGLISH_SIGNAL_RE.search(merged))
    return has_pinyin_signal and has_english_signal


def derive_fixture_category_tags(
    sample: dict[str, Any],
    review_entry: dict[str, Any],
    payload: dict[str, Any] | None,
) -> list[str]:
    tags = {
        str(tag)
        for tag in review_entry.get("review_tags", [])
        if isinstance(tag, str) and tag in GAP_CATEGORY_LABELS
    }
    if sample.get("page_type") == "non_homework":
        tags.add("non_homework_image")
    if sample.get("page_type") == "cover_or_instruction_page":
        tags.add("cover_or_instruction_page")
    if "choice_page" in sample.get("coverage_flags", []):
        tags.add("standalone_multiple_choice_page")
    if (
        sample.get("page_type") == "chinese_homework"
        and int(sample.get("question_count") or 0) >= 5
        and int(sample.get("section_count") or 0) >= 2
    ):
        tags.add("complex_chinese_page")
    if payload_has_pinyin_and_english(payload):
        tags.add("mixed_pinyin_english")
    return sorted(tags)


def count_blanks(text: str) -> int:
    stripped = str(text or "")
    return (
        len(BLANK_UNDERSCORE.findall(stripped))
        + len(BLANK_PARENS.findall(stripped))
        + stripped.count("□")
        + stripped.count("○")
    )


def count_options(question: dict[str, Any], text: str) -> int:
    options = question.get("options")
    if isinstance(options, list):
        return sum(1 for option in options if str(option or "").strip())
    markers = [match.group(1) for match in OPTION_MARKER.finditer(str(text or ""))]
    if not markers:
        return 0
    return len(dict.fromkeys(markers))


def looks_garbled(text: str) -> bool:
    return is_pseudo_or_garbled_question(text)


def build_question_texts(questions: list[RecognitionQuestionContract]) -> tuple[list[str], str]:
    section_titles: list[str] = []
    question_texts: list[str] = []
    seen_sections: set[str] = set()
    for question in questions:
        section_title = str(question.section_title or "").strip()
        if section_title and section_title not in seen_sections:
            seen_sections.add(section_title)
            section_titles.append(section_title)
        text = str(question.question_text or "").strip()
        if text:
            question_texts.append(text)
    merged = "\n".join(section_titles + question_texts)
    return question_texts, merged


def infer_document_classification(
    payload: dict[str, Any],
    questions: list[RecognitionQuestionContract],
) -> dict[str, Any]:
    stored = payload.get("document_classification")
    question_texts, raw_text = build_question_texts(questions)
    inferred = classify_document(raw_text=raw_text, question_texts=question_texts)
    inferred_payload = inferred.model_dump()
    if not isinstance(stored, dict):
        return inferred_payload

    merged = dict(inferred_payload)
    for key, value in stored.items():
        if value not in (None, "", [], {}):
            merged[key] = value

    doc_type = (
        stored.get("doc_family")
        or stored.get("document_type")
        or inferred_payload.get("doc_family")
        or inferred_payload.get("document_type")
    )
    if doc_type:
        merged["doc_family"] = doc_type
        merged.setdefault("document_type", doc_type)
    return merged


def _evaluate_recognition_questions(
    recognition_document: RecognitionDocument,
    document_classification: dict[str, Any],
    *,
    legacy_field_break_count: int,
) -> dict[str, int]:
    meta_filtered_count = 0
    pseudo_filtered_count = 0
    section_keys: set[tuple[Any, Any]] = set()
    options_count = 0
    blanks_count = 0
    kept_question_count = 0
    residual_meta_like_count = 0
    suspicious_question_count = 0

    for question in recognition_document.questions:
        raw_text = str(question.question_text or "")
        cleaned = clean_question_text(raw_text, document_classification)
        diagnostic_text = cleaned or raw_text
        contract_drop = should_drop_question_payload(question.model_dump())
        is_meta_like = (
            contract_drop
            or is_meta_instruction_or_footer_text(raw_text)
            or is_meta_instruction_or_footer_text(cleaned)
        )
        is_pseudo_like = looks_garbled(diagnostic_text)
        if contract_drop or should_drop_candidate_question(cleaned, question.bbox, document_classification):
            if is_pseudo_like and not is_meta_like:
                pseudo_filtered_count += 1
            else:
                meta_filtered_count += 1
            continue

        kept_question_count += 1
        if is_meta_like:
            residual_meta_like_count += 1
        if is_pseudo_like:
            suspicious_question_count += 1

        section_title = str(question.section_title or "").strip()
        section_index = question.section_index
        if section_title or section_index is not None:
            section_keys.add((section_index, section_title))

        blank_count = question.blank_count
        if not isinstance(blank_count, int) or blank_count < 0:
            blank_count = count_blanks(cleaned)
        blanks_count += blank_count
        options_count += count_options(question.model_dump(), cleaned)

    return {
        "kept_question_count": kept_question_count,
        "filtered_question_count": meta_filtered_count + pseudo_filtered_count,
        "meta_filtered_count": meta_filtered_count,
        "pseudo_filtered_count": pseudo_filtered_count,
        "section_count": len(section_keys),
        "options_count": options_count,
        "blanks_count": blanks_count,
        "residual_meta_like_count": residual_meta_like_count,
        "suspicious_question_count": suspicious_question_count,
        "legacy_field_break_count": legacy_field_break_count,
    }


def evaluate_questions(
    questions: list[dict[str, Any]],
    document_classification: dict[str, Any],
) -> dict[str, int]:
    recognition_document, legacy_field_break_count = _build_evaluator_recognition_document(
        questions,
        sample_name="evaluate_questions",
    )
    return _evaluate_recognition_questions(
        recognition_document,
        document_classification,
        legacy_field_break_count=legacy_field_break_count,
    )


def sample_from_payload(
    *,
    source_kind: str,
    sample_name: str,
    job_id: str | None,
    payload: dict[str, Any],
    image_path: Path | None = None,
    skipped_reason: str = "",
) -> dict[str, Any]:
    questions = payload.get("questions")
    if not isinstance(questions, list):
        questions = []
    recognition_document, legacy_field_break_count = _build_evaluator_recognition_document(
        questions,
        sample_name=sample_name,
    )
    document_classification = infer_document_classification(payload, recognition_document.questions)
    metrics = _evaluate_recognition_questions(
        recognition_document,
        document_classification,
        legacy_field_break_count=legacy_field_break_count,
    )
    page_type = str(document_classification.get("page_type") or "unknown")
    document_type = str(
        document_classification.get("doc_family")
        or document_classification.get("document_type")
        or "unknown"
    )
    image_metadata = read_image_metadata(image_path)
    coverage_flags = infer_coverage_flags(
        page_type=page_type,
        question_count=metrics["kept_question_count"],
        section_count=metrics["section_count"],
        options_count=metrics["options_count"],
        layout_hint=str(image_metadata["layout_hint"]),
    )
    return {
        "sample_name": sample_name,
        "source_kind": source_kind,
        "job_id": job_id or "",
        "sample_origin": str(image_metadata["sample_origin"]),
        "has_json_sidecar": bool(image_metadata["has_json_sidecar"]),
        "image_width": image_metadata["image_width"],
        "image_height": image_metadata["image_height"],
        "aspect_ratio": image_metadata["aspect_ratio"],
        "layout_hint": image_metadata["layout_hint"],
        "coverage_flags": coverage_flags,
        "page_type": page_type,
        "document_type": document_type,
        "question_count": metrics["kept_question_count"],
        "raw_question_count": len(questions),
        "filtered_question_count": metrics["filtered_question_count"],
        "meta_filtered_count": metrics["meta_filtered_count"],
        "pseudo_filtered_count": metrics["pseudo_filtered_count"],
        "section_count": metrics["section_count"],
        "options_count": metrics["options_count"],
        "blanks_count": metrics["blanks_count"],
        "skipped_reason": skipped_reason,
        "_suspicious_question_count": metrics["suspicious_question_count"],
        "_residual_meta_like_count": metrics["residual_meta_like_count"],
        "_legacy_field_break_count": metrics["legacy_field_break_count"],
    }


def parse_job_query(conn: sqlite3.Connection) -> str:
    cols = column_names(conn, "parse_jobs")
    order_column = "created_at" if "created_at" in cols else "updated_at" if "updated_at" in cols else "job_id"
    where_parts = ["status = 'completed'"]
    if "deleted_at" in cols:
        where_parts.append("deleted_at IS NULL")
    file_name_expr = "file_name" if "file_name" in cols else "'' AS file_name"
    created_at_expr = order_column if order_column in cols else "'' AS created_at"
    return (
        f"SELECT job_id, {file_name_expr}, status, {created_at_expr} AS sort_ts, data "
        f"FROM parse_jobs WHERE {' AND '.join(where_parts)} "
        f"ORDER BY sort_ts DESC LIMIT ?"
    )


def load_recent_cached_samples(
    conn: sqlite3.Connection,
    limit: int,
    seen_job_ids: set[str],
    seen_sample_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for row in conn.execute(parse_job_query(conn), (max(limit * 3, limit),)).fetchall():
        job_id = str(row["job_id"] or "")
        if not job_id or job_id in seen_job_ids:
            continue
        payload = safe_json_loads(row["data"])
        sample_name = str(payload.get("file_name") or row["file_name"] or job_id)
        if seen_sample_names is not None and sample_name in seen_sample_names:
            continue
        samples.append(
            sample_from_payload(
                source_kind="cached_db",
                sample_name=sample_name,
                job_id=job_id,
                payload=payload,
            )
        )
        seen_job_ids.add(job_id)
        if seen_sample_names is not None:
            seen_sample_names.add(sample_name)
        if len(samples) >= limit:
            break
    return samples


def lookup_cached_fixture(
    conn: sqlite3.Connection,
    image_path: Path,
) -> tuple[str, dict[str, Any]] | None:
    if not table_exists(conn, "image_fingerprints"):
        return None
    try:
        from image_fingerprint import compute_fingerprints
    except Exception:
        return None

    fingerprint = compute_fingerprints(image_path.read_bytes())
    ahash = fingerprint.get("ahash")
    if not ahash:
        return None

    row = conn.execute(
        """
        SELECT pj.job_id, pj.data
        FROM image_fingerprints fp
        JOIN parse_jobs pj ON pj.job_id = fp.job_id
        WHERE fp.ahash = ? AND pj.status = 'completed'
        ORDER BY fp.created_at DESC
        LIMIT 1
        """,
        (ahash,),
    ).fetchone()
    if not row:
        return None
    return str(row["job_id"] or ""), safe_json_loads(row["data"])


def load_fixture_samples(
    conn: sqlite3.Connection | None,
    fixture_images: list[Path],
    limit: int,
    seen_job_ids: set[str],
    *,
    only_json_fixtures: bool = False,
    seen_sample_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    ordered_images = sorted(fixture_images, key=fixture_image_sort_key)
    for image_path in ordered_images:
        sample_name = image_path.name
        if seen_sample_names is not None and sample_name in seen_sample_names:
            continue
        json_fixture_payload = load_json_fixture_payload(image_path)
        if json_fixture_payload is not None:
            samples.append(
                sample_from_payload(
                    source_kind="json_fixture",
                    sample_name=sample_name,
                    job_id="",
                    payload=json_fixture_payload,
                    image_path=image_path,
                )
            )
            if seen_sample_names is not None:
                seen_sample_names.add(sample_name)
            if len(samples) >= limit:
                break
            continue
        if only_json_fixtures:
            continue
        if conn is not None:
            cached = lookup_cached_fixture(conn, image_path)
            if cached:
                job_id, payload = cached
                if job_id and job_id not in seen_job_ids:
                    samples.append(
                        sample_from_payload(
                            source_kind="fixture_cache",
                            sample_name=sample_name,
                            job_id=job_id,
                            payload=payload,
                            image_path=image_path,
                        )
                    )
                    seen_job_ids.add(job_id)
                    if seen_sample_names is not None:
                        seen_sample_names.add(sample_name)
                    if len(samples) >= limit:
                        break
                    continue
        image_metadata = read_image_metadata(image_path)
        coverage_flags = infer_coverage_flags(
            page_type="unknown",
            question_count=0,
            section_count=0,
            options_count=0,
            layout_hint=str(image_metadata["layout_hint"]),
        )
        samples.append(
            {
                "sample_name": sample_name,
                "source_kind": "fixture_only",
                "job_id": "",
                "sample_origin": str(image_metadata["sample_origin"]),
                "has_json_sidecar": bool(image_metadata["has_json_sidecar"]),
                "image_width": image_metadata["image_width"],
                "image_height": image_metadata["image_height"],
                "aspect_ratio": image_metadata["aspect_ratio"],
                "layout_hint": image_metadata["layout_hint"],
                "coverage_flags": coverage_flags,
                "page_type": "unknown",
                "document_type": "unknown",
                "question_count": 0,
                "raw_question_count": 0,
                "filtered_question_count": 0,
                "meta_filtered_count": 0,
                "pseudo_filtered_count": 0,
                "section_count": 0,
                "options_count": 0,
                "blanks_count": 0,
                "skipped_reason": SKIPPED_NO_CACHE_REASON,
                "_suspicious_question_count": 0,
                "_residual_meta_like_count": 0,
                "_legacy_field_break_count": 0,
            }
        )
        if seen_sample_names is not None:
            seen_sample_names.add(sample_name)
    return samples


def split_samples_by_skip(
    samples: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    effective_samples: list[dict[str, Any]] = []
    skipped_samples: list[dict[str, Any]] = []
    for sample in samples:
        if str(sample.get("skipped_reason") or "").strip():
            skipped_samples.append(sample)
        else:
            effective_samples.append(sample)
    return effective_samples, skipped_samples


def build_summary(
    samples: list[dict[str, Any]],
    *,
    db_path: Path | None,
    fixture_dirs: list[Path],
    fixture_images: list[Path],
) -> dict[str, Any]:
    effective_samples, skipped_samples = split_samples_by_skip(samples)
    all_source_kinds = Counter(sample["source_kind"] for sample in samples)
    source_kinds = Counter(sample["source_kind"] for sample in effective_samples)
    skipped_source_kinds = Counter(sample["source_kind"] for sample in skipped_samples)
    page_types = Counter(sample["page_type"] for sample in effective_samples)
    document_types = Counter(sample["document_type"] for sample in effective_samples)
    skipped_reasons = Counter(
        sample["skipped_reason"] for sample in skipped_samples if sample["skipped_reason"]
    )
    coverage_flags = Counter(
        flag
        for sample in effective_samples
        for flag in sample.get("coverage_flags", [])
        if isinstance(flag, str) and flag
    )
    skipped_no_cache = sum(
        1 for sample in skipped_samples if sample["skipped_reason"] == SKIPPED_NO_CACHE_REASON
    )
    raw_question_total = sum(int(sample["raw_question_count"]) for sample in effective_samples)
    kept_question_total = sum(int(sample["question_count"]) for sample in effective_samples)
    filtered_question_total = sum(
        int(sample["filtered_question_count"]) for sample in effective_samples
    )
    meta_filtered_total = sum(int(sample["meta_filtered_count"]) for sample in effective_samples)
    pseudo_filtered_total = sum(int(sample["pseudo_filtered_count"]) for sample in effective_samples)
    suspicious_question_count = sum(
        int(sample["_suspicious_question_count"]) for sample in effective_samples
    )
    residual_meta_like_count = sum(
        int(sample["_residual_meta_like_count"]) for sample in effective_samples
    )
    legacy_field_break_count = sum(
        int(sample["_legacy_field_break_count"]) for sample in effective_samples
    )
    conservative_total = sum(
        1
        for sample in effective_samples
        if sample["page_type"] in {"cover_or_instruction_page", "non_homework", "unknown"}
    )
    conservative_bad = sum(
        1
        for sample in effective_samples
        if sample["page_type"] in {"cover_or_instruction_page", "non_homework", "unknown"}
        and sample["question_count"] > 0
    )
    mixed_total = sum(1 for sample in effective_samples if sample["page_type"] == "mixed_homework")
    mixed_kept = sum(
        1
        for sample in effective_samples
        if sample["page_type"] == "mixed_homework" and sample["question_count"] > 0
    )
    section_positive = sum(1 for sample in effective_samples if sample["section_count"] > 0)
    options_positive = sum(1 for sample in effective_samples if sample["options_count"] > 0)
    blanks_positive = sum(1 for sample in effective_samples if sample["blanks_count"] > 0)
    meta_violations = residual_meta_like_count
    conservative_bad_samples = [
        sample["sample_name"]
        for sample in effective_samples
        if sample["page_type"] in {"cover_or_instruction_page", "non_homework", "unknown"}
        and sample["question_count"] > 0
    ]
    residual_meta_samples = [
        sample["sample_name"]
        for sample in effective_samples
        if int(sample["_residual_meta_like_count"]) > 0
    ]
    suspicious_samples = [
        sample["sample_name"]
        for sample in effective_samples
        if int(sample["_suspicious_question_count"]) > 0
    ]
    legacy_field_samples = [
        sample["sample_name"]
        for sample in effective_samples
        if int(sample["_legacy_field_break_count"]) > 0
    ]
    violation_total_count = (
        len(conservative_bad_samples)
        + len(residual_meta_samples)
        + len(suspicious_samples)
        + len(legacy_field_samples)
    )

    return {
        "db_path": str(db_path) if db_path else "",
        "fixture_dirs": [str(path) for path in fixture_dirs],
        "fixture_image_count": len(fixture_images),
        "loaded_sample_count": len(samples),
        "sample_count": len(effective_samples),
        "effective_sample_count": len(effective_samples),
        "skipped_count": len(skipped_samples),
        "skipped_no_cache_count": skipped_no_cache,
        "source_kind_counts": dict(source_kinds),
        "page_type_counts": dict(page_types),
        "doctype_counts": dict(document_types),
        "document_type_counts": dict(document_types),
        "coverage_flag_counts": dict(coverage_flags),
        "effective_samples": {
            "count": len(effective_samples),
            "source_kind_counts": dict(source_kinds),
            "page_type_counts": dict(page_types),
            "doctype_counts": dict(document_types),
            "coverage_flag_counts": dict(coverage_flags),
        },
        "skipped_samples": {
            "count": len(skipped_samples),
            "source_kind_counts": dict(skipped_source_kinds),
            "skipped_reason_counts": dict(skipped_reasons),
        },
        "question_stats": {
            "raw_question_total": raw_question_total,
            "kept_question_total": kept_question_total,
            "filtered_question_total": filtered_question_total,
            "meta_filtered_total": meta_filtered_total,
            "pseudo_filtered_total": pseudo_filtered_total,
            "max_questions_in_sample": max(
                (int(sample["question_count"]) for sample in effective_samples),
                default=0,
            ),
        },
        "filter_metadata": {
            "skipped_reason_counts": dict(skipped_reasons),
            "filtered_sample_count": sum(
                1 for sample in effective_samples if int(sample["filtered_question_count"]) > 0
            ),
            "json_fixture_count": source_kinds.get("json_fixture", 0),
            "fixture_cache_count": source_kinds.get("fixture_cache", 0),
            "fixture_only_count": all_source_kinds.get("fixture_only", 0),
            "excluded_fixture_only_count": skipped_source_kinds.get("fixture_only", 0),
            "cached_db_count": source_kinds.get("cached_db", 0),
        },
        "violation_total_count": violation_total_count,
        "violations": {
            "conservative_pages_with_questions": {
                "count": len(conservative_bad_samples),
                "samples": conservative_bad_samples,
            },
            "residual_meta_like_questions": {
                "count": len(residual_meta_samples),
                "samples": residual_meta_samples,
            },
            "suspicious_or_garbled_questions": {
                "count": len(suspicious_samples),
                "samples": suspicious_samples,
            },
            "legacy_field_breaks": {
                "count": len(legacy_field_samples),
                "samples": legacy_field_samples,
            },
        },
        "checks": {
            "mixed_homework_not_dropped": {
                "observed": mixed_total,
                "kept_with_questions": mixed_kept,
                "passed": mixed_total == mixed_kept,
            },
            "meta_footer_not_in_question": {
                "violations": meta_violations,
                "passed": meta_violations == 0,
            },
            "options_blanks_sections_preserved": {
                "samples_with_sections": section_positive,
                "samples_with_options": options_positive,
                "samples_with_blanks": blanks_positive,
                "passed": section_positive > 0 or options_positive > 0 or blanks_positive > 0,
            },
            "cover_non_homework_unknown_conservative": {
                "observed": conservative_total,
                "bad_with_questions": conservative_bad,
                "passed": conservative_bad == 0,
            },
            "no_garbled_or_pseudo_question": {
                "violations": suspicious_question_count,
                "passed": suspicious_question_count == 0,
            },
            "pipeline_routing_old_fields_kept": {
                "violations": legacy_field_break_count,
                "passed": legacy_field_break_count == 0,
            },
        },
    }


def build_quality_gate(
    summary: dict[str, Any],
    effective_samples: list[dict[str, Any]],
    skipped_samples: list[dict[str, Any]],
    *,
    only_json_fixtures: bool,
) -> dict[str, Any]:
    mode = "only_json_fixtures" if only_json_fixtures else "no_paid"
    expected = QUALITY_GATE_EXPECTATIONS[mode]
    observed_effective_source_kinds = dict(summary.get("source_kind_counts", {}))
    observed_skipped_source_kinds = dict(
        ((summary.get("skipped_samples") or {}).get("source_kind_counts") or {})
    )
    observed_skipped_reasons = dict(
        ((summary.get("skipped_samples") or {}).get("skipped_reason_counts") or {})
    )
    unknown_effective_count = sum(
        1 for sample in effective_samples if str(sample.get("page_type") or "") == "unknown"
    )
    fixture_only_effective_count = sum(
        1 for sample in effective_samples if str(sample.get("source_kind") or "") == "fixture_only"
    )
    checks = {
        "effective_sample_count": {
            "expected": expected["effective_sample_count"],
            "observed": int(summary.get("effective_sample_count", 0)),
        },
        "skipped_count": {
            "expected": expected["skipped_count"],
            "observed": int(summary.get("skipped_count", 0)),
        },
        "effective_source_kind_counts": {
            "expected": expected["effective_source_kind_counts"],
            "observed": observed_effective_source_kinds,
        },
        "skipped_source_kind_counts": {
            "expected": expected["skipped_source_kind_counts"],
            "observed": observed_skipped_source_kinds,
        },
        "skipped_reason_counts": {
            "expected": expected["skipped_reason_counts"],
            "observed": observed_skipped_reasons,
        },
        "violation_total_count": {
            "expected": expected["violation_total_count"],
            "observed": int(summary.get("violation_total_count", 0)),
        },
        "unknown_effective_count": {
            "expected": expected["unknown_effective_count"],
            "observed": unknown_effective_count,
        },
        "fixture_only_effective_count": {
            "expected": expected["fixture_only_effective_count"],
            "observed": fixture_only_effective_count,
        },
        "loaded_sample_count_matches_split": {
            "expected": len(effective_samples) + len(skipped_samples),
            "observed": int(summary.get("loaded_sample_count", 0)),
        },
    }
    for check in checks.values():
        check["passed"] = check["observed"] == check["expected"]
    return {
        "mode": mode,
        "passed": all(check["passed"] for check in checks.values()),
        "checks": checks,
    }


def build_fixture_gap_report(
    *,
    fixture_samples: list[dict[str, Any]],
    fixture_dirs: list[Path],
    fixture_images: list[Path],
    db_path: Path | None,
) -> dict[str, Any]:
    review_tags = load_fixture_review_tags()
    sample_entries: list[dict[str, Any]] = []
    for sample in fixture_samples:
        sample_origin = str(sample.get("sample_origin") or "")
        review_entry = review_tags.get(sample_origin, {})
        payload = get_fixture_payload_for_sample(sample)
        category_tags = derive_fixture_category_tags(sample, review_entry, payload)

        missing_truth_artifacts: list[str] = []
        missing_fields: list[str] = []
        source_kind = str(sample.get("source_kind") or "")
        if source_kind == "fixture_only":
            missing_truth_artifacts = ["json_sidecar", "cache_payload"]
            missing_fields = [
                "document_classification.page_type",
                "document_classification.doc_family",
                "questions",
            ]
        elif source_kind == "fixture_cache":
            missing_truth_artifacts = ["json_sidecar", "human_verified_ground_truth"]
        elif source_kind == "json_fixture":
            if str(sample.get("page_type") or "unknown") == "unknown":
                missing_fields.append("document_classification.page_type")
            if str(sample.get("document_type") or "unknown") == "unknown":
                missing_fields.append("document_classification.doc_family")
            if (
                int(sample.get("raw_question_count") or 0) == 0
                and str(sample.get("page_type") or "")
                not in {"cover_or_instruction_page", "non_homework"}
            ):
                missing_fields.append("questions")

        sample_entries.append(
            {
                "sample_name": str(sample.get("sample_name") or ""),
                "sample_origin": sample_origin,
                "source_kind": source_kind,
                "page_type": str(sample.get("page_type") or "unknown"),
                "document_type": str(sample.get("document_type") or "unknown"),
                "layout_hint": str(sample.get("layout_hint") or "unknown"),
                "question_count": int(sample.get("question_count") or 0),
                "section_count": int(sample.get("section_count") or 0),
                "category_tags": category_tags,
                "missing_truth_artifacts": missing_truth_artifacts,
                "missing_fields": missing_fields,
                "review_note": str(review_entry.get("note") or ""),
            }
        )

    normalized_truth_entries = [
        entry for entry in sample_entries if entry["source_kind"] == "json_fixture"
    ]
    gap_category_ids = sorted(
        category_id
        for category_id in GAP_CATEGORY_LABELS
        if not any(category_id in entry["category_tags"] for entry in normalized_truth_entries)
    )
    gap_category_set = set(gap_category_ids)

    ground_truth_gap_samples: list[dict[str, Any]] = []
    for entry in sample_entries:
        if not entry["missing_truth_artifacts"] and not entry["missing_fields"]:
            continue
        candidate_gap_categories = [
            category_id for category_id in entry["category_tags"] if category_id in gap_category_set
        ]
        if candidate_gap_categories:
            annotation_priority = "high"
        elif entry["source_kind"] == "fixture_only":
            annotation_priority = "medium"
        else:
            annotation_priority = "low"
        ground_truth_gap_samples.append(
            {
                "sample_name": entry["sample_name"],
                "sample_origin": entry["sample_origin"],
                "source_kind": entry["source_kind"],
                "page_type": entry["page_type"],
                "document_type": entry["document_type"],
                "layout_hint": entry["layout_hint"],
                "missing_truth_artifacts": entry["missing_truth_artifacts"],
                "missing_fields": entry["missing_fields"],
                "candidate_gap_categories": candidate_gap_categories,
                "worth_manual_annotation": bool(
                    entry["missing_truth_artifacts"] or entry["missing_fields"]
                ),
                "annotation_priority": annotation_priority,
                "review_note": entry["review_note"],
            }
        )
    ground_truth_gap_samples.sort(
        key=lambda entry: (
            {"high": 0, "medium": 1, "low": 2}[entry["annotation_priority"]],
            entry["sample_origin"],
        )
    )

    category_gap_summary: list[dict[str, Any]] = []
    for category_id, label in GAP_CATEGORY_LABELS.items():
        normalized_truth_samples = [
            entry["sample_origin"]
            for entry in normalized_truth_entries
            if category_id in entry["category_tags"]
        ]
        missing_truth_candidates = [
            gap["sample_origin"]
            for gap in ground_truth_gap_samples
            if category_id in gap["candidate_gap_categories"]
        ]
        category_gap_summary.append(
            {
                "category_id": category_id,
                "label": label,
                "status": "covered" if normalized_truth_samples else "gap",
                "normalized_truth_sample_count": len(normalized_truth_samples),
                "normalized_truth_samples": normalized_truth_samples,
                "missing_truth_candidate_count": len(missing_truth_candidates),
                "missing_truth_candidates": missing_truth_candidates,
            }
        )

    return {
        "fixture_dirs": [str(path) for path in fixture_dirs],
        "db_path": str(db_path) if db_path else "",
        "inventory": {
            "fixture_image_count": len(fixture_images),
            "fixture_sample_count": len(fixture_samples),
            "source_kind_counts": dict(Counter(entry["source_kind"] for entry in sample_entries)),
            "normalized_truth_sample_count": len(normalized_truth_entries),
        },
        "gap_category_ids": gap_category_ids,
        "ground_truth_gaps": {
            "count": len(ground_truth_gap_samples),
            "samples": ground_truth_gap_samples,
        },
        "category_gap_summary": category_gap_summary,
    }


def write_json_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        current = None
    if current != serialized:
        path.write_text(serialized, encoding="utf-8")


def evaluate_cached_samples(
    *,
    db_path: Path | None,
    sample_dirs: list[Path],
    limit: int,
    only_json_fixtures: bool,
) -> dict[str, Any]:
    resolved_db_path = resolve_db_path(db_path)
    fixture_dirs = resolve_sample_dirs(sample_dirs)
    fixture_images = load_fixture_images(fixture_dirs)
    fixture_limit = max(limit, len(fixture_images) or 1)

    samples: list[dict[str, Any]] = []
    fixture_samples: list[dict[str, Any]] = []
    seen_job_ids: set[str] = set()
    seen_sample_names: set[str] = set()
    conn: sqlite3.Connection | None = None

    try:
        if resolved_db_path:
            conn = connect_readonly(resolved_db_path)

        if fixture_images:
            fixture_seen_job_ids: set[str] = set()
            fixture_seen_sample_names: set[str] = set()
            fixture_samples = load_fixture_samples(
                conn,
                fixture_images,
                fixture_limit,
                fixture_seen_job_ids,
                only_json_fixtures=False,
                seen_sample_names=fixture_seen_sample_names,
            )
            if only_json_fixtures:
                samples.extend(
                    sample for sample in fixture_samples if str(sample.get("source_kind")) == "json_fixture"
                )
            else:
                samples.extend(fixture_samples)
                seen_job_ids = fixture_seen_job_ids
                seen_sample_names = fixture_seen_sample_names

        if (
            conn is not None
            and len(samples) < limit
            and table_exists(conn, "parse_jobs")
            and not only_json_fixtures
        ):
            samples.extend(
                load_recent_cached_samples(
                    conn,
                    limit=limit - len(samples),
                    seen_job_ids=seen_job_ids,
                    seen_sample_names=seen_sample_names,
                )
            )
    finally:
        if conn is not None:
            conn.close()

    samples = samples[:limit]
    effective_samples, skipped_samples = split_samples_by_skip(samples)
    summary = build_summary(
        samples,
        db_path=resolved_db_path,
        fixture_dirs=fixture_dirs,
        fixture_images=fixture_images,
    )
    quality_gate = build_quality_gate(
        summary,
        effective_samples,
        skipped_samples,
        only_json_fixtures=only_json_fixtures,
    )
    fixture_gap_report = build_fixture_gap_report(
        fixture_samples=fixture_samples,
        fixture_dirs=fixture_dirs,
        fixture_images=fixture_images,
        db_path=resolved_db_path,
    )
    summary["quality_gate"] = quality_gate
    summary["fixture_gap_overview"] = {
        "gap_category_ids": list(fixture_gap_report["gap_category_ids"]),
        "ground_truth_gap_sample_count": int(fixture_gap_report["ground_truth_gaps"]["count"]),
    }
    return {
        "effective_samples": effective_samples,
        "skipped_samples": skipped_samples,
        "summary": summary,
        "fixture_gap_report": fixture_gap_report,
    }


def main() -> int:
    args = parse_args()
    result = evaluate_cached_samples(
        db_path=args.db_path,
        sample_dirs=args.sample_dirs,
        limit=max(int(args.limit or 0), 1),
        only_json_fixtures=args.only_json_fixtures,
    )
    write_json_report(args.gap_report_path, result["fixture_gap_report"])
    try:
        gap_report_label = str(args.gap_report_path.relative_to(ROOT))
    except ValueError:
        gap_report_label = str(args.gap_report_path)
    result["summary"]["fixture_gap_report_path"] = gap_report_label

    print(
        json.dumps(
            {
                "effective_samples": result["effective_samples"],
                "skipped_samples": result["skipped_samples"],
                "summary": result["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
