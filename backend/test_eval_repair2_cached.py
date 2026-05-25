from __future__ import annotations

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
