"""
作业识别相关 API 路由。
parse-jobs / questions / parse-history
"""
import asyncio
import uuid
import time

from fastapi import APIRouter, Request, Depends, File, UploadFile, Form, BackgroundTasks, HTTPException

from models import ParseJob, Question, JobStatus, QuestionStatus, TutorRequest, TutorResponse
from dependencies import get_current_user
from job_store import _jobs, _ts, _credit_balances, _model_calls, _tutor_chats
from pipeline import enqueue_parse_job, worker_process_job, save_result
import db as _db
from db import get_active_pricing
from logger import info as _log_info, warning as _log_warn, error as _log_error, debug
from logger import info, error
from model_logger import make_log_entry
from deepseek_client import DeepSeekClient
from tutor_prompt import build_tutor_messages
import oss_client as _oss
from schemas.recognition import RecognitionImage, build_recognition_document
from vision_client import QwenVLClient
from pydantic import BaseModel, Field
from pydantic import BaseModel as PydanticBase
from typing import Optional, List

from limiter import limiter

router = APIRouter()



# ─── 0. POST /api/parse-jobs/init ──────────────────────────
class InitJobRequest(BaseModel):
    client_upload_id: str
    file_name: str
    file_size: int
    mime_type: str

@router.post("/api/parse-jobs/init")
@limiter.limit("10/minute")
async def init_parse_job(body: InitJobRequest, request: Request, user: tuple = Depends(get_current_user)):
    """两段式上传第一步：创建任务，立即返回 job_id。不做任何图片处理。"""
    t0 = time.time()
    parent_id, child_id = user
    cid = body.client_upload_id

    # 幂等：child_id + client_upload_id 已存在 → 直接返回
    existing = _db.get_existing_job_by_client_upload(child_id, cid)
    if existing:
        ejid, estatus, _, _ = existing
        _log_info(f"api_init_dedup jid={ejid} status={estatus} client_upload_id={cid}")
        return {"ok": True, "data": {"job_id": ejid, "status": estatus, "file_name": body.file_name}, "request_id": uuid.uuid4().hex}

    # 内存去重兜底
    for jid_existing, j_existing in _jobs.items():
        cu_id = j_existing.get("client_upload_id") or j_existing.get("client_task_id", "")
        if cu_id == cid:
            estatus = str(j_existing["job"].status) if "job" in j_existing and hasattr(j_existing.get("job"), "status") else j_existing.get("status", "created")
            _log_info(f"api_init_dedup_memory jid={jid_existing} status={estatus}")
            return {"ok": True, "data": {"job_id": jid_existing, "status": estatus, "file_name": body.file_name}, "request_id": uuid.uuid4().hex}

    jid = uuid.uuid4().hex[:12]
    now = _ts()

    # 立即落库，status=created
    _db.save_parse_job(jid, child_id, parent_id, body.file_name, 0, "created", now,
                       client_upload_id=cid)
    enqueue_parse_job(jid, {
        "job": ParseJob(job_id=jid, status=JobStatus.created, questions_count=0,
                        created_at=now, file_name=body.file_name),
        "questions": [], "poll_count": 0,
        "child_id": child_id, "parent_id": parent_id,
        "client_task_id": cid, "client_upload_id": cid,
    })

    t1 = time.time()
    _log_info(f"api_init_created jid={jid} elapsed_ms={int((t1-t0)*1000)} client_upload_id={cid}")
    return {"ok": True, "data": {"job_id": jid, "status": "created", "file_name": body.file_name}, "request_id": uuid.uuid4().hex}

# ─── 0.5. POST /api/parse-jobs/{job_id}/upload ──────────────
@router.post("/api/parse-jobs/{job_id}/upload")
@limiter.limit("5/minute")
async def upload_to_job(
    job_id: str,
    file: UploadFile = File(...),
    request: Request = None,
    user: tuple = Depends(get_current_user),
):
    """两段式上传第二步：上传图片到已创建的任务，启动后台 worker。"""
    t0 = time.time()
    parent_id, child_id = user

    # 校验 job 存在 + child_id 匹配
    job_entry = _jobs.get(job_id)
    if not job_entry or job_entry.get("child_id") != child_id:
        # 查 DB
        row = _db.get_job_by_client_upload_id(child_id, job_entry.get("client_upload_id", "") if job_entry else "")
        if not row:
            return {"ok": False, "code": "not_found", "message": "任务不存在", "request_id": uuid.uuid4().hex}

    # 校验未被删除
    try:
        c = _db._conn()
        del_row = c.execute("SELECT deleted_at FROM parse_jobs WHERE job_id = ?", (job_id,)).fetchone()
        c.close()
        if del_row and del_row["deleted_at"]:
            return {"ok": False, "code": "deleted", "message": "任务已删除", "request_id": uuid.uuid4().hex}
    except Exception:
        pass

    # 读取图片
    try:
        contents = await file.read()
    except Exception as fe:
        _log_info(f"api_upload_file_read_failed jid={job_id} error={fe}")
        return {"ok": False, "code": "file_read_failed", "message": "文件读取失败", "request_id": uuid.uuid4().hex}

    now = _ts()
    t1 = time.time()
    _log_info(f"api_upload_received jid={job_id} bytes={len(contents)} elapsed_ms={int((t1-t0)*1000)}")

    # 更新状态 + 启动 worker
    if job_entry and "job" in job_entry:
        job_entry["job"].status = JobStatus.uploaded
    _db.save_parse_job(job_id, child_id, parent_id, file.filename, 0, "uploaded", now,
                       client_upload_id=job_entry.get("client_upload_id", "") if job_entry else "")

    asyncio.create_task(worker_process_job(job_id, contents, file, now, parent_id, child_id))
    t2 = time.time()
    _log_info(f"api_upload_done jid={job_id} elapsed_ms={int((t2-t0)*1000)}")

    # ── 图片指纹写入（只写不读）──
    try:
        from image_fingerprint import compute_fingerprints
        fp = compute_fingerprints(contents)
        fp["parent_id"] = parent_id
        fp["child_id"] = child_id
        fp["job_id"] = job_id
        _db.save_image_fingerprint(fp)
    except Exception as e:
        _log_warn(f"fp_write_fail jid={job_id} err={type(e).__name__}:{e}")

    return {"ok": True, "data": {"job_id": job_id, "status": "uploaded", "file_name": file.filename}, "request_id": uuid.uuid4().hex}

# ─── 1. POST /api/parse-jobs ───────────────────────────────
@router.post("/api/parse-jobs")
@limiter.limit("5/minute")
async def create_parse_job(
    file: UploadFile = File(...),
    client_task_id: str = Form(None),
    page_range: str = Form("1"),
    source_type: str = Form("web_upload"),
    request: Request = None,
    background_tasks: BackgroundTasks = None,
    user: tuple = Depends(get_current_user),
):
    t0 = time.time()
    _log_info(f"api_handler_enter client_task_id={client_task_id} t0={t0:.3f}")
    try:
        parent_id, child_id = user

        # ─── 幂等去重：内存 + DB ───
        if client_task_id:
            # 1. 内存去重（兼容 enqueue_parse_job 和 load_all 两种结构）
            for jid_existing, j_existing in _jobs.items():
                cu_id = j_existing.get("client_upload_id") or j_existing.get("client_task_id", "")
                if cu_id == client_task_id:
                    # 兼容两种数据格式：有 'job' 子对象的和扁平 dict 的
                    if "job" in j_existing and hasattr(j_existing["job"], "status"):
                        estatus = str(j_existing["job"].status)
                    else:
                        estatus = j_existing.get("status", "uploaded")
                    _log_info(f"api_dedup_memory jid={jid_existing} status={estatus}")
                    return {"ok": True, "data": {"job_id": jid_existing, "status": estatus, "file_name": file.filename}, "request_id": uuid.uuid4().hex}
            # 2. DB 去重
            existing = _db.get_existing_job_by_client_upload(child_id, client_task_id)
            if existing:
                ejid, estatus, eqcount, efname = existing
                _log_info(f"api_dedup_db jid={ejid} status={estatus}")
                return {"ok": True, "data": {"job_id": ejid, "status": estatus, "file_name": efname or file.filename}, "request_id": uuid.uuid4().hex}

        jid = uuid.uuid4().hex[:12]
        now = _ts()
        t1 = time.time()
        _log_info(f"api_before_file_read jid={jid} elapsed_ms={int((t1-t0)*1000)}")

        # ── file.read() — 包裹 try，失败时标记 job failed ──
        try:
            contents = await file.read()
        except Exception as fe:
            tfe = time.time()
            _log_info(f"api_file_read_failed jid={jid} elapsed_ms={int((tfe-t0)*1000)} error={fe}")
            # 创建 failed 任务记录，避免脏状态
            enqueue_parse_job(jid, {
                "job": ParseJob(job_id=jid, status=JobStatus.failed,
                                questions_count=0, created_at=now, file_name=file.filename),
                "questions": [], "poll_count": 0,
                "child_id": child_id, "parent_id": parent_id,
                "client_task_id": client_task_id,
                "client_upload_id": client_task_id or "",
            })
            _db.save_parse_job(jid, child_id, parent_id, file.filename, 0, "failed", now,
                               client_upload_id=client_task_id or "", error_code="file_read_failed")
            return {"ok": False, "code": "file_read_failed", "message": "文件读取失败，请重试", "request_id": uuid.uuid4().hex}

        t2 = time.time()
        _log_info(f"api_after_file_read jid={jid} bytes={len(contents)} elapsed_ms={int((t2-t0)*1000)}")

        # ── Step 2: 同图去重 — 指纹命中后复用历史 job，跳过 OCR/Qwen ──
        try:
            from image_fingerprint import compute_fingerprints
            fp = compute_fingerprints(contents)
            fp["parent_id"] = parent_id
            fp["child_id"] = child_id
            fp["job_id"] = jid
            reusable = _db.find_reusable_job_by_fingerprint(
                parent_id, child_id,
                fp.get("original_sha256"), fp.get("ahash"), fp.get("dhash"),
                fp.get("aspect_ratio"),
            )
            if reusable:
                source_job_id, source_status, source_qcount, ah_dist, dh_dist = reusable
                _log_info(
                    f"DEDUP_HIT jid={jid} source_job_id={source_job_id} "
                    f"source_status={source_status} qcount={source_qcount} "
                    f"ahash_dist={ah_dist} dhash_dist={dh_dist}"
                )
                # Save fingerprint for this new upload too (links jid to same image)
                _db.save_image_fingerprint(fp)
                return {
                    "ok": True,
                    "data": {
                        "job_id": source_job_id,
                        "status": source_status,
                        "file_name": file.filename,
                        "reused": True,
                        "source_job_id": source_job_id,
                    },
                    "request_id": uuid.uuid4().hex,
                }
            else:
                _log_info(f"DEDUP_MISS jid={jid} reason=new_image")
        except Exception as e:
            _log_warn(f"dedup_check_fail jid={jid} err={type(e).__name__}:{e} (fallthrough to normal)")
        # ── end dedup ──

        # ── 注册任务到内存 + DB（file.read 成功后立即落库）──
        enqueue_parse_job(jid, {
            "job": ParseJob(job_id=jid, status=JobStatus.uploaded,
                            questions_count=0, created_at=now, file_name=file.filename),
            "questions": [], "poll_count": 0,
            "child_id": child_id,
            "parent_id": parent_id,
            "client_task_id": client_task_id,
            "client_upload_id": client_task_id or "",
        })
        _db.save_parse_job(jid, child_id, parent_id, file.filename, 0, JobStatus.uploaded, now,
                           client_upload_id=client_task_id or "")
        t3 = time.time()
        _log_info(f"api_job_saved jid={jid} elapsed_ms={int((t3-t0)*1000)}")

        # ── 启动后台 worker ──
        asyncio.create_task(worker_process_job(jid, contents, file, now, parent_id, child_id))
        t4 = time.time()
        _log_info(f"api_worker_started jid={jid} elapsed_ms={int((t4-t0)*1000)}")

        # ── 图片指纹写入（只写不读，为去重做准备）──
        try:
            from image_fingerprint import compute_fingerprints
            fp = compute_fingerprints(contents)
            fp["parent_id"] = parent_id
            fp["child_id"] = child_id
            fp["job_id"] = jid
            _db.save_image_fingerprint(fp)
        except Exception as e:
            _log_warn(f"fp_write_fail jid={jid} err={type(e).__name__}:{e}")

        # ── 返回 ──
        t5 = time.time()
        _log_info(f"api_response_returned jid={jid} elapsed_ms={int((t5-t0)*1000)}")
        return {"ok": True, "data": {"job_id": jid, "status": "uploaded", "file_name": file.filename}, "request_id": uuid.uuid4().hex}
    except Exception as e:
        te = time.time()
        _log_info(f"api_handler_exception client_task_id={client_task_id} elapsed_ms={int((te-t0)*1000)} error={e}")
        return {"ok": False, "code": "parse_failed", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 2. GET /api/parse-jobs/recent (before {job_id} to avoid conflict) ──
@router.get("/api/parse-jobs/recent")
@limiter.limit("20/minute")
async def get_recent_parse_jobs(request: Request, user: tuple = Depends(get_current_user)):
    """返回当前 child 最近 10 条解析任务（DB + 内存合并去重）。"""
    try:
        parent_id, child_id = user

        # 1. 从 DB 查（跨重启存活）
        db_rows = _db.get_recent_parse_jobs(child_id, 20)
        seen = set()

        def add_job(job_id: str, status: str, questions_count: int, file_name: str, created_at: str):
            if job_id in seen:
                return
            seen.add(job_id)
            return {
                "job_id": job_id,
                "status": status,
                "questions_count": questions_count,
                "file_name": file_name,
                "created_at": created_at,
            }

        recent = []
        # 优先内存（更新鲜），排除 failed 状态
        for jid, j in _jobs.items():
            if j.get("child_id") == child_id:
                job = j.get("job")
                # 兼容扁平旧格式：无 job 包装时用 j 本身
                if not job and isinstance(j, dict) and "status" in j:
                    job = j
                if not job or not hasattr(job, "status"):
                    continue
                if hasattr(job, "status") and str(job.status) == "failed":
                    continue
                # 扁平格式可能没有 questions_count 属性，用 .get
                qcount = job.questions_count if hasattr(job, "questions_count") else (job.get("questions_count") if isinstance(job, dict) else 0) or len(j.get("questions", []))
                fname = job.file_name if hasattr(job, "file_name") else (job.get("file_name", "") if isinstance(job, dict) else "")
                cat = job.created_at if hasattr(job, "created_at") else (job.get("created_at", "") if isinstance(job, dict) else "")
                entry = add_job(jid, job.status if hasattr(job, "status") else job.get("status", "?"), qcount, fname, cat)
                if entry:
                    recent.append(entry)

        # 补充 DB 中不在内存的
        for row in db_rows:
            entry = add_job(row["job_id"], row["status"], row["questions_count"],
                            row["file_name"], row["created_at"])
            if entry:
                recent.append(entry)

        recent.sort(key=lambda x: x["created_at"], reverse=True)
        return {"ok": True, "data": recent[:10], "request_id": uuid.uuid4().hex}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "code": "recent_error", "message": str(e), "request_id": uuid.uuid4().hex}


# ─── 2.3.5. GET /api/parse-jobs/{job_id}/recover ────────────
@router.get("/api/parse-jobs/{job_id}/recover")
@limiter.limit("10/minute")
async def recover_by_job_id(job_id: str, request: Request, user: tuple = Depends(get_current_user)):
    """按 job_id 精确恢复任务状态（poll 失败后前端使用）。查内存+DB。"""
    try:
        _, child_id = user
        debug("[diag] recover_by_jid jid={job_id} start")
        # 1. 内存
        j = _jobs.get(job_id)
        if j:
            job = j.get("job")
            if job:
                st = str(job.status) if hasattr(job, "status") else job.get("status", "?")
                qcount = job.questions_count if hasattr(job, "questions_count") else len(j.get("questions", []))
                debug("[diag] recover_by_jid jid={job_id} result=memory status={st} qcount={qcount}")
                # 诊断 grading 字段
                qs = j.get("questions", [])
                _wg = sum(1 for q in qs if (getattr(q, "is_correct", None) is not None) or (isinstance(q, dict) and q.get("is_correct") is not None))
                debug("[diag] recover_return jid={job_id} qcount={qcount} with_grading={_wg}")
                return {"ok": True, "data": {"job_id": job_id, "status": st, "questions_count": qcount, "file_name": getattr(job, "file_name", "") or job.get("file_name", "")}, "request_id": uuid.uuid4().hex}
        # 2. DB
        import sqlite3, json
        c = _db._conn()
        row = c.execute("SELECT status, questions_count, file_name FROM parse_jobs WHERE job_id=? AND deleted_at IS NULL", (job_id,)).fetchone()
        c.close()
        if row:
            debug("[diag] recover_by_jid jid={job_id} result=db status={row['status']} qcount={row['questions_count']}")
            return {"ok": True, "data": {"job_id": job_id, "status": row["status"], "questions_count": row["questions_count"], "file_name": row["file_name"] or ""}, "request_id": uuid.uuid4().hex}
        debug("[diag] recover_by_jid jid={job_id} result=not_found")
        return {"ok": False, "code": "not_found", "message": "任务不存在", "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "recover_error", "message": str(e), "request_id": uuid.uuid4().hex}


# ─── 2.4. GET /api/parse-jobs/recover?client_upload_id=xxx ──
@router.get("/api/parse-jobs/recover")
@limiter.limit("10/minute")
async def recover_parse_job(client_upload_id: str, request: Request, user: tuple = Depends(get_current_user)):
    """上传超时后按 client_upload_id 恢复 job。返回 uploaded/processing/completed 状态。"""
    try:
        parent_id, child_id = user
        if not client_upload_id:
            debug("[diag] recover_get cu=(empty) result=missing_param")
            return {"ok": False, "code": "missing_param", "message": "缺少 client_upload_id", "request_id": uuid.uuid4().hex}
        # 先查内存（更新鲜）
        for jid, j in _jobs.items():
            if j.get("child_id") == child_id and j.get("client_upload_id") == client_upload_id:
                job = j.get("job")
                if job and hasattr(job, "status") and str(job.status) != "failed":
                    st = str(job.status)
                    debug("[diag] recover_get cu={client_upload_id[:16]} result=found_memory jid={jid} status={st}")
                    return {"ok": True, "data": {"job_id": jid, "status": st, "questions_count": job.questions_count or len(j.get("questions", [])), "file_name": job.file_name}, "request_id": uuid.uuid4().hex}
        # 再查 DB（跨重启）
        row = _db.get_job_by_client_upload_id(child_id, client_upload_id)
        if row:
            debug("[diag] recover_get cu={client_upload_id[:16]} result=found_db jid={row.get('job_id','?')} status={row.get('status','?')}")
            return {"ok": True, "data": row, "request_id": uuid.uuid4().hex}
        debug("[diag] recover_get cu={client_upload_id[:16]} result=not_found")
        return {"ok": False, "code": "not_found", "message": "未找到对应任务", "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "recover_error", "message": str(e), "request_id": uuid.uuid4().hex}


# ─── 2.5. DELETE /api/parse-jobs/{job_id} (before GET {job_id} to avoid conflict) ──
@router.delete("/api/parse-jobs/{job_id}")
@limiter.limit("5/minute")
async def delete_parse_job(job_id: str, request: Request, user: tuple = Depends(get_current_user)):
    """软删除解析任务，deleted_at 写入当前时间。"""
    try:
        _, child_id = user
        ok = _db.soft_delete_parse_job(job_id, child_id)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail={"ok": False, "code": "not_found", "message": "任务不存在或已删除", "request_id": uuid.uuid4().hex},
            )
        # 同时从内存移除（下次 restart 也不会被 recent 返回）
        _jobs.pop(job_id, None)
        return {"ok": True, "data": {"job_id": job_id, "deleted": True}, "request_id": uuid.uuid4().hex}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "code": "delete_error", "message": str(e), "request_id": uuid.uuid4().hex}


# ─── 3. GET /api/parse-jobs/{job_id} ───────────────────────
@router.get("/api/parse-jobs/{job_id}")
@limiter.limit("30/minute")
async def get_parse_job_status(job_id: str, request: Request, user: tuple = Depends(get_current_user)):
    try:
        await asyncio.sleep(0.3)
        if job_id not in _jobs:
            raise HTTPException(status_code=404, detail={"ok": False, "code": "not_found", "message": "任务不存在", "request_id": uuid.uuid4().hex})
        j = _jobs[job_id]
        job_obj = j.get("job")
        # 兼容扁平旧格式：无 job 包装时直接从顶层读 status
        if not job_obj and isinstance(j, dict) and "status" in j:
            job_obj = j  # 整个 j 就是 job 数据
        # 防御：job entry 被异常路径破坏时返回 404
        if not job_obj:
            raise HTTPException(status_code=404, detail={"ok": False, "code": "not_found", "message": "任务已过期或数据异常", "request_id": uuid.uuid4().hex})
        # 兼容两种格式：Pydantic ParseJob 对象 或 load_all 恢复的 dict
        if hasattr(job_obj, "status"):
            job_status = job_obj.status
        elif isinstance(job_obj, dict):
            job_status = job_obj.get("status", "")
            if not job_status:
                raise HTTPException(status_code=404, detail={"ok": False, "code": "not_found", "message": "任务已过期或数据异常", "request_id": uuid.uuid4().hex})
        else:
            raise HTTPException(status_code=404, detail={"ok": False, "code": "not_found", "message": "任务已过期或数据异常", "request_id": uuid.uuid4().hex})
        j["poll_count"] = j.get("poll_count", 0) + 1
        if hasattr(job_obj, "updated_at"):
            job_obj.updated_at = _ts()
        # 返回数据：Pydantic → model_dump()，dict → 直接返回
        if hasattr(job_obj, "model_dump"):
            data = job_obj.model_dump()
        else:
            data = job_obj
        data["job_id"] = data.get("job_id") or job_id
        data["image_url"] = data.get("image_url") or j.get("image_url", "")
        data["document_classification"] = (
            j.get("document_classification")
            or (j.get("recognition") or {}).get("meta", {}).get("document_classification", {})
        )
        # 附加调试信息：error_code / progress
        data["error_code"] = j.get("error_code", "")
        data["progress"] = j.get("progress", "")
        # 诊断日志（每 10 次 poll 记一次，避免刷屏）
        pc = j.get("poll_count", 0)
        if pc % 10 == 1:
            debug("[diag] poll_get_job jid={job_id} status={job_status} qcount={job_obj.questions_count} poll=#{pc}")
        return {"ok": True, "data": data, "request_id": uuid.uuid4().hex}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "code": "status_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 4. GET /api/parse-jobs/{job_id}/questions ─────────────
@router.get("/api/parse-jobs/{job_id}/questions")
@limiter.limit("30/minute")
async def get_parse_job_questions(job_id: str, request: Request, user: tuple = Depends(get_current_user)):
    try:
        await asyncio.sleep(0.3)
        # 1) 内存优先（活跃任务，且 questions 非空）
        if job_id in _jobs and _jobs[job_id].get("questions"):
            qs = _jobs[job_id]["questions"]
            # 兼容 load_all 恢复的 dict 和运行时 Pydantic 对象
            data = [q.model_dump() if hasattr(q, "model_dump") else q for q in qs]
            # 诊断
            _with_g = sum(1 for d in data if d.get("is_correct") is not None)
            _with_sa = sum(1 for d in data if d.get("student_answer"))
            debug("[diag] questions_return jid={job_id} source=memory qcount={len(data)} with_grading={_with_g} with_child_answer={_with_sa}")
            return {"ok": True, "data": data, "request_id": uuid.uuid4().hex}
        # 2) DB 回退（跨重启 / 历史记录）— data 列存的是 JSON dict，直接返回
        db_data = _db.get_job_data(job_id)
        if db_data and db_data.get("questions"):
            return {"ok": True, "data": db_data["questions"], "request_id": uuid.uuid4().hex}
        if db_data:
            return {"ok": True, "data": [], "request_id": uuid.uuid4().hex}
        raise HTTPException(status_code=404, detail={"ok": False, "code": "not_found", "message": "任务不存在", "request_id": uuid.uuid4().hex})
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "code": "questions_error", "message": str(e), "request_id": uuid.uuid4().hex}


@router.get("/api/parse-jobs/{job_id}/recognition")
@limiter.limit("30/minute")
async def get_parse_job_recognition(job_id: str, request: Request, user: tuple = Depends(get_current_user)):
    try:
        await asyncio.sleep(0.1)
        if job_id in _jobs and _jobs[job_id].get("recognition"):
            return {"ok": True, "data": _jobs[job_id]["recognition"], "request_id": uuid.uuid4().hex}

        db_data = _db.get_job_data(job_id)
        if not db_data:
            raise HTTPException(status_code=404, detail={"ok": False, "code": "not_found", "message": "任务不存在", "request_id": uuid.uuid4().hex})

        if db_data.get("recognition"):
            return {"ok": True, "data": db_data["recognition"], "request_id": uuid.uuid4().hex}

        image = RecognitionImage(
            id=job_id,
            text=db_data.get("file_name") or job_id,
            source="persisted_job",
            kind="image",
            status="completed",
            file_path=db_data.get("image_url"),
            preprocess_versions=db_data.get("preprocess_versions", []),
        )
        recognition = build_recognition_document(
            image=image,
            raw_questions=db_data.get("questions", []),
            raw_blocks=db_data.get("ocr_blocks", []),
            block_source=db_data.get("ocr_block_source", "persisted_ocr"),
            layout_regions=(db_data.get("document_classification", {}) or {}).get("layout_regions", []),
            meta={
                "job_id": job_id,
                "status": db_data.get("status"),
                "image_url": db_data.get("image_url"),
                "document_classification": db_data.get("document_classification", {}),
            },
        ).model_dump()
        return {"ok": True, "data": recognition, "request_id": uuid.uuid4().hex}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "code": "recognition_error", "message": str(e), "request_id": uuid.uuid4().hex}




# ─── 3.5. GET /api/questions/{question_id} ──────────────────
@router.get("/api/questions/{question_id}")
@limiter.limit("30/minute")
async def get_question_detail(question_id: str, request: Request):
    """获取单题详情，从所有 job 中查找"""
    try:
        for jid, j in _jobs.items():
            for q in j.get("questions", []):
                _qid = getattr(q, "question_id", None) or q.get("question_id", "")
                if _qid == question_id:
                    # 兼容 dict 和 Question 对象
                    _qd = q.model_dump() if hasattr(q, "model_dump") else q
                    return {"ok": True, "data": _qd, "request_id": uuid.uuid4().hex}
        raise HTTPException(status_code=404, detail={"ok": False, "code": "not_found", "message": "题目不存在", "request_id": uuid.uuid4().hex})
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "code": "question_error", "message": str(e), "request_id": uuid.uuid4().hex}


# ─── 3.6. POST /api/questions/{question_id}/status ──────────
class QuestionStatusRequest(BaseModel):
    status: str  # mastered | mistake_book | needs_review
    child_answer: str = ""

@router.post("/api/questions/{question_id}/status")
@limiter.limit("10/minute")
async def update_question_status(question_id: str, body: QuestionStatusRequest, request: Request, user: tuple = Depends(get_current_user)):
    """更新题目状态：已掌握 / 加入错题本"""
    try:
        _, child_id = user
        if body.status not in ("mastered", "mistake_book", "needs_review"):
            return {"ok": False, "code": "invalid_status", "message": "无效状态", "request_id": uuid.uuid4().hex}
        # 写入 question_attempt（基准 Table 12）
        attempt_id = uuid.uuid4().hex[:12]
        _db.save_question_attempt(attempt_id, question_id, child_id, body.status, body.child_answer or "")
        # 加入错题本 → 持久化到 SQLite
        if body.status == "mistake_book":
            mid = _db.save_mistake(child_id, question_id, "unknown", body.child_answer or "")
            return {"ok": True, "data": {"question_id": question_id, "status": body.status, "mistake_id": mid, "attempt_id": attempt_id}, "request_id": uuid.uuid4().hex}
        return {"ok": True, "data": {"question_id": question_id, "status": body.status, "attempt_id": attempt_id}, "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "status_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 4. POST /api/questions/{question_id}/tutor ────────────
@router.post("/api/questions/{question_id}/tutor")
@limiter.limit("10/minute")
async def tutor_question(question_id: str, body: TutorRequest, request: Request, user: tuple = Depends(get_current_user)):
    """DeepSeek 单题辅导 — 基准 Table 11 R4, 不接收图片 Base64"""
    try:
        # Find question from all jobs (兼容 dict 和 Question 对象)
        q_found = None
        for j in _jobs.values():
            for q in j.get("questions", []):
                _qid = getattr(q, "question_id", None) or q.get("question_id", "")
                if _qid == question_id:
                    q_found = q
                    break

        if not q_found:
            raise HTTPException(status_code=404, detail={
                "ok": False, "code": "not_found",
                "message": "题目不存在", "request_id": uuid.uuid4().hex,
            })

        # Credit check — 基准 Table 24: 后端二次校验（按家长计费）
        parent_id, child_id_for_credit = user
        # 优先从 DB 加载余额到内存缓存（避免重启后内存缓存过期覆盖 DB 真实值）
        if parent_id not in _credit_balances:
            c = _db._conn()
            bal_row = c.execute("SELECT balance FROM credit_account WHERE parent_user_id = ?", (parent_id,)).fetchone()
            c.close()
            _credit_balances[parent_id] = bal_row["balance"] if bal_row else 50
        credits = _credit_balances[parent_id]
        if credits <= 0:
            return {
                "ok": True,
                "data": TutorResponse(
                    reply_text="学豆不足，请联系家长充值",
                    chat_limit_reached=True,
                    remaining_rounds=0,
                    credit_balance=credits,
                    request_id=uuid.uuid4().hex,
                ).model_dump(),
                "request_id": uuid.uuid4().hex,
            }

        # Chat limit check — Phase 1: 超限时调用 DeepSeek 摘要前 N 轮 (Table 25)
        history = _tutor_chats.get(question_id, [])
        MAX_ROUNDS = 10
        if len(history) >= MAX_ROUNDS * 2:  # user+assistant pairs
            keep_recent = 2 * 2  # 保留最近 2 轮
            to_summarize = history[:-keep_recent]
            # 调用 DeepSeek 摘要
            try:
                ds = DeepSeekClient()
                summary_messages = [
                    {"role": "system", "content": "请用 2-3 句话总结以下辅导对话的核心内容和已讲到的知识点。"},
                ]
                for m in to_summarize:
                    summary_messages.append({"role": m["role"], "content": m["content"][:500]})
                sum_result = ds.tutor(summary_messages)
                summary_text = sum_result.get("reply_text", "（前序对话摘要）")
            except Exception:
                summary_text = "（前序对话摘要不可用）"
            # 重建：摘要 + 最近轮次
            history = [
                {"role": "system", "content": f"[前序对话摘要] {summary_text}"},
                *history[-keep_recent:],
            ]
            _tutor_chats[question_id] = history

        # Build prompt and call DeepSeek
        t_start = time.time()
        ds = DeepSeekClient()
        feat_code = "deepseek_tutor_initial" if body.mode == "initial" else "deepseek_tutor_followup"
        # 兼容 dict 和 Question 对象
        _qt = q_found.question_text if hasattr(q_found, "question_text") else q_found.get("question_text", "")
        _vd = q_found.visual_description if hasattr(q_found, "visual_description") else q_found.get("visual_description", "")
        messages = build_tutor_messages(
            mode=body.mode,
            question_text=_qt,
            visual_description=_vd or "",
            chat_history=history if body.mode == "followup" else None,
            user_message=body.message,
            action=body.action,
        )
        result = ds.tutor(messages)
        latency_ms = int((time.time() - t_start) * 1000)

        # Save to chat history + deduct credit — SQLite 持久化
        history.append({"role": "user", "content": body.message or _qt})
        history.append({"role": "assistant", "content": result["reply_text"]})
        _tutor_chats[question_id] = history
        _credit_balances[parent_id] = credits - 1
        _db.save_tutor_chat(question_id, history)
        _db.save_credit_balance(parent_id, credits - 1)

        # 写入结构化 ai_tutoring_chat（基准 Table 12，按 sequence_number）
        seq = len(history)
        call_id = uuid.uuid4().hex[:12]
        _db.save_tutor_message(uuid.uuid4().hex[:12], question_id, child_id_for_credit, seq - 1, "user", body.message or _qt)
        _db.save_tutor_message(call_id, question_id, child_id_for_credit, seq, "assistant", result["reply_text"], call_id)

        # ── 成本账本：ai_tutoring_messages + sessions（Hermes 规划 §5.4）──
        session_id = f"sess_{question_id}"
        try:
            _db.upsert_tutoring_session(session_id, question_id, child_id_for_credit)
            _db.save_tutoring_message(uuid.uuid4().hex[:12], session_id, question_id, child_id_for_credit,
                                       seq - 1, "user", body.message or q_found.question_text)
            _db.save_tutoring_message(call_id, session_id, question_id, child_id_for_credit,
                                       seq, "assistant", result["reply_text"],
                                       call_id=call_id, feature_code=feat_code)
        except Exception as _te:
            error(f"[Tutor] ai_tutoring_messages write failed (non-blocking): {_te}")

        # Log model call — SQLite 持久化
        pricing = get_active_pricing("deepseek", "deepseek-v4-flash")
        tutor_trace_id = uuid.uuid4().hex  # 辅导链路 trace
        usage = result.get("usage") or {}
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        cache_hit = usage.get("cache_hit_tokens", 0)
        cache_miss = usage.get("cache_miss_tokens", 0)
        _tlog = make_log_entry(
            task_id="tutor",
            question_id=question_id,
            job_id=question_id,
            provider_name="deepseek",
            model_name="deepseek-v4-flash",
            feature_code=feat_code,
            trace_id=tutor_trace_id,
            latency_ms=result.get("latency_ms", latency_ms),
            success=result["success"],
            error_code=result.get("error"),
            billing_status="free_tier" if result["success"] else "failed",
            prompt_name=f"tutor_{body.mode}",
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_hit_tokens=cache_hit,
            cache_miss_tokens=cache_miss,
            estimated_cost=0.0,
            pricing=pricing,
        )
        _model_calls.append(_tlog)
        _db.save_model_call(_tlog)

        # ── credit_ledger 回写真实成本（Hermes 规划 T6）──
        try:
            actual_cost = _tlog.get("cost_cny", 0.0)
            _db.add_credit_ledger_entry(parent_id, child_id_for_credit, -1, feat_code,
                                         f"辅导: {question_id}", question_id,
                                         feature_code=feat_code, job_id=question_id,
                                         question_id=question_id, call_id=call_id,
                                         actual_cost_cny=actual_cost, credit_delta=-1,
                                         billing_status="free_tier")
            # 同步更新 model_calls.credit_cost
            call_cid = _tlog.get("id", "")
            if call_cid and actual_cost > 0:
                _db._update_model_call_credit(call_cid, -1, actual_cost)
        except Exception as _ce:
            error(f"[Tutor] credit_ledger cost write failed (non-blocking): {_ce}")

        remaining = MAX_ROUNDS - len(history) // 2
        credit_after = credits - 1
        return {
            "ok": True,
            "data": TutorResponse(
                reply_text=result["reply_text"],
                chat_limit_reached=False,
                remaining_rounds=remaining,
                credit_balance=credit_after,
                request_id=uuid.uuid4().hex,
            ).model_dump(),
            "request_id": uuid.uuid4().hex,
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "code": "tutor_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 5. POST /api/questions/{question_id}/vision ───────────
@router.post("/api/questions/{question_id}/vision")
@limiter.limit("5/minute")
async def vision_retry(question_id: str, request: Request = None, user: tuple = Depends(get_current_user)):
    """视觉二次路由：对已有题目的裁切图做 Qwen-VL 重读"""
    try:
        # Find the question from all jobs
        q_found = None
        q_bbox = None
        q_text = ""
        for jid, j in _jobs.items():
            for q in j.get("questions", []):
                _qid = getattr(q, "question_id", None) or q.get("question_id", "")
                if _qid == question_id:
                    q_found = q
                    q_bbox = q.bbox if hasattr(q, "bbox") else q.get("bbox")
                    q_text = q.question_text if hasattr(q, "question_text") else q.get("question_text", "")
                    break

        if not q_found:
            raise HTTPException(status_code=404, detail={
                "ok": False, "code": "not_found",
                "message": "题目不存在", "request_id": uuid.uuid4().hex,
            })

        # 从 OSS 或持久化存储读取原始图片
        import os as _osp
        jid_from_q = question_id[:12]  # question_id = {jid}-{n}-{i}
        img_bytes = None

        # 优先 OSS
        job_data = _jobs.get(jid_from_q, {})
        oss_key = job_data.get("oss_key", "")
        if oss_key:
            try:
                import urllib.request
                signed_url = _oss.get_signed_url(oss_key)
                if signed_url:
                    with urllib.request.urlopen(signed_url) as resp:
                        img_bytes = resp.read()
                    info(f"[Vision] loaded from OSS: {oss_key}")
            except Exception:
                error("[Vision] OSS load failed, try local")

        # 降级：本地文件
        if img_bytes is None:
            img_path = f"/tmp/yomi/{jid_from_q}.jpg"
            if not _osp.path.exists(img_path):
                return {
                    "ok": True,
                    "data": TutorResponse(
                        reply_text=f"[图片已过期] 题目文字: {q_text[:80]}",
                        chat_limit_reached=False, remaining_rounds=2,
                        request_id=uuid.uuid4().hex,
                    ).model_dump(),
                    "request_id": uuid.uuid4().hex,
                }
            with open(img_path, "rb") as _pf:
                img_bytes = _pf.read()

        qwen_vl = QwenVLClient()
        if not qwen_vl._available():
            return {
                "ok": True,
                "data": TutorResponse(
                    reply_text=f"[Qwen-VL 未接入] 题目文字: {q_text[:80]}",
                    chat_limit_reached=False, remaining_rounds=2,
                    request_id=uuid.uuid4().hex,
                ).model_dump(),
                "request_id": uuid.uuid4().hex,
            }

        # 真实调用 Qwen-VL
        t_vs = time.time()
        vl_result = qwen_vl.analyze_question(
            image_bytes=img_bytes,
            bbox=q_bbox or [0, 0, 0, 0],
            question_text=q_text,
        )
        vl_ms = int((time.time() - t_vs) * 1000)
        q_found.visual_description = vl_result["visual_description"]

        # 记录 model_call_log（与 /tutor 对称）
        usage = vl_result.get("usage", {}) if vl_result.get("success") else {}
        pricing = get_active_pricing("aliyun_dashscope", "qwen-vl-plus")
        _vlog = make_log_entry(
            task_id="vision_retry",
            question_id=question_id,
            job_id=question_id,
            provider_name="aliyun_dashscope",
            model_name="qwen-vl-plus",
            feature_code="qwen_vl_parse_homework",
            latency_ms=vl_ms,
            success=vl_result["success"],
            error_code=vl_result.get("error"),
            billing_status="free_tier" if vl_result["success"] else "failed",
            prompt_name="qwen_vl_vision_retry",
            input_tokens=usage.get("prompt_tokens", 0) if isinstance(usage, dict) else 0,
            output_tokens=usage.get("completion_tokens", 0) if isinstance(usage, dict) else 0,
            pricing=pricing,
        )
        _model_calls.append(_vlog)
        _db.save_model_call(_vlog)

        return {
            "ok": True,
            "data": TutorResponse(
                reply_text=vl_result["visual_description"][:500],
                chat_limit_reached=False, remaining_rounds=2,
                request_id=uuid.uuid4().hex,
            ).model_dump(),
            "request_id": uuid.uuid4().hex,
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "code": "vision_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 6. GET /api/me/entitlement ────────────────────────────




# ─── 9. 识图切题历史后端持久化 ─────────────────────────

class ParseHistoryItem(PydanticBase):
    job_id: str
    data_json: str
    updated_at: str


class ParseHistorySaveRequest(PydanticBase):
    job_id: str
    data_json: str  # JSON string of JobHistoryEntry


@router.get("/api/parse-history")
@limiter.limit("30/minute")
async def get_parse_history(request: Request, user: tuple = Depends(get_current_user)):
    """返回当前 child 的识图切题历史（最近 50 条）。"""
    try:
        parent_id, child_id = user
        rows = _db.get_parse_history(child_id)
        return {
            "ok": True,
            "data": [ParseHistoryItem(**r).model_dump() for r in rows],
            "request_id": uuid.uuid4().hex,
        }
    except HTTPException:
        raise
    except Exception as e:
        return {
            "ok": False,
            "code": "parse_history_error",
            "message": str(e),
            "request_id": uuid.uuid4().hex,
        }


@router.post("/api/parse-history")
@limiter.limit("30/minute")
async def save_parse_history(request: Request, body: ParseHistorySaveRequest, user: tuple = Depends(get_current_user)):
    """保存一条识图切题历史到后端（清缓存后可恢复）。"""
    try:
        parent_id, child_id = user
        _db.save_parse_history(child_id, body.job_id, body.data_json)
        return {
            "ok": True,
            "message": "saved",
            "request_id": uuid.uuid4().hex,
        }
    except HTTPException:
        raise
    except Exception as e:
        return {
            "ok": False,
            "code": "parse_history_save_error",
            "message": str(e),
            "request_id": uuid.uuid4().hex,
        }
