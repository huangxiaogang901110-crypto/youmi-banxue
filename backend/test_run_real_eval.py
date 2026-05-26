from __future__ import annotations

import json
from pathlib import Path

import run_real_eval


def _write_fixture_sample(sample_dir: Path, *, name: str, intended_use: str = "math") -> None:
    (sample_dir / f"{name}.jpg").write_bytes(b"not-a-real-image")
    (sample_dir / f"{name}.json").write_text(
        json.dumps(
            {
                "page_type": "math_homework",
                "questions": [
                    {
                        "question_id": f"{name}-q1",
                        "question_number": 1,
                        "question_text": "1. 5 + 3 = ___",
                        "answer_bbox": [10, 20, 30, 40],
                    }
                ],
            },
            ensure_ascii=False,
        ),
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

    report = run_real_eval.run(["--sample-dir", str(sample_dir)])

    assert report["summary"]["dry_run"] is True
    assert report["summary"]["run_real"] is False
    assert report["samples"][0]["status"] == "planned"
    assert report["samples"][0]["model_calls_per_image"] == 0
    assert report["samples"][0]["cost_per_image"] == 0


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

    report = run_real_eval.run(["--sample-dir", str(sample_dir), "--dry-run"])

    assert report["summary"]["env"] == "skipped"


def test_run_real_requires_env(tmp_path, monkeypatch):
    sample_dir = _make_sample_dir(tmp_path)
    _write_fixture_sample(sample_dir, name="sample_a")
    _write_manifest(
        sample_dir,
        [{"filename": "sample_a.jpg", "sidecar": "sample_a.json", "intended_use": "A"}],
    )
    monkeypatch.delenv("QWEN_DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    exit_code = run_real_eval.main(["--sample-dir", str(sample_dir), "--run-real"])

    assert exit_code == 2


def test_limit_default_is_capped_to_five(tmp_path):
    sample_dir = _make_sample_dir(tmp_path)
    entries = []
    for index in range(7):
        name = f"sample_{index}"
        _write_fixture_sample(sample_dir, name=name)
        entries.append(
            {
                "filename": f"{name}.jpg",
                "sidecar": f"{name}.json",
                "intended_use": name,
            }
        )
    _write_manifest(sample_dir, entries, total_images=7)

    report = run_real_eval.run(["--sample-dir", str(sample_dir)])

    assert report["summary"]["selected_count"] == 5
    assert len(report["samples"]) == 5


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

    samples, summary = run_real_eval.select_manifest_samples(sample_dir, limit=5)

    assert [sample["filename"] for sample in samples] == ["sample_a.jpg", "sample_b.jpg"]
    assert summary["manifest_image_entries"] == 2


def test_json_and_markdown_outputs_can_be_generated(tmp_path):
    sample_dir = _make_sample_dir(tmp_path)
    _write_fixture_sample(sample_dir, name="sample_a")
    _write_manifest(
        sample_dir,
        [{"filename": "sample_a.jpg", "sidecar": "sample_a.json", "intended_use": "A"}],
    )
    output_json = tmp_path / "out" / "result.json"
    output_md = tmp_path / "out" / "report.md"

    report = run_real_eval.run(
        [
            "--sample-dir",
            str(sample_dir),
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ]
    )

    assert output_json.is_file()
    assert output_md.is_file()
    loaded = json.loads(output_json.read_text(encoding="utf-8"))
    assert loaded["samples"][0]["sample_id"] == report["samples"][0]["sample_id"]
    assert "Repair-3D Real Eval Runner" in output_md.read_text(encoding="utf-8")


def test_call_source_is_fixed(tmp_path, monkeypatch):
    sample_dir = _make_sample_dir(tmp_path)
    _write_fixture_sample(sample_dir, name="sample_a")
    _write_manifest(
        sample_dir,
        [{"filename": "sample_a.jpg", "sidecar": "sample_a.json", "intended_use": "A"}],
    )
    monkeypatch.setenv("YOMICALL_SOURCE", "prod")

    report = run_real_eval.run(["--sample-dir", str(sample_dir)])

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

    report = run_real_eval.run(["--sample-dir", str(sample_dir)])

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

    report = run_real_eval.run(["--sample-dir", str(sample_dir)])

    assert len(report["samples"]) == 1
    assert report["samples"][0]["filename"] == "sample_a.jpg"


def os_environ_value(name: str) -> str | None:
    import os

    return os.environ.get(name)
