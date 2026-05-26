#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import eval_repair2_cached as cached_eval
from models import JobStatus, ParseJob

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SAMPLE_DIR = ROOT / "local_eval_samples" / "repair3d_user_samples"
DEFAULT_DB_PATH = Path("/tmp/repair3d_real_eval.db")
DEFAULT_LIMIT = 5
MAX_LIMIT = 5
SAFE_CALL_SOURCE = "repair3d_real_eval"
REQUIRED_ENV_VARS = ("QWEN_DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
PROTECTED_DB_TOKENS = ("yomi.db", "/home/hermes_me/yomi/")
RUNNER_PARENT_ID = "repair3d-eval-parent"
RUNNER_CHILD_ID = "repair3d-eval-child"


class RunnerError(RuntimeError):
    pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Minimal Repair-3D real-eval runner with safe dry-run default."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Read manifest + sidecars only. This is default when --run-real is absent.",
    )
    mode.add_argument(
        "--run-real",
        action="store_true",
        help="Allow real pipeline execution. Requires isolated DB and env vars.",
    )
    parser.add_argument(
        "--sample-dir",
        type=Path,
        default=DEFAULT_SAMPLE_DIR,
        help="Directory containing manifest.json, images, and sidecars.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Max samples to evaluate. Hard-capped at {MAX_LIMIT}.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite path for isolated real-run eval only.",
    )
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-md", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.run_real:
        args.dry_run = True
    args.sample_dir = resolve_path(args.sample_dir)
    args.db_path = resolve_path(args.db_path)
    if args.output_json is not None:
        args.output_json = resolve_path(args.output_json)
    if args.output_md is not None:
        args.output_md = resolve_path(args.output_md)
    return args


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def guess_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".bmp":
        return "image/bmp"
    return "application/octet-stream"


def normalize_limit(limit: int) -> int:
    if limit < 1:
        raise RunnerError("--limit must be >= 1")
    return min(limit, MAX_LIMIT)


def guard_db_path(db_path: Path) -> Path:
    resolved = resolve_path(db_path)
    lowered = str(resolved).lower()
    if any(token in lowered for token in PROTECTED_DB_TOKENS):
        raise RunnerError(f"Refusing production-like db path: {resolved}")
    return resolved


def env_status(names: Sequence[str]) -> dict[str, str]:
    return {
        name: "present" if str(os.environ.get(name, "")).strip() else "absent"
        for name in names
    }


def require_real_env() -> dict[str, str]:
    status = env_status(REQUIRED_ENV_VARS)
    missing = [name for name, state in status.items() if state == "absent"]
    if missing:
        raise RunnerError(
            "Missing required env vars for --run-real: " + ", ".join(missing)
        )
    return status


def load_manifest(sample_dir: Path) -> dict[str, Any]:
    manifest_path = sample_dir / "manifest.json"
    if not manifest_path.is_file():
        raise RunnerError(f"Manifest not found: {manifest_path}")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError(f"Manifest unreadable: {manifest_path}") from exc
    if not isinstance(raw, dict):
        raise RunnerError(f"Manifest must be a JSON object: {manifest_path}")
    return raw


def load_sidecar_payload(sidecar_path: Path) -> dict[str, Any] | None:
    if not sidecar_path.is_file():
        return None
    try:
        raw = sidecar_path.read_text(encoding="utf-8")
    except OSError:
        return None
    return cached_eval.normalize_fixture_payload(cached_eval.safe_json_loads(raw))


def sample_id_from_filename(filename: str) -> str:
    return Path(filename).stem


def select_manifest_samples(sample_dir: Path, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest = load_manifest(sample_dir)
    raw_samples = manifest.get("samples")
    if not isinstance(raw_samples, list):
        raise RunnerError("manifest.json missing samples list")

    image_entries: list[dict[str, Any]] = []
    missing_images = 0
    missing_sidecars = 0
    invalid_sidecars = 0

    for entry in raw_samples:
        if not isinstance(entry, dict):
            continue
        filename = str(entry.get("filename") or "").strip()
        if Path(filename).suffix.lower() not in IMAGE_SUFFIXES:
            continue

        sidecar_name = str(entry.get("sidecar") or Path(filename).with_suffix(".json").name)
        image_path = sample_dir / filename
        sidecar_path = sample_dir / sidecar_name
        payload = load_sidecar_payload(sidecar_path)

        if not image_path.is_file():
            missing_images += 1
        if not sidecar_path.is_file():
            missing_sidecars += 1
        elif payload is None:
            invalid_sidecars += 1

        image_entries.append(
            {
                "sample_id": sample_id_from_filename(filename),
                "filename": filename,
                "intended_use": str(entry.get("intended_use") or "").strip(),
                "image_path": image_path,
                "image_exists": image_path.is_file(),
                "sidecar": sidecar_name,
                "sidecar_path": sidecar_path,
                "sidecar_exists": sidecar_path.is_file(),
                "sidecar_payload": payload,
            }
        )

    selected = image_entries[:limit]
    total_images = manifest.get("total_images")
    manifest_complete = isinstance(total_images, int) and total_images == len(image_entries)
    if missing_images or missing_sidecars or invalid_sidecars:
        manifest_complete = False

    summary = {
        "manifest_path": str(sample_dir / "manifest.json"),
        "manifest_complete": manifest_complete,
        "declared_total_images": total_images if isinstance(total_images, int) else None,
        "manifest_image_entries": len(image_entries),
        "selected_count": len(selected),
        "sidecar_complete": all(
            sample["sidecar_exists"] and sample["sidecar_payload"] is not None
            for sample in selected
        ),
        "missing_image_count": missing_images,
        "missing_sidecar_count": missing_sidecars,
        "invalid_sidecar_count": invalid_sidecars,
    }
    return selected, summary


def derive_cached_metrics(
    sample: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return cached_eval.sample_from_payload(
        source_kind="json_fixture",
        sample_name=sample["filename"],
        job_id="",
        payload=payload,
        image_path=sample["image_path"],
    )


def metric_bundle(sample_metrics: dict[str, Any] | None, *, fallback_status: str = "") -> dict[str, Any]:
    if not sample_metrics:
        return {
            "terminal_status": fallback_status,
            "questions_count": 0,
            "zero_question_completed_rate": 0.0,
            "answer_bbox_non_empty_rate": 0.0,
            "answer_bbox_false_positive_rate": 0.0,
            "terminal_status_distribution": {fallback_status: 1} if fallback_status else {},
            "needs_review_count": 1 if fallback_status == "needs_review" else 0,
            "low_confidence_count": 1 if fallback_status == "low_confidence" else 0,
            "meta_false_positive_count": 0,
        }

    terminal_status = str(sample_metrics.get("terminal_status") or "")
    questions_count = int(sample_metrics.get("question_count") or 0)
    answer_bbox_valid = int(sample_metrics.get("_answer_bbox_valid_count") or 0)
    answer_bbox_empty = int(sample_metrics.get("_answer_bbox_empty_count") or 0)
    answer_bbox_all_zero = int(sample_metrics.get("_answer_bbox_all_zero_count") or 0)
    answer_bbox_invalid = int(sample_metrics.get("_answer_bbox_invalid_count") or 0)
    answer_bbox_false_positive = int(sample_metrics.get("_answer_bbox_false_positive_count") or 0)
    answer_bbox_false_positive_candidates = int(
        sample_metrics.get("_answer_bbox_false_positive_candidate_count") or 0
    )
    answer_bbox_total = (
        answer_bbox_valid + answer_bbox_empty + answer_bbox_all_zero + answer_bbox_invalid
    )
    return {
        "terminal_status": terminal_status,
        "questions_count": questions_count,
        "zero_question_completed_rate": (
            1.0 if terminal_status == "completed" and questions_count == 0 else 0.0
        ),
        "answer_bbox_non_empty_rate": (
            answer_bbox_valid / answer_bbox_total if answer_bbox_total else 0.0
        ),
        "answer_bbox_false_positive_rate": (
            answer_bbox_false_positive / answer_bbox_false_positive_candidates
            if answer_bbox_false_positive_candidates
            else 0.0
        ),
        "terminal_status_distribution": {terminal_status: 1} if terminal_status else {},
        "needs_review_count": 1 if terminal_status == "needs_review" else 0,
        "low_confidence_count": 1 if terminal_status == "low_confidence" else 0,
        "meta_false_positive_count": answer_bbox_false_positive,
    }


def make_row(
    *,
    sample: dict[str, Any],
    status: str,
    sample_metrics: dict[str, Any] | None,
    latency_ms: int,
    model_calls_per_image: int,
    cost_per_image: float,
    skipped_reason: str,
    dry_run: bool,
    run_real: bool,
    call_source: str,
    db_path: Path,
) -> dict[str, Any]:
    metrics = metric_bundle(sample_metrics)
    return {
        "sample_id": sample["sample_id"],
        "filename": sample["filename"],
        "intended_use": sample["intended_use"],
        "status": status,
        "questions_count": metrics["questions_count"],
        "zero_question_completed_rate": metrics["zero_question_completed_rate"],
        "answer_bbox_non_empty_rate": metrics["answer_bbox_non_empty_rate"],
        "answer_bbox_false_positive_rate": metrics["answer_bbox_false_positive_rate"],
        "terminal_status_distribution": metrics["terminal_status_distribution"],
        "latency_ms": latency_ms,
        "model_calls_per_image": model_calls_per_image,
        "cost_per_image": cost_per_image,
        "needs_review_count": metrics["needs_review_count"],
        "low_confidence_count": metrics["low_confidence_count"],
        "meta_false_positive_count": metrics["meta_false_positive_count"],
        "skipped_reason": skipped_reason,
        "no_paid": not run_real,
        "dry_run": dry_run,
        "run_real": run_real,
        "call_source": call_source,
        "db_path": str(db_path),
    }


def build_dry_run_row(sample: dict[str, Any], *, db_path: Path) -> dict[str, Any]:
    skipped_reason = ""
    status = "planned"
    sample_metrics: dict[str, Any] | None = None

    if not sample["image_exists"]:
        status = "skipped"
        skipped_reason = "missing_image"
    elif not sample["sidecar_exists"]:
        status = "skipped"
        skipped_reason = "missing_sidecar"
    elif sample["sidecar_payload"] is None:
        status = "skipped"
        skipped_reason = "invalid_sidecar"
    else:
        sample_metrics = derive_cached_metrics(sample, sample["sidecar_payload"])

    return make_row(
        sample=sample,
        status=status,
        sample_metrics=sample_metrics,
        latency_ms=0,
        model_calls_per_image=0,
        cost_per_image=0.0,
        skipped_reason=skipped_reason,
        dry_run=True,
        run_real=False,
        call_source=SAFE_CALL_SOURCE,
        db_path=db_path,
    )


def configure_real_runtime(db_path: Path) -> tuple[Any, Any]:
    os.environ["YOMICALL_SOURCE"] = SAFE_CALL_SOURCE
    db_path.parent.mkdir(parents=True, exist_ok=True)

    import db as db_module

    db_module.DB_PATH = db_path
    db_module.init()

    if "job_store" in sys.modules:
        importlib.reload(sys.modules["job_store"])
    if "pipeline" in sys.modules:
        pipeline_module = importlib.reload(sys.modules["pipeline"])
    else:
        pipeline_module = importlib.import_module("pipeline")
    return db_module, pipeline_module


def enqueue_job(pipeline_module: Any, db_module: Any, *, jid: str, filename: str, now: str) -> None:
    db_module.save_parse_job(
        jid,
        RUNNER_CHILD_ID,
        RUNNER_PARENT_ID,
        filename,
        0,
        JobStatus.created.value,
        now,
        client_upload_id=jid,
    )
    pipeline_module.enqueue_parse_job(
        jid,
        {
            "job": ParseJob(
                job_id=jid,
                status=JobStatus.created,
                questions_count=0,
                created_at=now,
                file_name=filename,
            ),
            "questions": [],
            "poll_count": 0,
            "child_id": RUNNER_CHILD_ID,
            "parent_id": RUNNER_PARENT_ID,
            "client_task_id": jid,
            "client_upload_id": jid,
        },
    )


def query_job_payload(db_path: Path, job_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status, data FROM parse_jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise RunnerError(f"parse_jobs row missing for job {job_id}")
    payload = cached_eval.safe_json_loads(row["data"])
    if "status" not in payload:
        payload["status"] = row["status"]
    return payload


def query_model_call_stats(db_path: Path, job_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS call_count,
            COALESCE(SUM(cost_cny), 0) AS total_cost_cny,
            COALESCE(SUM(latency_ms), 0) AS total_latency_ms
        FROM model_calls
        WHERE job_id = ?
        """,
        (job_id,),
    ).fetchone()
    conn.close()
    if not row:
        return {"call_count": 0, "total_cost_cny": 0.0, "total_latency_ms": 0}
    return {
        "call_count": int(row["call_count"] or 0),
        "total_cost_cny": float(row["total_cost_cny"] or 0.0),
        "total_latency_ms": int(row["total_latency_ms"] or 0),
    }


def execute_real_sample(sample: dict[str, Any], *, db_path: Path) -> dict[str, Any]:
    db_module, pipeline_module = configure_real_runtime(db_path)
    jid = uuid.uuid4().hex[:12]
    now = utc_now()
    enqueue_job(pipeline_module, db_module, jid=jid, filename=sample["filename"], now=now)

    file_obj = SimpleNamespace(
        filename=sample["filename"],
        content_type=guess_content_type(sample["image_path"]),
    )
    contents = sample["image_path"].read_bytes()
    started = time.perf_counter()
    asyncio.run(
        pipeline_module.worker_process_job(
            jid,
            contents,
            file_obj,
            now,
            RUNNER_PARENT_ID,
            RUNNER_CHILD_ID,
        )
    )
    wall_latency_ms = int((time.perf_counter() - started) * 1000)

    payload = query_job_payload(db_path, jid)
    sample_metrics = cached_eval.sample_from_payload(
        source_kind="cached_db",
        sample_name=sample["filename"],
        job_id=jid,
        payload=payload,
        image_path=sample["image_path"],
    )
    call_stats = query_model_call_stats(db_path, jid)
    return make_row(
        sample=sample,
        status=str(payload.get("status") or sample_metrics.get("terminal_status") or "completed"),
        sample_metrics=sample_metrics,
        latency_ms=max(wall_latency_ms, call_stats["total_latency_ms"]),
        model_calls_per_image=call_stats["call_count"],
        cost_per_image=call_stats["total_cost_cny"],
        skipped_reason="",
        dry_run=False,
        run_real=True,
        call_source=SAFE_CALL_SOURCE,
        db_path=db_path,
    )


def build_report(
    *,
    samples: list[dict[str, Any]],
    manifest_summary: dict[str, Any],
    dry_run: bool,
    run_real: bool,
    db_path: Path,
    env_state: dict[str, str] | None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for sample in samples:
        if dry_run:
            rows.append(build_dry_run_row(sample, db_path=db_path))
            continue
        if not sample["image_exists"]:
            rows.append(
                make_row(
                    sample=sample,
                    status="skipped",
                    sample_metrics=None,
                    latency_ms=0,
                    model_calls_per_image=0,
                    cost_per_image=0.0,
                    skipped_reason="missing_image",
                    dry_run=False,
                    run_real=True,
                    call_source=SAFE_CALL_SOURCE,
                    db_path=db_path,
                )
            )
            continue
        try:
            rows.append(execute_real_sample(sample, db_path=db_path))
        except Exception as exc:  # pragma: no cover - defensive real-run wrapper
            rows.append(
                make_row(
                    sample=sample,
                    status="failed",
                    sample_metrics=None,
                    latency_ms=0,
                    model_calls_per_image=0,
                    cost_per_image=0.0,
                    skipped_reason=f"worker_error:{exc.__class__.__name__}",
                    dry_run=False,
                    run_real=True,
                    call_source=SAFE_CALL_SOURCE,
                    db_path=db_path,
                )
            )

    return {
        "summary": {
            **manifest_summary,
            "sample_dir": str(samples[0]["image_path"].parent) if samples else str(DEFAULT_SAMPLE_DIR),
            "effective_limit": len(samples),
            "call_source": SAFE_CALL_SOURCE,
            "db_path": str(db_path),
            "env": env_state if env_state is not None else "skipped",
            "run_real": run_real,
            "dry_run": dry_run,
            "no_paid": not run_real,
            "selected_samples": [
                {
                    "sample_id": sample["sample_id"],
                    "filename": sample["filename"],
                    "intended_use": sample["intended_use"],
                    "sidecar_exists": sample["sidecar_exists"],
                    "image_exists": sample["image_exists"],
                }
                for sample in samples
            ],
        },
        "samples": rows,
    }


def write_json_report(path: Path | None, report: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def markdown_status_map(distribution: dict[str, int]) -> str:
    if not distribution:
        return "-"
    return json.dumps(distribution, ensure_ascii=False, sort_keys=True)


def write_markdown_report(path: Path | None, report: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = report["summary"]
    lines = [
        "# Repair-3D Real Eval Runner",
        "",
        f"- dry_run: `{summary['dry_run']}`",
        f"- run_real: `{summary['run_real']}`",
        f"- no_paid: `{summary['no_paid']}`",
        f"- sample_dir: `{summary['sample_dir']}`",
        f"- manifest_complete: `{summary['manifest_complete']}`",
        f"- sidecar_complete: `{summary['sidecar_complete']}`",
        f"- call_source: `{summary['call_source']}`",
        f"- db_path: `{summary['db_path']}`",
        "",
        "| sample_id | filename | status | questions | model_calls | cost_cny | skipped_reason | terminal_status_distribution |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for row in report["samples"]:
        lines.append(
            "| {sample_id} | {filename} | {status} | {questions_count} | {model_calls_per_image} | "
            "{cost_per_image} | {skipped_reason} | {terminal_status_distribution} |".format(
                sample_id=row["sample_id"],
                filename=row["filename"],
                status=row["status"],
                questions_count=row["questions_count"],
                model_calls_per_image=row["model_calls_per_image"],
                cost_per_image=row["cost_per_image"],
                skipped_reason=row["skipped_reason"] or "-",
                terminal_status_distribution=markdown_status_map(
                    row["terminal_status_distribution"]
                ),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_summary(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print(
        json.dumps(
            {
                "sample_dir": summary["sample_dir"],
                "selected_count": summary["selected_count"],
                "manifest_complete": summary["manifest_complete"],
                "sidecar_complete": summary["sidecar_complete"],
                "call_source": summary["call_source"],
                "db_path": summary["db_path"],
                "dry_run": summary["dry_run"],
                "run_real": summary["run_real"],
                "env": summary["env"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def run(argv: Sequence[str] | None = None) -> dict[str, Any]:
    args = parse_args(argv)
    limit = normalize_limit(args.limit)
    db_path = guard_db_path(args.db_path)
    samples, manifest_summary = select_manifest_samples(args.sample_dir, limit)

    env_state: dict[str, str] | None = None
    if args.run_real:
        os.environ["YOMICALL_SOURCE"] = SAFE_CALL_SOURCE
        env_state = require_real_env()

    report = build_report(
        samples=samples,
        manifest_summary=manifest_summary,
        dry_run=args.dry_run,
        run_real=args.run_real,
        db_path=db_path,
        env_state=env_state,
    )
    write_json_report(args.output_json, report)
    write_markdown_report(args.output_md, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    try:
        report = run(argv)
    except RunnerError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
