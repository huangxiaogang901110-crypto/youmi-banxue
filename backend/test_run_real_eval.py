from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import run_real_eval


def _write_fixture_sample(
    sample_dir: Path,
    *,
    name: str,
    intended_use: str = "math",
    payload: dict | None = None,
) -> None:
    (sample_dir / f"{name}.jpg").write_bytes(b"not-a-real-image")
    fixture_payload = payload or {
        "page_type": "math_homework",
        "questions": [
            {
                "question_id": f"{name}-q1",
                "question_number": 1,
                "question_text": "1. 5 + 3 = ___",
                "answer_bbox": [10, 20, 30, 40],
            }
        ],
        "intended_use": intended_use,
    }
    (sample_dir / f"{name}.json").write_text(
        json.dumps(fixture_payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_manifest(sample_dir: Path, entries: list[dict], *, total_images: int | None = None) -> None:
    payload = {
        "created_at": "2026-05-26T00:00:00Z",
        "total_images": total_images if total_images is not None else len(entries),
        "samples": entries,
    }
    (sample_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _make_sample_dir(tmp_path: Path) -> Path:
    sample_dir = tmp_path / "repair3d_user_samples"
    sample_dir.mkdir()
    return sample_dir


def _run(sample_dir: Path, *extra_args: str, sample_ids: list[str] | None = None) -> dict:
    args = ["--sample-dir", str(sample_dir)]
    if sample_ids is not None:
        args.extend(["--sample-ids", ",".join(sample_ids)])
    args.extend(extra_args)
    return run_real_eval.run(args)


def _populate_default_sample_set(sample_dir: Path) -> None:
    entries: list[dict] = []
    for sample_id in run_real_eval.DEFAULT_SAMPLE_IDS:
        expected_document_type = (
            "non_homework"
            if sample_id == run_real_eval.NON_HOMEWORK_SAMPLE_ID
            else "homework"
        )
        payload = (
            {
                "sample_id": sample_id,
                "expected_document_type": "non_homework",
                "questions": [],
            }
            if sample_id == run_real_eval.NON_HOMEWORK_SAMPLE_ID
            else None
        )
        _write_fixture_sample(sample_dir, name=sample_id, intended_use=sample_id, payload=payload)
        entries.append(
            {
                "filename": f"{sample_id}.jpg",
                "sidecar": f"{sample_id}.json",
                "intended_use": sample_id,
                "expected_document_type": expected_document_type,
            }
        )

    _write_fixture_sample(sample_dir, name="bonus_sample", intended_use="bonus")
    entries.append(
        {
            "filename": "bonus_sample.jpg",
            "sidecar": "bonus_sample.json",
            "intended_use": "bonus",
            "expected_document_type": "homework",
        }
    )
    _write_manifest(sample_dir, entries, total_images=len(entries))


def test_default_dry_run_does_not_trigger_real_model(monkeypatch, tmp_path):
    sample_dir = _make_sample_dir(tmp_path)
    _write_fixture_sample(sample_dir, name="sample_a")
    _write_manifest(
        sample_dir,
        [{"filename": "sample_a.jpg", "sidecar": "sample_a.json", "intended_use": "A"}],
    )

    def _boom(*args, **kwargs):
        raise AssertionError("real pipeline should not run in dry-run")

    monkeypatch.setattr(run_real_eval, "execute_real_sample", _boom)

    report = _run(sample_dir, sample_ids=["sample_a"])

    assert report["summary"]["dry_run"] is True
    assert report["summary"]["run_real"] is False
    assert report["summary"]["cost_available"] is False
    assert report["samples"][0]["status"] == "planned"
    assert report["samples"][0]["model_calls_per_image"] == 0
    assert report["samples"][0]["cost_per_image"] is None


def test_run_real_absent_does_not_check_env(monkeypatch, tmp_path):
    sample_dir = _make_sample_dir(tmp_path)
    _write_fixture_sample(sample_dir, name="sample_a")
    _write_manifest(
        sample_dir,
        [{"filename": "sample_a.jpg", "sidecar": "sample_a.json", "intended_use": "A"}],
    )

    def _boom():
        raise AssertionError("env check should be skipped in dry-run")

    monkeypatch.setattr(run_real_eval, "require_real_env", _boom)
    monkeypatch.delenv(run_real_eval.QWEN_TIMEOUT_ENV_VAR, raising=False)

    report = _run(
        sample_dir,
        "--dry-run",
        "--qwen-timeout-seconds",
        "30",
        sample_ids=["sample_a"],
    )

    assert report["summary"]["env"] == "skipped"
    assert report["summary"]["qwen_timeout_seconds"] == 30
    assert os_environ_value(run_real_eval.QWEN_TIMEOUT_ENV_VAR) is None


def test_run_real_requires_env(tmp_path, monkeypatch):
    sample_dir = _make_sample_dir(tmp_path)
    _write_fixture_sample(sample_dir, name="sample_a")
    _write_manifest(
        sample_dir,
        [{"filename": "sample_a.jpg", "sidecar": "sample_a.json", "intended_use": "A"}],
    )
    monkeypatch.delenv("QWEN_DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    exit_code = run_real_eval.main(
        ["--sample-dir", str(sample_dir), "--run-real", "--sample-ids", "sample_a"]
    )

    assert exit_code == 2


def test_default_sample_ids_include_non_homework(tmp_path):
    sample_dir = _make_sample_dir(tmp_path)
    _populate_default_sample_set(sample_dir)

    report = run_real_eval.run(["--sample-dir", str(sample_dir)])

    assert report["summary"]["selected_count"] == 5
    assert report["summary"]["sample_ids"] == list(run_real_eval.DEFAULT_SAMPLE_IDS)
    assert run_real_eval.NON_HOMEWORK_SAMPLE_ID in report["summary"]["sample_ids"]
    assert report["summary"]["non_homework_result"]["sample_id"] == run_real_eval.NON_HOMEWORK_SAMPLE_ID


def test_sample_dir_reads_manifest_and_skips_json_entries(tmp_path):
    sample_dir = _make_sample_dir(tmp_path)
    _write_fixture_sample(sample_dir, name="sample_a")
    _write_fixture_sample(sample_dir, name="sample_b")
    _write_manifest(
        sample_dir,
        [
            {"filename": "sample_a.jpg", "sidecar": "sample_a.json", "intended_use": "A"},
            {"filename": "sample_a.json", "sidecar": "sample_a.json", "intended_use": "JSON"},
            {"filename": "sample_b.jpg", "sidecar": "sample_b.json", "intended_use": "B"},
        ],
        total_images=2,
    )

    samples, summary = run_real_eval.select_manifest_samples(
        sample_dir,
        limit=5,
        sample_ids=["sample_a", "sample_b"],
    )

    assert [sample["filename"] for sample in samples] == ["sample_a.jpg", "sample_b.jpg"]
    assert summary["manifest_image_entries"] == 2


def test_sample_ids_are_optional_and_preserve_order(tmp_path):
    sample_dir = _make_sample_dir(tmp_path)
    _write_fixture_sample(sample_dir, name="sample_a")
    _write_fixture_sample(sample_dir, name="sample_b")
    _write_manifest(
        sample_dir,
        [
            {"filename": "sample_a.jpg", "sidecar": "sample_a.json", "intended_use": "A"},
            {"filename": "sample_b.jpg", "sidecar": "sample_b.json", "intended_use": "B"},
        ],
    )

    report = _run(sample_dir, "--limit", "2", sample_ids=["sample_b", "sample_a"])

    assert report["summary"]["sample_ids"] == ["sample_b", "sample_a"]
    assert [row["sample_id"] for row in report["samples"]] == ["sample_b", "sample_a"]


def test_sample_ids_count_over_limit_is_rejected(tmp_path):
    sample_dir = _make_sample_dir(tmp_path)
    _write_fixture_sample(sample_dir, name="sample_a")
    _write_fixture_sample(sample_dir, name="sample_b")
    _write_manifest(
        sample_dir,
        [
            {"filename": "sample_a.jpg", "sidecar": "sample_a.json", "intended_use": "A"},
            {"filename": "sample_b.jpg", "sidecar": "sample_b.json", "intended_use": "B"},
        ],
    )

    exit_code = run_real_eval.main(
        [
            "--sample-dir",
            str(sample_dir),
            "--sample-ids",
            "sample_a,sample_b",
            "--limit",
            "1",
        ]
    )

    assert exit_code == 2


def test_json_and_markdown_outputs_can_be_generated(tmp_path):
    sample_dir = _make_sample_dir(tmp_path)
    _write_fixture_sample(sample_dir, name="sample_a")
    _write_manifest(
        sample_dir,
        [{"filename": "sample_a.jpg", "sidecar": "sample_a.json", "intended_use": "A"}],
    )
    output_json = tmp_path / "out" / "result.json"
    output_md = tmp_path / "out" / "report.md"

    report = _run(
        sample_dir,
        "--output-json",
        str(output_json),
        "--output-md",
        str(output_md),
        sample_ids=["sample_a"],
    )

    assert output_json.is_file()
    assert output_md.is_file()
    loaded = json.loads(output_json.read_text(encoding="utf-8"))
    assert loaded["samples"][0]["sample_id"] == report["samples"][0]["sample_id"]
    assert "Repair-3D Real Eval Runner" in output_md.read_text(encoding="utf-8")


def test_qwen_timeout_seconds_only_applies_in_run_real(monkeypatch, tmp_path):
    sample_dir = _make_sample_dir(tmp_path)
    _write_fixture_sample(sample_dir, name="sample_a")
    _write_manifest(
        sample_dir,
        [{"filename": "sample_a.jpg", "sidecar": "sample_a.json", "intended_use": "A"}],
    )

    captured: dict[str, str | None] = {}
    monkeypatch.delenv(run_real_eval.QWEN_TIMEOUT_ENV_VAR, raising=False)

    def _fake_require_real_env():
        captured["require_real_env"] = os_environ_value(run_real_eval.QWEN_TIMEOUT_ENV_VAR)
        return {"QWEN_DASHSCOPE_API_KEY": "present", "DEEPSEEK_API_KEY": "present"}

    def _fake_execute_real_sample(sample: dict, *, db_path: Path) -> dict:
        captured["execute_real_sample"] = os_environ_value(run_real_eval.QWEN_TIMEOUT_ENV_VAR)
        sample_metrics = run_real_eval.derive_cached_metrics(sample, sample["sidecar_payload"])
        return run_real_eval.make_row(
            sample=sample,
            status="completed",
            sample_metrics=sample_metrics,
            latency_ms=12,
            model_calls_per_image=2,
            cost_per_image=1.25,
            skipped_reason="",
            dry_run=False,
            run_real=True,
            call_source=run_real_eval.SAFE_CALL_SOURCE,
            db_path=db_path,
        )

    monkeypatch.setattr(run_real_eval, "require_real_env", _fake_require_real_env)
    monkeypatch.setattr(run_real_eval, "execute_real_sample", _fake_execute_real_sample)

    report = _run(
        sample_dir,
        "--run-real",
        "--qwen-timeout-seconds",
        "45",
        sample_ids=["sample_a"],
    )

    assert report["summary"]["run_real"] is True
    assert report["summary"]["qwen_timeout_seconds"] == 45
    assert captured["require_real_env"] == "45"
    assert captured["execute_real_sample"] == "45"
    assert os_environ_value(run_real_eval.QWEN_TIMEOUT_ENV_VAR) is None


def test_call_source_is_fixed(tmp_path, monkeypatch):
    sample_dir = _make_sample_dir(tmp_path)
    _write_fixture_sample(sample_dir, name="sample_a")
    _write_manifest(
        sample_dir,
        [{"filename": "sample_a.jpg", "sidecar": "sample_a.json", "intended_use": "A"}],
    )
    monkeypatch.setenv("YOMICALL_SOURCE", "prod")

    report = _run(sample_dir, sample_ids=["sample_a"])

    assert report["summary"]["call_source"] == run_real_eval.SAFE_CALL_SOURCE
    assert report["samples"][0]["call_source"] == run_real_eval.SAFE_CALL_SOURCE
    assert os_environ_value("YOMICALL_SOURCE") == "prod"


def test_db_path_default_is_tmp(tmp_path):
    sample_dir = _make_sample_dir(tmp_path)
    _write_fixture_sample(sample_dir, name="sample_a")
    _write_manifest(
        sample_dir,
        [{"filename": "sample_a.jpg", "sidecar": "sample_a.json", "intended_use": "A"}],
    )

    report = _run(sample_dir, sample_ids=["sample_a"])

    assert report["summary"]["db_path"] == str(run_real_eval.DEFAULT_DB_PATH)


def test_production_db_path_is_rejected(tmp_path):
    sample_dir = _make_sample_dir(tmp_path)
    _write_fixture_sample(sample_dir, name="sample_a")
    _write_manifest(
        sample_dir,
        [{"filename": "sample_a.jpg", "sidecar": "sample_a.json", "intended_use": "A"}],
    )

    exit_code = run_real_eval.main(
        [
            "--sample-dir",
            str(sample_dir),
            "--sample-ids",
            "sample_a",
            "--db-path",
            str(tmp_path / "backend" / "yomi.db"),
        ]
    )

    assert exit_code == 2


def test_sidecar_json_is_not_treated_as_image(tmp_path):
    sample_dir = _make_sample_dir(tmp_path)
    _write_fixture_sample(sample_dir, name="sample_a")
    _write_manifest(
        sample_dir,
        [
            {"filename": "sample_a.json", "sidecar": "sample_a.json", "intended_use": "JSON"},
            {"filename": "sample_a.jpg", "sidecar": "sample_a.json", "intended_use": "A"},
        ],
        total_images=1,
    )

    report = _run(sample_dir, sample_ids=["sample_a"])

    assert len(report["samples"]) == 1
    assert report["samples"][0]["filename"] == "sample_a.jpg"


def test_query_model_call_stats_aggregates_cost_columns(tmp_path):
    db_path = tmp_path / "model_calls.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE model_calls (
            id TEXT PRIMARY KEY,
            job_id TEXT,
            cost_cny REAL,
            credit_cost REAL,
            latency_ms INTEGER
        )
        """
    )
    conn.executemany(
        "INSERT INTO model_calls (id, job_id, cost_cny, credit_cost, latency_ms) VALUES (?, ?, ?, ?, ?)",
        [
            ("call-1", "job-1", 1.25, 9.0, 100),
            ("call-2", "job-1", 0.0, 2.0, 200),
            ("call-3", "job-1", None, 0.0, 50),
        ],
    )
    conn.commit()
    conn.close()

    stats = run_real_eval.query_model_call_stats(db_path, "job-1")

    assert stats["call_count"] == 3
    assert stats["cost_available"] is True
    assert stats["total_cost_cny"] == 3.25
    assert stats["total_latency_ms"] == 350


def test_query_model_call_stats_marks_cost_unavailable_when_missing(tmp_path):
    db_path = tmp_path / "model_calls.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE model_calls (
            id TEXT PRIMARY KEY,
            job_id TEXT,
            latency_ms INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO model_calls (id, job_id, latency_ms) VALUES (?, ?, ?)",
        ("call-1", "job-1", 120),
    )
    conn.commit()
    conn.close()

    stats = run_real_eval.query_model_call_stats(db_path, "job-1")

    assert stats["call_count"] == 1
    assert stats["cost_available"] is False
    assert stats["total_cost_cny"] is None
    assert stats["total_latency_ms"] == 120


def os_environ_value(name: str) -> str | None:
    import os

    return os.environ.get(name)
