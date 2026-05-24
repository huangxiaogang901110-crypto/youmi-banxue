from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from image_fingerprint import compute_fingerprints

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "backend" / "yomi.db"
DEFAULT_SAMPLE_DIR = ROOT / "local_eval_samples"
TERMINAL_STATUSES = {"completed", "needs_review", "low_confidence", "failed"}


def load_images(sample_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in sample_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )


def _compute_ahash(image_path: Path) -> str | None:
    """Compute ahash for local image, return None on failure."""
    try:
        fp = compute_fingerprints(image_path.read_bytes())
        return fp.get("ahash")
    except Exception:
        return None


def lookup_cached_job(db_path: Path, ahash: str) -> dict[str, Any] | None:
    """Find a completed job with the same ahash, return its parse result if found."""
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        # Find job_id from fingerprints
        row = cur.execute(
            "SELECT job_id FROM image_fingerprints WHERE ahash = ? ORDER BY created_at DESC LIMIT 1",
            (ahash,),
        ).fetchone()
        if not row:
            return None
        job_id = row[0]
        # Check parse_jobs for completed job
        job_row = cur.execute(
            "SELECT data FROM parse_jobs WHERE id = ? AND status = 'completed'",
            (job_id,),
        ).fetchone()
        if not job_row:
            return None
        job_data = json.loads(job_row[0]) if isinstance(job_row[0], str) else job_row[0]
        questions = job_data.get("questions", []) if isinstance(job_data, dict) else []
        recognition = job_data.get("recognition", {}) if isinstance(job_data, dict) else {}
        doc_cls = (
            job_data.get("document_classification", {})
            if isinstance(job_data, dict)
            else {}
        )
        return {
            "job_id": job_id,
            "questions": questions,
            "recognition": recognition,
            "document_classification": doc_cls,
            "final_status": "completed",
        }
    except Exception:
        return None
    finally:
        conn.close()


def login(base: str, phone: str, password: str) -> tuple[requests.Session, int, bool]:
    session = requests.Session()
    session.trust_env = False
    response = session.post(
        f"{base}/api/auth/login",
        json={"phone": phone, "password": password},
        timeout=20,
    )
    payload = response.json()
    token = ((payload.get("data") or {}).get("token")) if isinstance(payload, dict) else None
    if response.status_code == 200 and payload.get("ok") and token:
        session.headers.update({"Authorization": f"Bearer {token}"})
        return session, response.status_code, True
    return session, response.status_code, False


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def query_sqlite(job_id: str) -> tuple[bool, bool]:
    if not DB_PATH.exists():
        return False, False
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        try:
            qwen_count = cur.execute(
                """
                SELECT COUNT(*) FROM model_calls
                WHERE json_extract(data, '$.task_id') = ?
                  AND (
                    json_extract(data, '$.model_name') LIKE 'qwen-vl%'
                    OR json_extract(data, '$.provider_name') = 'aliyun_dashscope'
                  )
                """,
                (job_id,),
            ).fetchone()[0]
        except sqlite3.OperationalError:
            qwen_count = cur.execute(
                "SELECT COUNT(*) FROM model_calls WHERE data LIKE ?",
                (f'%"{job_id}"%',),
            ).fetchone()[0]
        fingerprint_count = cur.execute(
            "SELECT COUNT(*) FROM image_fingerprints WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]
        return qwen_count > 0, fingerprint_count > 0
    finally:
        conn.close()


def detect_child_answer_pollution(questions: list[dict[str, Any]]) -> bool:
    answers = [str(q.get("student_answer") or "").strip() for q in questions if str(q.get("student_answer") or "").strip()]
    if not answers:
        return False
    if any(len(answer) >= 120 or answer.count("\n") >= 3 for answer in answers):
        return True
    counts = Counter(answers)
    top = counts.most_common(1)[0][1]
    return len(answers) >= 3 and top / len(answers) >= 0.6


def evaluate_one(
    session: requests.Session,
    base: str,
    image_path: Path,
    *,
    poll_interval: float,
    timeout_sec: float,
    allow_paid_ocr: bool = False,
    max_paid_calls: int = 0,
    paid_ocr_used: list[int] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    width, height = image_size(image_path)

    # ─── CostGuard-0: 付费 OCR 闸门 ───
    paid_ocr_counter = paid_ocr_used if paid_ocr_used is not None else [0]

    if not allow_paid_ocr:
        # 默认模式：尝试缓存，无缓存则跳过
        ahash = _compute_ahash(image_path)
        if ahash and db_path:
            cached = lookup_cached_job(db_path, ahash)
            if cached:
                questions = cached["questions"]
                return _build_result_from_cache(
                    image_path, width, height, cached, questions, db_path
                )
        # 无缓存 → 跳过
        return _skipped_paid_ocr(image_path, width, height)
    
    if max_paid_calls <= 0:
        print("ERROR: --allow-paid-ocr requires --max-paid-calls N (positive integer)", file=sys.stderr)
        sys.exit(3)
    
    if paid_ocr_counter[0] >= max_paid_calls:
        return _max_paid_calls_reached(image_path, width, height, paid_ocr_counter)
    
    paid_ocr_counter[0] += 1
    print(f"[paid_ocr] call {paid_ocr_counter[0]}/{max_paid_calls}: {image_path.name}", file=sys.stderr)

    with image_path.open("rb") as file_obj:
        response = session.post(
            f"{base}/api/parse-jobs",
            files={"file": (image_path.name, file_obj, "image/jpeg")},
            data={"source_type": "web_upload", "client_task_id": uuid.uuid4().hex},
            timeout=60,
        )
    upload_status_code = response.status_code
    upload_data = response.json()
    job_id = ((upload_data.get("data") or {}).get("job_id")) if isinstance(upload_data, dict) else None
    status_sequence: list[str] = []
    started_at = time.time()
    final_job = None
    if not job_id:
        return {
            "file_name": image_path.name,
            "size": [width, height],
            "upload_status_code": upload_status_code,
            "job_id": None,
            "status_sequence": [],
            "latency_sec": 0.0,
            "final_status": "failed",
            "question_count": 0,
            "questions": [],
            "recognition": {},
            "document_classification": {},
            "completed_empty": False,
            "zero_bbox_count": 0,
            "meta_like_question_count": 0,
            "header_question_count": 0,
            "instruction_question_count": 0,
            "footer_question_count": 0,
            "question_region_hit_count": 0,
            "section_nonempty_count": 0,
            "bbox_negative_count": 0,
            "bbox_giant_count": 0,
            "answer_bbox_usable_count": 0,
            "overlay_ready": False,
            "child_answer_pollution": False,
            "grouping_reasonable": False,
            "qwen_recorded": False,
            "fingerprint_written": False,
            "major_failure_reason": "upload_failed",
        }

    while time.time() - started_at <= timeout_sec:
        status_response = session.get(f"{base}/api/parse-jobs/{job_id}", timeout=20)
        status_payload = status_response.json()
        final_job = status_payload.get("data") or {}
        current_status = str(final_job.get("status") or "")
        if current_status and (not status_sequence or status_sequence[-1] != current_status):
            status_sequence.append(current_status)
        if current_status in TERMINAL_STATUSES:
            break
        time.sleep(poll_interval)

    latency_sec = round(time.time() - started_at, 2)
    final_status = str((final_job or {}).get("status") or "failed")

    questions_payload = session.get(f"{base}/api/parse-jobs/{job_id}/questions", timeout=20).json()
    questions = questions_payload.get("data") or []
    recognition_payload = session.get(f"{base}/api/parse-jobs/{job_id}/recognition", timeout=20).json()
    recognition = recognition_payload.get("data") or {}
    document_classification = (
        (final_job or {}).get("document_classification")
        or (recognition.get("meta") or {}).get("document_classification")
        or {}
    )
    stats = document_classification.get("stats", {}) if isinstance(document_classification, dict) else {}
    qwen_recorded, fingerprint_written = query_sqlite(job_id)

    answer_bbox_usable_count = sum(1 for question in questions if question.get("answer_bbox"))
    overlay_ready = any(question.get("answer_bbox") and question.get("image_url") for question in questions)
    section_nonempty_count = sum(
        1
        for question in questions
        if question.get("section_title") or question.get("section_index") is not None or question.get("sub_index") is not None
    )
    zero_bbox_count = sum(1 for question in questions if question.get("bbox") == [0, 0, 0, 0])

    return {
        "file_name": image_path.name,
        "size": [width, height],
        "subject": document_classification.get("subject", "unknown"),
        "page_type": document_classification.get("page_type", "unknown"),
        "doc_family": document_classification.get("doc_family", "unknown"),
        "upload_status_code": upload_status_code,
        "job_id": job_id,
        "status_sequence": status_sequence,
        "latency_sec": latency_sec,
        "final_status": final_status,
        "question_count": len(questions),
        "questions": questions,
        "recognition": recognition,
        "document_classification": document_classification,
        "completed_empty": final_status == "completed" and len(questions) == 0,
        "zero_bbox_count": zero_bbox_count,
        "meta_like_question_count": int(stats.get("meta_like_question_count", 0) or 0),
        "header_question_count": int(stats.get("header_question_count", 0) or 0),
        "instruction_question_count": int(stats.get("instruction_question_count", 0) or 0),
        "footer_question_count": int(stats.get("footer_question_count", 0) or 0),
        "question_region_hit_count": int(stats.get("question_region_hit_count", 0) or 0),
        "section_nonempty_count": int(stats.get("section_nonempty_count", section_nonempty_count) or 0),
        "bbox_negative_count": int(stats.get("bbox_negative_count", 0) or 0),
        "bbox_giant_count": int(stats.get("bbox_giant_count", 0) or 0),
        "answer_bbox_usable_count": answer_bbox_usable_count,
        "overlay_ready": overlay_ready,
        "child_answer_pollution": detect_child_answer_pollution(questions),
        "grouping_reasonable": section_nonempty_count > 0 or len(questions) <= 2,
        "qwen_recorded": qwen_recorded,
        "fingerprint_written": fingerprint_written,
        "major_failure_reason": (
            document_classification.get("major_failure_reason")
            or (recognition.get("meta") or {}).get("error_code")
            or ""
        ),
    }


def _skipped_paid_ocr(image_path: Path, width: int, height: int) -> dict[str, Any]:
    """CostGuard: default mode skips paid OCR."""
    print(f"[paid_ocr] SKIPPED (no --allow-paid-ocr): {image_path.name}", file=sys.stderr)
    return {
        "file_name": image_path.name,
        "size": [width, height],
        "upload_status_code": 0,
        "job_id": None,
        "status_sequence": ["SKIPPED_PAID_OCR"],
        "latency_sec": 0.0,
        "final_status": "skipped_paid_ocr",
        "question_count": 0,
        "questions": [],
        "recognition": {},
        "document_classification": {},
        "completed_empty": False,
        "zero_bbox_count": 0,
        "meta_like_question_count": 0,
        "header_question_count": 0,
        "instruction_question_count": 0,
        "footer_question_count": 0,
        "question_region_hit_count": 0,
        "section_nonempty_count": 0,
        "bbox_negative_count": 0,
        "bbox_giant_count": 0,
        "answer_bbox_usable_count": 0,
        "overlay_ready": False,
        "child_answer_pollution": False,
        "grouping_reasonable": False,
        "qwen_recorded": False,
        "fingerprint_written": False,
        "major_failure_reason": "skipped_paid_ocr",
    }


def _max_paid_calls_reached(
    image_path: Path, width: int, height: int, paid_ocr_counter: list[int]
) -> dict[str, Any]:
    """CostGuard: max paid calls reached, stop further OCR."""
    print(
        f"[paid_ocr] MAX_PAID_CALLS_REACHED (used={paid_ocr_counter[0]}): {image_path.name}",
        file=sys.stderr,
    )
    return {
        "file_name": image_path.name,
        "size": [width, height],
        "upload_status_code": 0,
        "job_id": None,
        "status_sequence": ["MAX_PAID_CALLS_REACHED"],
        "latency_sec": 0.0,
        "final_status": "max_paid_calls_reached",
        "question_count": 0,
        "questions": [],
        "recognition": {},
        "document_classification": {},
        "completed_empty": False,
        "zero_bbox_count": 0,
        "meta_like_question_count": 0,
        "header_question_count": 0,
        "instruction_question_count": 0,
        "footer_question_count": 0,
        "question_region_hit_count": 0,
        "section_nonempty_count": 0,
        "bbox_negative_count": 0,
        "bbox_giant_count": 0,
        "answer_bbox_usable_count": 0,
        "overlay_ready": False,
        "child_answer_pollution": False,
        "grouping_reasonable": False,
        "qwen_recorded": False,
        "fingerprint_written": False,
        "major_failure_reason": "max_paid_calls_reached",
    }


def _build_result_from_cache(
    image_path: Path,
    width: int,
    height: int,
    cached: dict[str, Any],
    questions: list[dict[str, Any]],
    db_path: Path,
) -> dict[str, Any]:
    """CostGuard: build result from cached parse_jobs data."""
    print(f"[paid_ocr] CACHED (job={cached['job_id'][:12]}): {image_path.name}", file=sys.stderr)
    qwen_recorded, fingerprint_written = query_sqlite(cached["job_id"])
    doc_cls = cached.get("document_classification", {})
    stats = doc_cls.get("stats", {}) if isinstance(doc_cls, dict) else {}
    section_nonempty_count = sum(
        1
        for question in questions
        if question.get("section_title")
        or question.get("section_index") is not None
        or question.get("sub_index") is not None
    )
    return {
        "file_name": image_path.name,
        "size": [width, height],
        "subject": doc_cls.get("subject", "unknown"),
        "page_type": doc_cls.get("page_type", "unknown"),
        "doc_family": doc_cls.get("doc_family", "unknown"),
        "upload_status_code": 0,
        "job_id": cached["job_id"],
        "status_sequence": ["cached"],
        "latency_sec": 0.0,
        "final_status": "completed",
        "question_count": len(questions),
        "questions": questions,
        "recognition": cached.get("recognition", {}),
        "document_classification": doc_cls,
        "completed_empty": False,
        "zero_bbox_count": sum(1 for q in questions if q.get("bbox") == [0, 0, 0, 0]),
        "meta_like_question_count": int(stats.get("meta_like_question_count", 0) or 0),
        "header_question_count": int(stats.get("header_question_count", 0) or 0),
        "instruction_question_count": int(stats.get("instruction_question_count", 0) or 0),
        "footer_question_count": int(stats.get("footer_question_count", 0) or 0),
        "question_region_hit_count": int(stats.get("question_region_hit_count", 0) or 0),
        "section_nonempty_count": section_nonempty_count,
        "bbox_negative_count": int(stats.get("bbox_negative_count", 0) or 0),
        "bbox_giant_count": int(stats.get("bbox_giant_count", 0) or 0),
        "answer_bbox_usable_count": sum(1 for q in questions if q.get("answer_bbox")),
        "overlay_ready": any(q.get("answer_bbox") and q.get("image_url") for q in questions),
        "child_answer_pollution": detect_child_answer_pollution(questions),
        "grouping_reasonable": section_nonempty_count > 0 or len(questions) <= 2,
        "qwen_recorded": qwen_recorded,
        "fingerprint_written": fingerprint_written,
        "major_failure_reason": "",
    }


def bucket_question_count(count: int) -> str:
    if count == 0:
        return "0"
    if count <= 4:
        return "1-4"
    if count <= 9:
        return "5-9"
    if count <= 19:
        return "10-19"
    return "20+"


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    total_questions = sum(result["question_count"] for result in results)
    total_section_nonempty = sum(result["section_nonempty_count"] for result in results)
    total_question_region_hits = sum(result["question_region_hit_count"] for result in results)
    answer_region_detected = sum(
        1
        for result in results
        if any(region.get("label") == "answer_region" for region in result["document_classification"].get("layout_regions", []))
    )
    failure_reasons = Counter(result["major_failure_reason"] or "none" for result in results)
    page_type_counter = Counter(result["page_type"] for result in results)
    subject_counter = Counter(result["subject"] for result in results)
    status_counter = Counter(result["final_status"] for result in results)
    question_bucket_counter = Counter(bucket_question_count(result["question_count"]) for result in results)

    return {
        "sample_count": len(results),
        "page_type_distribution": dict(page_type_counter),
        "subject_distribution": dict(subject_counter),
        "status_distribution": dict(status_counter),
        "question_count_distribution": dict(question_bucket_counter),
        "non_homework_cover_unknown_question_count": sum(
            result["question_count"]
            for result in results
            if result["page_type"] in {"non_homework", "cover_or_instruction_page", "unknown"}
        ),
        "non_homework_cover_unknown_pages_with_questions": sum(
            1
            for result in results
            if result["page_type"] in {"non_homework", "cover_or_instruction_page", "unknown"} and result["question_count"] > 0
        ),
        "meta_like_question_count": sum(result["meta_like_question_count"] for result in results),
        "header_question_count": sum(result["header_question_count"] for result in results),
        "instruction_question_count": sum(result["instruction_question_count"] for result in results),
        "footer_question_count": sum(result["footer_question_count"] for result in results),
        "section_nonempty_rate": round(total_section_nonempty / total_questions, 4) if total_questions else 0.0,
        "question_region_hit_rate": round(total_question_region_hits / total_questions, 4) if total_questions else 0.0,
        "answer_region_detected_images": answer_region_detected,
        "bbox_negative_count": sum(result["bbox_negative_count"] for result in results),
        "bbox_giant_count": sum(result["bbox_giant_count"] for result in results),
        "qwen_recorded_images": sum(1 for result in results if result["qwen_recorded"]),
        "image_fingerprint_written_images": sum(1 for result in results if result["fingerprint_written"]),
        "completed_empty_count": sum(1 for result in results if result["completed_empty"]),
        "answer_bbox_usable_images": sum(1 for result in results if result["answer_bbox_usable_count"] > 0),
        "failure_reason_distribution": dict(failure_reasons),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair-1 / Step 1 local evaluation against a local Codex backend")
    parser.add_argument("--base", default="http://127.0.0.1:8002")
    parser.add_argument("--samples-dir", default=str(DEFAULT_SAMPLE_DIR))
    parser.add_argument("--phone", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--poll-interval", type=float, default=3.0)
    parser.add_argument("--timeout-sec", type=float, default=90.0)
    parser.add_argument("--upload-gap-sec", type=float, default=13.0)
    parser.add_argument("--json-out", default="/tmp/repair1_step1_eval.json")
    parser.add_argument(
        "--allow-paid-ocr",
        action="store_true",
        default=False,
        help="Allow real paid OCR calls (requires --max-paid-calls)",
    )
    parser.add_argument(
        "--max-paid-calls",
        type=int,
        default=0,
        help="Maximum paid OCR calls when --allow-paid-ocr is set",
    )
    args = parser.parse_args()

    # Validate gate
    if args.allow_paid_ocr and args.max_paid_calls <= 0:
        print("ERROR: --allow-paid-ocr requires --max-paid-calls N (positive integer)", file=sys.stderr)
        return 3

    paid_ocr_used: list[int] = [0]
    print(
        f"[paid_ocr] gate: allowed={args.allow_paid_ocr} max_calls={args.max_paid_calls}",
        file=sys.stderr,
    )

    samples_dir = Path(args.samples_dir)
    images = load_images(samples_dir)
    if not images:
        print(json.dumps({"ok": False, "reason": "no_samples"}))
        return 1

    session, login_status_code, login_ok = login(args.base, args.phone, args.password)
    if not login_ok:
        print(json.dumps({"ok": False, "reason": "login_failed", "login_status_code": login_status_code}))
        return 2

    results: list[dict[str, Any]] = []
    last_upload_at = 0.0
    for image_path in images:
        wait_for = args.upload_gap_sec - (time.time() - last_upload_at)
        if wait_for > 0:
            time.sleep(wait_for)
        result = evaluate_one(
            session,
            args.base,
            image_path,
            poll_interval=args.poll_interval,
            timeout_sec=args.timeout_sec,
            allow_paid_ocr=args.allow_paid_ocr,
            max_paid_calls=args.max_paid_calls,
            paid_ocr_used=paid_ocr_used,
            db_path=DB_PATH,
        )
        last_upload_at = time.time()
        results.append(result)

    # Print summary
    skipped = sum(1 for r in results if r["final_status"] == "skipped_paid_ocr")
    maxed = sum(1 for r in results if r["final_status"] == "max_paid_calls_reached")
    cached = sum(1 for r in results if r.get("status_sequence") == ["cached"])
    print(
        f"[paid_ocr] summary: paid_used={paid_ocr_used[0]} cached={cached} skipped={skipped} max_reached={maxed}",
        file=sys.stderr,
    )

    payload = {
        "ok": True,
        "base": args.base,
        "sample_dir": str(samples_dir),
        "login_status_code": login_status_code,
        "login_ok": login_ok,
        "results": results,
        "aggregate": aggregate(results),
    }
    Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(
        {
            "ok": True,
            "sample_count": len(results),
            "json_out": args.json_out,
            "status_distribution": payload["aggregate"]["status_distribution"],
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
