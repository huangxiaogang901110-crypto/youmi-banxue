from __future__ import annotations

import json
import sqlite3

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
