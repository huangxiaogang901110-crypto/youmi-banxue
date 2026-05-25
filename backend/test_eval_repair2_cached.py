from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from document_classifier import DocumentClassification
import eval_repair2_cached as cached_eval


def _create_parse_jobs_db(path, *, with_completed: bool) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE parse_jobs (job_id TEXT, status TEXT)")
    if with_completed:
        conn.execute("INSERT INTO parse_jobs(job_id, status) VALUES ('job-1', 'completed')")
    conn.commit()
    conn.close()


def _write_sidecar_fixture(image_path, payload) -> None:
    image_path.write_bytes(b"fixture-image")
    image_path.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_resolve_db_path_prefers_populated_backup(monkeypatch, tmp_path):
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()

    sqlite3.connect(backend_dir / "yomi.db").close()
    backup_db = backend_dir / "yomi.db.migrated_backup_20260514_093419"
    _create_parse_jobs_db(backup_db, with_completed=True)

    monkeypatch.setattr(cached_eval, "ROOT", tmp_path)

    assert cached_eval.resolve_db_path(None) == backup_db


def test_evaluate_questions_filters_legacy_meta_and_pseudo():
    document = DocumentClassification(page_type="math_homework").model_dump()
    metrics = cached_eval.evaluate_questions(
        [
            {"question_id": "q-1", "question_number": 1, "question_text": "班级：三年级"},
            {"question_id": "q-2", "question_number": 2, "question_text": "▲ - ● →"},
            {"question_id": "q-3", "question_number": 3, "question_text": "1. 5 + 3 = ___"},
        ],
        document,
    )

    assert metrics["kept_question_count"] == 1
    assert metrics["filtered_question_count"] == 2
    assert metrics["meta_filtered_count"] == 1
    assert metrics["pseudo_filtered_count"] == 1
    assert metrics["residual_meta_like_count"] == 0
    assert metrics["suspicious_question_count"] == 0


def test_load_fixture_samples_reads_json_sidecar_with_aliases(tmp_path):
    image_path = tmp_path / "chinese.jpg"
    _write_sidecar_fixture(
        image_path,
        {
            "PageType": "chinese_homework",
            "documenttype": "worksheet",
            "questions": [
                {
                    "question_id": "q-1",
                    "question_number": 1,
                    "QuestionText": "高兴——（    ）",
                    "SectionTitle": "一、反义词",
                },
                {
                    "question_id": "q-2",
                    "question_number": 2,
                    "QuestionText": "认真——（    ）",
                    "SectionTitle": None,
                },
            ],
        },
    )

    samples = cached_eval.load_fixture_samples(
        conn=None,
        fixture_images=[image_path],
        limit=10,
        seen_job_ids=set(),
    )

    assert len(samples) == 1
    sample = samples[0]
    assert sample["source_kind"] == "json_fixture"
    assert sample["page_type"] == "chinese_homework"
    assert sample["document_type"] == "worksheet"
    assert sample["question_count"] == 2
    assert sample["section_count"] == 1
    assert sample["skipped_reason"] == ""
    assert sample["pseudo_filtered_count"] == 0
    assert sample["_suspicious_question_count"] == 0


def test_load_fixture_samples_without_json_or_cache_is_skipped(tmp_path):
    image_path = tmp_path / "uncached.png"
    image_path.write_bytes(b"fixture-image")

    samples = cached_eval.load_fixture_samples(
        conn=None,
        fixture_images=[image_path],
        limit=10,
        seen_job_ids=set(),
    )

    assert samples == [
        {
            "sample_name": "uncached.png",
            "source_kind": "fixture_only",
            "job_id": "",
            "sample_origin": "uncached.png",
            "has_json_sidecar": False,
            "image_width": None,
            "image_height": None,
            "aspect_ratio": None,
            "layout_hint": "unknown",
            "coverage_flags": [],
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
            "skipped_reason": "SKIPPED_NO_CACHE",
            "_suspicious_question_count": 0,
            "_residual_meta_like_count": 0,
            "_legacy_field_break_count": 0,
        }
    ]


def test_json_fixture_preserves_multiple_choice_options(tmp_path):
    image_path = tmp_path / "choice.webp"
    _write_sidecar_fixture(
        image_path,
        {
            "pagetype": "english_homework",
            "questions": [
                {
                    "question_id": "q-1",
                    "question_number": 1,
                    "questiontext": "1. 选择正确答案。",
                    "options": ["A. apple", "B. banana", "C. cherry", "D. date"],
                }
            ],
        },
    )

    sample = cached_eval.load_fixture_samples(
        conn=None,
        fixture_images=[image_path],
        limit=10,
        seen_job_ids=set(),
    )[0]

    assert sample["source_kind"] == "json_fixture"
    assert sample["question_count"] == 1
    assert sample["options_count"] == 4


def test_json_fixture_cover_page_stays_questionless(tmp_path):
    image_path = tmp_path / "cover.bmp"
    _write_sidecar_fixture(
        image_path,
        {
            "page_type": "cover_or_instruction_page",
            "doc_family": "cover_sheet",
            "questions": [
                {
                    "question_id": "q-1",
                    "question_number": 1,
                    "question_text": "寒假作业",
                }
            ],
        },
    )

    sample = cached_eval.load_fixture_samples(
        conn=None,
        fixture_images=[image_path],
        limit=10,
        seen_job_ids=set(),
    )[0]

    assert sample["source_kind"] == "json_fixture"
    assert sample["page_type"] == "cover_or_instruction_page"
    assert sample["document_type"] == "cover_sheet"
    assert sample["question_count"] == 0


def test_only_json_fixtures_skips_images_without_sidecar(tmp_path):
    """--only-json-fixtures must skip images that lack a JSON sidecar."""
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    json_img = fixture_dir / "with_json.jpg"
    _write_sidecar_fixture(
        json_img,
        {
            "page_type": "math_homework",
            "questions": [
                {
                    "question_id": "q-1",
                    "question_number": 1,
                    "question_text": "5 + 3 = ___",
                }
            ],
        },
    )
    no_json_img = fixture_dir / "without_json.jpg"
    no_json_img.write_bytes(b"no-sidecar")

    samples = cached_eval.load_fixture_samples(
        conn=None,
        fixture_images=[json_img, no_json_img],
        limit=10,
        seen_job_ids=set(),
        only_json_fixtures=True,
    )

    assert len(samples) == 1
    assert samples[0]["source_kind"] == "json_fixture"
    assert samples[0]["sample_name"] == "with_json.jpg"


def test_only_json_fixtures_does_not_miss_json_samples(tmp_path):
    """All images with JSON sidecars must be included when --only-json-fixtures is set."""
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    paths = []
    for i in range(3):
        img = fixture_dir / f"sample_{i}.jpg"
        _write_sidecar_fixture(
            img,
            {
                "page_type": "math_homework",
                "questions": [
                    {
                        "question_id": f"q-{i}",
                        "question_number": i + 1,
                        "question_text": f"question {i}",
                    }
                ],
            },
        )
        paths.append(img)

    samples = cached_eval.load_fixture_samples(
        conn=None,
        fixture_images=paths,
        limit=10,
        seen_job_ids=set(),
        only_json_fixtures=True,
    )

    assert len(samples) == 3
    kinds = {sample["source_kind"] for sample in samples}
    assert kinds == {"json_fixture"}


def test_only_json_fixtures_flag_parsed_by_argparse(monkeypatch):
    """--only-json-fixtures must be recognized as a valid argument."""
    import sys
    monkeypatch.setattr(sys, "argv", ["prog", "--only-json-fixtures"])
    args = cached_eval.parse_args()
    assert hasattr(args, "only_json_fixtures")
    assert args.only_json_fixtures is True


def test_load_fixture_samples_prioritizes_json_sidecars_when_limit_small(tmp_path):
    uncached = tmp_path / "a_uncached.jpg"
    uncached.write_bytes(b"no-sidecar")
    json_a = tmp_path / "b_json.jpg"
    _write_sidecar_fixture(
        json_a,
        {
            "page_type": "math_homework",
            "questions": [
                {"question_id": "q-1", "question_number": 1, "question_text": "1 + 1 = ___"}
            ],
        },
    )
    json_b = tmp_path / "c_json.jpg"
    _write_sidecar_fixture(
        json_b,
        {
            "page_type": "chinese_homework",
            "questions": [
                {"question_id": "q-2", "question_number": 1, "question_text": "高兴——（    ）"}
            ],
        },
    )

    samples = cached_eval.load_fixture_samples(
        conn=None,
        fixture_images=[uncached, json_b, json_a],
        limit=2,
        seen_job_ids=set(),
    )

    assert [sample["sample_name"] for sample in samples] == ["b_json.jpg", "c_json.jpg"]
    assert {sample["source_kind"] for sample in samples} == {"json_fixture"}


def test_sample_from_payload_infers_missing_page_type_from_questions():
    sample = cached_eval.sample_from_payload(
        source_kind="cached_db",
        sample_name="math.jpg",
        job_id="job-1",
        payload={
            "document_classification": {"doc_family": "math_arithmetic"},
            "questions": [
                {
                    "question_id": "q-1",
                    "question_number": 1,
                    "question_text": "1. 5 + 3 = ___",
                }
            ],
        },
    )

    assert sample["page_type"] == "math_homework"
    assert sample["document_type"] == "math_arithmetic"


def test_build_summary_reports_question_filter_and_coverage_metadata():
    samples = [
        {
            "sample_name": "choice.jpg",
            "source_kind": "json_fixture",
            "job_id": "",
            "sample_origin": "local_eval_samples/choice.jpg",
            "has_json_sidecar": True,
            "image_width": 1080,
            "image_height": 1920,
            "aspect_ratio": 1.778,
            "layout_hint": "portrait",
            "coverage_flags": ["multiple_choice", "choice_page", "complex_photo_layout"],
            "page_type": "math_homework",
            "document_type": "math_comparison_logic",
            "question_count": 3,
            "raw_question_count": 3,
            "filtered_question_count": 1,
            "meta_filtered_count": 1,
            "pseudo_filtered_count": 0,
            "section_count": 1,
            "options_count": 12,
            "blanks_count": 0,
            "skipped_reason": "",
            "_suspicious_question_count": 0,
            "_residual_meta_like_count": 0,
            "_legacy_field_break_count": 0,
        },
        {
            "sample_name": "mixed.jpg",
            "source_kind": "fixture_only",
            "job_id": "",
            "sample_origin": "local_eval_samples/mixed.jpg",
            "has_json_sidecar": False,
            "image_width": 1080,
            "image_height": 1920,
            "aspect_ratio": 1.778,
            "layout_hint": "portrait",
            "coverage_flags": ["mixed_homework", "complex_photo_layout"],
            "page_type": "mixed_homework",
            "document_type": "unknown",
            "question_count": 2,
            "raw_question_count": 2,
            "filtered_question_count": 0,
            "meta_filtered_count": 0,
            "pseudo_filtered_count": 0,
            "section_count": 2,
            "options_count": 0,
            "blanks_count": 2,
            "skipped_reason": "SKIPPED_NO_CACHE",
            "_suspicious_question_count": 1,
            "_residual_meta_like_count": 0,
            "_legacy_field_break_count": 0,
        },
    ]

    summary = cached_eval.build_summary(
        samples,
        db_path=None,
        fixture_dirs=[Path("/tmp/fixtures")],
        fixture_images=[Path("/tmp/fixtures/a.jpg"), Path("/tmp/fixtures/b.jpg")],
    )

    assert summary["fixture_image_count"] == 2
    assert summary["loaded_sample_count"] == 2
    assert summary["sample_count"] == 1
    assert summary["effective_sample_count"] == 1
    assert summary["skipped_count"] == 1
    assert summary["source_kind_counts"] == {"json_fixture": 1}
    assert summary["doctype_counts"]["math_comparison_logic"] == 1
    assert summary["coverage_flag_counts"]["complex_photo_layout"] == 1
    assert summary["question_stats"]["kept_question_total"] == 3
    assert summary["question_stats"]["filtered_question_total"] == 1
    assert summary["filter_metadata"]["fixture_only_count"] == 1
    assert summary["filter_metadata"]["excluded_fixture_only_count"] == 1
    assert summary["filter_metadata"]["skipped_reason_counts"] == {"SKIPPED_NO_CACHE": 1}
    assert summary["effective_samples"]["count"] == 1
    assert summary["skipped_samples"] == {
        "count": 1,
        "source_kind_counts": {"fixture_only": 1},
        "skipped_reason_counts": {"SKIPPED_NO_CACHE": 1},
    }
    assert summary["violations"]["suspicious_or_garbled_questions"]["count"] == 0


def test_default_load_fixture_samples_still_works(tmp_path):
    """Default mode (only_json_fixtures=False) must produce fixture_only for no-json images."""
    img = tmp_path / "no_json.png"
    img.write_bytes(b"no-sidecar")

    samples = cached_eval.load_fixture_samples(
        conn=None,
        fixture_images=[img],
        limit=10,
        seen_job_ids=set(),
        only_json_fixtures=False,
    )

    assert len(samples) == 1
    assert samples[0]["source_kind"] == "fixture_only"
    assert samples[0]["skipped_reason"] == "SKIPPED_NO_CACHE"
