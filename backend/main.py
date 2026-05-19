"""
悠米伴学 API — FastAPI 后端骨架
Phase 1 真实 AI 接入，DeepSeek tutor + Qwen-VL 已上线
"""

import asyncio
import uuid
import json
from datetime import datetime, timezone
from fastapi import FastAPI, Request, HTTPException, File, UploadFile, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import TypeVar, Generic, Optional, List
import os
import time
from ocr_client import AliyunOCRClient
from question_cutter import cut_to_questions
from model_logger import make_log_entry
from db import get_active_pricing
from logger import info, warning, error, debug
from vision_client import QwenVLClient
from deepseek_client import DeepSeekClient
from tutor_prompt import build_tutor_messages
import oss_client as _oss
from auth import (
    create_token, verify_token, hash_password, verify_password,
    init_seed_users, get_parent_by_phone, get_children,
    create_child_token, verify_child_token, generate_child_secret,
    ParentUser, ChildProfile, _parents, _children,
)
import db as _db
from logger import info as _log_info, warning as _log_warn, error as _log_error

# 确保 root Python 能找到 hermes_me 安装的包（systemd 以 root 运行）
import sys
_site = "/home/hermes_me/.local/lib/python3.10/site-packages"
if _site not in sys.path:
    sys.path.insert(0, _site)

from slowapi import Limiter
from slowapi.util import get_remote_address

from models import (
    EntitlementStatus, JobStatus, QuestionStatus,
    ApiResponse, ParseJob, Question,
    TutorRequest, TutorResponse, Entitlement,
)
from dependencies import get_current_user, get_current_child
from job_store import (
    _jobs, _model_calls, _tutor_chats, _credit_balances,
    _deferred_vision_tasks, MOCK_ENTITLEMENT, MOCK_QUESTIONS, _ts,
)
from pipeline import enqueue_parse_job, save_result, grade_answers, worker_process_job

# ─── DB 初始化 ─────────────────────────────────────────────
_db.init()

# ─── 种子用户初始化 ─────────────────────────────────────────
init_seed_users()

# Load .env
from pathlib import Path
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
from enum import Enum

app = FastAPI(title="悠米伴学 API", version="0.1.0")

# ─── CORS ──────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001", "http://39.107.119.136:3000", "http://39.107.119.136:3001", "https://youmi.xyz"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── 限流 ──────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app.state.limiter = limiter

from slowapi.errors import RateLimitExceeded

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"ok": False, "code": "rate_limited", "message": "请求太频繁，请稍后再试", "request_id": uuid.uuid4().hex},
    )

# ─── 鉴权依赖 ──────────────────────────────────────────────

from fastapi import Depends, Header
from typing import Annotated

def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    x_child_id: Annotated[str | None, Header(alias="X-Child-Id")] = None,
):
    """FastAPI 依赖：验证 JWT（parent token 或 child token），返回 (parent_id, child_id)"""
    if not authorization:
        raise HTTPException(status_code=401, detail={"ok": False, "code": "unauthorized", "message": "请先登录"})

    # 优先尝试 child token（有 X-Child-Id 头）
    if x_child_id:
        secret = _db.get_child_jwt_secret(x_child_id)
        if secret:
            payload = verify_child_token(authorization, secret)
            if payload and payload.get("child_id") == x_child_id:
                return payload.get("parent_id", ""), payload.get("child_id", "")
        raise HTTPException(status_code=401, detail={"ok": False, "code": "child_token_invalid", "message": "子 token 无效"})

    # 回退 parent token
    payload = verify_token(authorization)
    if not payload:
        raise HTTPException(status_code=401, detail={"ok": False, "code": "token_expired", "message": "登录已过期，请重新登录"})
    return payload.get("parent_id", ""), payload.get("child_id", "")


def get_current_child(
    authorization: Annotated[str | None, Header()] = None,
    x_child_id: Annotated[str | None, Header(alias="X-Child-Id")] = None,
):
    """FastAPI 依赖：验证 child JWT（每 child 独立 secret），返回 (child_id, parent_id)"""
    if not authorization or not x_child_id:
        raise HTTPException(status_code=401, detail={"ok": False, "code": "unauthorized", "message": "缺少 Authorization 或 X-Child-Id"})
    secret = _db.get_child_jwt_secret(x_child_id)
    if not secret:
        raise HTTPException(status_code=401, detail={"ok": False, "code": "child_not_found", "message": f"孩子 {x_child_id} 不存在"})
    payload = verify_child_token(authorization, secret)
    if not payload:
        raise HTTPException(status_code=401, detail={"ok": False, "code": "token_invalid", "message": "子 token 无效或已过期"})
    if payload.get("child_id") != x_child_id:
        raise HTTPException(status_code=401, detail={"ok": False, "code": "child_mismatch", "message": "X-Child-Id 与 token 不匹配"})
    return payload.get("child_id", ""), payload.get("parent_id", "")

# ─── 异常处理 ──────────────────────────────────────────────
@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    # 提取 message：detail 可能是 dict 或 string
    if isinstance(exc.detail, dict):
        msg = exc.detail.get("message", str(exc.detail))
        code = exc.detail.get("code", f"http_{exc.status_code}")
        rid = exc.detail.get("request_id", uuid.uuid4().hex)
    else:
        msg = str(exc.detail)
        code = f"http_{exc.status_code}"
        rid = uuid.uuid4().hex
    return JSONResponse(status_code=exc.status_code, content={
        "ok": False, "code": code, "message": msg, "request_id": rid,
    })

@app.exception_handler(Exception)
async def universal_exc(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={
        "ok": False, "code": "internal_error",
        "message": "服务器内部错误", "request_id": uuid.uuid4().hex
    })

# ─── 鉴权端点 ──────────────────────────────────────────────

class LoginRequest(BaseModel):
    phone: str
    password: str

class RegisterRequest(BaseModel):
    phone: str
    password: str
    name: str = ""


@app.post("/api/auth/login")
@limiter.limit("10/minute")
async def login(body: LoginRequest, request: Request = None):
    """手机号 + 密码登录，返回 JWT token"""
    parent = get_parent_by_phone(body.phone)
    if not parent or not verify_password(body.password, parent.password_hash):
        return {"ok": False, "code": "invalid_credentials", "message": "手机号或密码错误"}
    children = get_children(parent.id)
    child_id = children[0].id if children else ""
    token = create_token({
        "parent_id": parent.id,
        "child_id": child_id,
        "phone": parent.phone,
    })
    return {
        "ok": True,
        "data": {
            "token": token,
            "parent": {"id": parent.id, "name": parent.name, "phone": parent.phone},
            "children": [{"id": c.id, "name": c.name} for c in children],
            "active_child_id": child_id,
        },
    }


@app.post("/api/auth/register")
@limiter.limit("3/minute")
async def register(body: RegisterRequest, request: Request = None):
    """注册（Phase 1 简化：自动创建 parent + 一个 child）"""
    if get_parent_by_phone(body.phone):
        return {"ok": False, "code": "phone_taken", "message": "该手机号已注册"}
    pid = f"p{len(_parents) + 1:03d}"
    cid = f"c{len(_children) + 1:03d}"
    _parents[pid] = ParentUser(id=pid, phone=body.phone, password_hash=hash_password(body.password), name=body.name)
    _children[cid] = ChildProfile(id=cid, parent_id=pid, name=body.name + "的宝宝")
    # 持久化到 SQLite
    _db.save_parent_user(pid, body.phone, _parents[pid].password_hash, body.name)
    _db.save_child_profile(cid, pid, body.name + "的宝宝")
    token = create_token({"parent_id": pid, "child_id": cid, "phone": body.phone})
    return {
        "ok": True,
        "data": {
            "token": token,
            "parent": {"id": pid, "name": body.name, "phone": body.phone},
            "children": [{"id": cid, "name": body.name + "的宝宝"}],
            "active_child_id": cid,
        },
    }


@app.get("/api/auth/children")
@limiter.limit("10/minute")
async def list_children(request: Request, user: tuple = Depends(get_current_user)):
    """获取当前家长的所有孩子"""
    parent_id, _ = user
    children = get_children(parent_id)
    return {"ok": True, "data": [{"id": c.id, "name": c.name, "avatar": c.avatar} for c in children]}


# ─── 健康检查 ──────────────────────────────────────────────
@app.get("/health")
def health_check():
    qwen_ok = False
    try:
        from vision_client import QwenVLClient
        qwen_ok = QwenVLClient()._available()
    except Exception:
        pass
    return {"ok": True, "message": "悠米伴学 API 运行中", "qwen_vl": qwen_ok}

# ─── 0. POST /api/parse-jobs/init ──────────────────────────
class InitJobRequest(BaseModel):
    client_upload_id: str
    file_name: str
    file_size: int
    mime_type: str

@app.post("/api/parse-jobs/init")
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
@app.post("/api/parse-jobs/{job_id}/upload")
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

    return {"ok": True, "data": {"job_id": job_id, "status": "uploaded", "file_name": file.filename}, "request_id": uuid.uuid4().hex}

# ─── 1. POST /api/parse-jobs ───────────────────────────────
@app.post("/api/parse-jobs")
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

        # ── 返回 ──
        t5 = time.time()
        _log_info(f"api_response_returned jid={jid} elapsed_ms={int((t5-t0)*1000)}")
        return {"ok": True, "data": {"job_id": jid, "status": "uploaded", "file_name": file.filename}, "request_id": uuid.uuid4().hex}
    except Exception as e:
        te = time.time()
        _log_info(f"api_handler_exception client_task_id={client_task_id} elapsed_ms={int((te-t0)*1000)} error={e}")
        return {"ok": False, "code": "parse_failed", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 2. GET /api/parse-jobs/recent (before {job_id} to avoid conflict) ──
@app.get("/api/parse-jobs/recent")
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
@app.get("/api/parse-jobs/{job_id}/recover")
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
@app.get("/api/parse-jobs/recover")
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
@app.delete("/api/parse-jobs/{job_id}")
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
@app.get("/api/parse-jobs/{job_id}")
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
@app.get("/api/parse-jobs/{job_id}/questions")
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

# ─── 3.5. GET /api/questions/{question_id} ──────────────────
@app.get("/api/questions/{question_id}")
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

@app.post("/api/questions/{question_id}/status")
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
@app.post("/api/questions/{question_id}/tutor")
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
@app.post("/api/questions/{question_id}/vision")
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
@app.get("/api/me/entitlement")
@limiter.limit("20/minute")
async def get_entitlement(request: Request, user: tuple = Depends(get_current_user)):
    try:
        parent_id, child_id = user
        # 从 credit_account 真实读取余额
        c = _db._conn()
        bal_row = c.execute("SELECT balance FROM credit_account WHERE parent_user_id = ?", (parent_id,)).fetchone()
        c.close()
        balance = bal_row["balance"] if bal_row else 50
        # 状态判断
        if balance > 10:
            estatus = EntitlementStatus.credit_enough
        elif balance > 0:
            estatus = EntitlementStatus.credit_low
        else:
            estatus = EntitlementStatus.credit_empty
        return {
            "ok": True,
            "data": Entitlement(
                user_id=parent_id,
                child_id=child_id,
                is_member=False,
                credit_balance=balance,
                status=estatus,
            ).model_dump(),
            "request_id": uuid.uuid4().hex,
        }
    except Exception as e:
        return {"ok": False, "code": "entitlement_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 7. POST /api/activation/redeem ────────────────────────
class ActivationRequest(BaseModel):
    code: str

# 激活码输错追踪（IP 维度，输错 5 次锁定 1 小时）
_activation_attempts: dict[str, dict] = {}

@app.post("/api/activation/redeem")
@limiter.limit("5/minute")
async def redeem_activation(body: ActivationRequest, request: Request, user: tuple = Depends(get_current_user)):
    try:
        parent_id, _ = user
        ip = request.client.host if request.client else "unknown"
        # 检查锁定
        now_ts = time.time()
        attempt = _activation_attempts.get(ip)
        if attempt and attempt.get("locked_until", 0) > now_ts:
            remaining = int(attempt["locked_until"] - now_ts)
            return JSONResponse(
                status_code=429,
                content={"ok": False, "code": "activation_locked", "message": f"尝试次数过多，请 {remaining} 秒后再试", "request_id": uuid.uuid4().hex},
            )
        await asyncio.sleep(0.3)
        # 真实核销：查询数据库
        result = _db.redeem_activation_code(body.code, parent_id)
        if result:
            # 写学豆流水
            _db.add_credit_ledger_entry(
                parent_id, "",
                result["credit_amount"],
                "activation_reward",
                f"激活码核销: {body.code}",
                result["code"],
            )
            _activation_attempts.pop(ip, None)
            return {"ok": True, "data": {"activated": True, "message": f"激活成功，获得 {result['credit_amount']} 学豆", "credit_added": result["credit_amount"]}, "request_id": uuid.uuid4().hex}
        # 输错 — 累加计数
        count = attempt["count"] + 1 if attempt else 1
        locked_until = now_ts + 3600 if count >= 5 else 0
        _activation_attempts[ip] = {"count": count, "locked_until": locked_until}
        if locked_until:
            return JSONResponse(
                status_code=429,
                content={"ok": False, "code": "activation_locked", "message": "尝试次数过多，请 1 小时后再试", "request_id": uuid.uuid4().hex},
            )
        return {"ok": False, "code": "invalid_code", "message": f"激活码无效（剩余 {5 - count} 次尝试）", "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "activation_error", "message": str(e), "request_id": uuid.uuid4().hex}


# ─── 10. POST /api/payment/create-order (Phase 0 占位) ─────
class CreateOrderRequest(BaseModel):
    plan_code: str
    amount: int

@app.post("/api/payment/create-order")
@limiter.limit("3/minute")
async def create_payment_order(body: CreateOrderRequest, request: Request = None):
    """支付下单占位，Phase 0 返回 Mock"""
    try:
        order_id = f"order-{uuid.uuid4().hex[:12]}"
        return {"ok": True, "data": {"order_id": order_id, "plan_code": body.plan_code, "amount": body.amount, "status": "pending", "payment_url": "https://mock.pay.example.com"}, "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "payment_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 11. POST /api/payment/callback (Phase 2 实现) ─────────
@app.post("/api/payment/callback")
@limiter.limit("30/minute")
async def payment_callback(request: Request):
    """支付回调占位，Phase 2 实现验签与幂等"""
    return {"ok": True, "message": "callback received (placeholder)", "request_id": uuid.uuid4().hex}

# ─── 12. GET /api/billing/credit-ledger (Phase 0 占位) ─────
@app.get("/api/billing/credit-ledger")
@limiter.limit("20/minute")
async def get_credit_ledger(request: Request, user: tuple = Depends(get_current_user)):
    """学豆流水查询 — 从 DB 真实读取"""
    try:
        parent_id, _ = user
        c = _db._conn()
        rows = c.execute(
            "SELECT * FROM credit_ledger WHERE parent_user_id = ? ORDER BY created_at DESC LIMIT 100",
            (parent_id,),
        ).fetchall()
        c.close()
        entries = [dict(r) for r in rows]
        total_change = sum(e["change_amount"] for e in entries)
        return {
            "ok": True,
            "data": {"entries": entries, "total": total_change},
            "request_id": uuid.uuid4().hex,
        }
    except Exception as e:
        return {"ok": False, "code": "ledger_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 13. GET /api/mistakes ──────────────────────────────────
@app.get("/api/mistakes")
@limiter.limit("20/minute")
async def get_mistakes(request: Request, user: tuple = Depends(get_current_user)):
    """获取当前孩子的错题列表"""
    try:
        _, child_id = user
        mistakes = _db.get_mistakes(child_id)
        return {"ok": True, "data": mistakes, "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "mistakes_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 13b. DELETE /api/mistakes/{mistake_id} ──────────────────
@app.delete("/api/mistakes/{mistake_id}")
@limiter.limit("10/minute")
async def delete_mistake_item(mistake_id: str, request: Request, user: tuple = Depends(get_current_user)):
    """软删除错题（联级删除辅导对话）"""
    try:
        _db.delete_mistake(mistake_id)
        return {"ok": True, "data": {"id": mistake_id}, "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "mistakes_delete_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 13c. PATCH /api/mistakes/{mistake_id} ──────────────────
class UpdateMistakeRequest(BaseModel):
    mastery_status: str | None = None
    error_type_code: str | None = None
    reason_desc: str | None = None

@app.patch("/api/mistakes/{mistake_id}")
@limiter.limit("10/minute")
async def update_mistake_item(mistake_id: str, body: UpdateMistakeRequest, request: Request, user: tuple = Depends(get_current_user)):
    """更新错题状态/类型"""
    try:
        _db.update_mistake(mistake_id, body.mastery_status, body.error_type_code, body.reason_desc)
        return {"ok": True, "data": {"id": mistake_id}, "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "mistakes_update_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 14. POST /api/auth/switch-child ────────────────────────
class SwitchChildRequest(BaseModel):
    child_id: str

@app.post("/api/auth/switch-child")
@limiter.limit("10/minute")
async def switch_child(body: SwitchChildRequest, request: Request, user: tuple = Depends(get_current_user)):
    """切换活跃孩子，返回新 JWT"""
    try:
        parent_id, _ = user
        parent = next((p for p in _parents.values() if p.id == parent_id), None)
        if not parent:
            return {"ok": False, "code": "not_found", "message": "用户不存在", "request_id": uuid.uuid4().hex}
        token = create_token({"parent_id": parent_id, "child_id": body.child_id, "phone": parent.phone})
        return {"ok": True, "data": {"token": token, "active_child_id": body.child_id}, "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "switch_error", "message": str(e), "request_id": uuid.uuid4().hex}

# 启动: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# ─── 8. POST /api/homework/parse ──────────────────────────
from pydantic import BaseModel as PydanticBase

class HomeworkParseRequest(PydanticBase):
    text: str

class HomeworkSubjectModel(PydanticBase):
    name: str
    tasks: list[str]

class HomeworkParseResponse(PydanticBase):
    subjects: list[HomeworkSubjectModel]
    raw_text: str

def mock_parse_homework(text: str) -> list[HomeworkSubjectModel]:
    """Mock 解析微信群作业文本，支持多种常见格式"""
    import re
    subjects: list[HomeworkSubjectModel] = []
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    current_subject: str | None = None
    in_homework = False

    for line in lines:
        # 跳过非作业内容（Summary/课堂内容等）
        if re.match(r"^(Summary|课堂内容|本节课)[：:]", line, re.IGNORECASE):
            in_homework = False
            continue

        # 检测作业区段开始
        if re.match(r"^(Homework|作业|任务)[：:]", line, re.IGNORECASE):
            in_homework = True
            continue

        # 格式A-C: 科目：任务1，任务2
        m = re.match(r"^【([一-鿿]{2,4})】\s*(.+)$", line)
        if not m:
            m = re.match(r"^(?:【)?([\u4e00-\u9fff]{2,4})(?:】)?\s*[：:\-]\s*(.+)$", line)
        if not m:
            m = re.match(r"^([\u4e00-\u9fff]{2,4})\s*[：:]\s*(.+)$", line)
        if not m:
            m = re.match(r"^([\u4e00-\u9fff]{2,4})\s*-\s*(.+)$", line)

        if m:
            name = m.group(1)
            raw_tasks = m.group(2)
            current_subject = name
            in_homework = True
            tasks = [t.strip() for t in re.split(r"[,，、/\-]\s*", raw_tasks) if t.strip()]
            if not tasks:
                tasks = [raw_tasks.strip()]
            existing = next((s for s in subjects if s.name == name), None)
            if existing:
                existing.tasks.extend(tasks)
            else:
                subjects.append(HomeworkSubjectModel(name=name, tasks=tasks))
            continue

        # 格式D: 🌸1. 任务 / ①任务 / 1. 任务 (编号列表，跟在Homework区段后)
        if in_homework:
            # 去掉行首的 emoji/符号编号：🌸1. ① 1. 1、 (1)
            task_text = re.sub(
                r"^[\U0001F300-\U0001F9FF\u2600-\u27BF\u2B50\u2764\u2728]*\s*\d+\s*[.、．)\s]+",
                "", line
            ).strip()
            # 也去掉纯数字编号 "1." "1、" 等
            task_text = re.sub(r"^\d+\s*[.、．)]\s*", "", task_text).strip()
            if task_text and len(task_text) > 2:
                name = current_subject or "作业"
                existing = next((s for s in subjects if s.name == name), None)
                if existing:
                    existing.tasks.append(task_text)
                else:
                    subjects.append(HomeworkSubjectModel(name=name, tasks=[task_text]))
            continue

        # 格式E: 无编号纯任务行（跟在科目后）
        if current_subject and len(line) > 3:
            existing = next((s for s in subjects if s.name == current_subject), None)
            if existing:
                existing.tasks.append(line)
            else:
                subjects.append(HomeworkSubjectModel(name=current_subject, tasks=[line]))

    return subjects

@app.post("/api/homework/parse")
@limiter.limit("5/minute")
async def parse_homework(body: HomeworkParseRequest, request: Request = None):
    try:
        # ── DeepSeek v4-flash 优先解析 ──
        ds = DeepSeekClient()
        if ds._available():
            ds_result = ds.parse_homework_text(body.text)
            # ── 记 model_call log（成本可追溯）──
            usage = ds_result.get("usage", {}) if ds_result.get("success") else {}
            pricing = get_active_pricing("deepseek", "deepseek-v4-flash")
            _hwlog = make_log_entry(
                task_id=uuid.uuid4().hex[:12],
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                feature_code="deepseek_homework_parse",
                latency_ms=ds_result.get("latency_ms", 0),
                success=ds_result["success"],
                input_tokens=usage.get("prompt_tokens", 0) if isinstance(usage, dict) else 0,
                output_tokens=usage.get("completion_tokens", 0) if isinstance(usage, dict) else 0,
                cache_hit_tokens=usage.get("prompt_cache_hit_tokens", 0) if isinstance(usage, dict) else 0,
                cache_miss_tokens=usage.get("prompt_cache_miss_tokens", 0) if isinstance(usage, dict) else 0,
                billing_status="free_tier" if ds_result["success"] else "failed",
                pricing=pricing,
                subjects_count=len(ds_result.get("subjects", [])),
            )
            _model_calls.append(_hwlog)
            _db.save_model_call(_hwlog)
            if ds_result["success"] and ds_result["subjects"]:
                subjects = [HomeworkSubjectModel(name=s["name"], tasks=s["tasks"]) for s in ds_result["subjects"]]
                info(f"[HW] DeepSeek parsed {len(subjects)} subjects in {ds_result['latency_ms']}ms")
                return {
                    "ok": True,
                    "data": HomeworkParseResponse(subjects=subjects, raw_text=body.text).model_dump(),
                    "request_id": uuid.uuid4().hex,
                }
            error(f"[HW] DeepSeek failed: {ds_result.get('error')}, falling back to mock")

        # ── Mock 回落 ──
        await asyncio.sleep(0.2)
        subjects = mock_parse_homework(body.text)
        if not subjects:
            return {
                "ok": False,
                "code": "parse_empty",
                "message": "未识别到作业格式，请参考示例重新粘贴",
                "request_id": uuid.uuid4().hex,
            }
        return {
            "ok": True,
            "data": HomeworkParseResponse(
                subjects=subjects,
                raw_text=body.text,
            ).model_dump(),
            "request_id": uuid.uuid4().hex,
        }
    except Exception as e:
        return {
            "ok": False,
            "code": "parse_error",
            "message": str(e),
            "request_id": uuid.uuid4().hex,
        }


# ─── 8.1 GET /api/homework/days ──────────────────────────

class HomeworkDayItem(PydanticBase):
    date: str
    data_json: str
    updated_at: str


@app.get("/api/homework/days")
@limiter.limit("30/minute")
async def get_homework_days(request: Request, user: tuple = Depends(get_current_user)):
    """返回当前 child 的作业清单历史（最近 14 天）。"""
    try:
        parent_id, child_id = user
        rows = _db.get_homework_days(child_id)
        return {
            "ok": True,
            "data": [HomeworkDayItem(**r).model_dump() for r in rows],
            "request_id": uuid.uuid4().hex,
        }
    except HTTPException:
        raise
    except Exception as e:
        return {
            "ok": False,
            "code": "homework_days_error",
            "message": str(e),
            "request_id": uuid.uuid4().hex,
        }


# ─── 8.2 POST /api/homework/days ─────────────────────────

class HomeworkDaySaveRequest(PydanticBase):
    date: str
    entries_json: str  # JSON string of HomeworkDayEntry[]


@app.post("/api/homework/days")
@limiter.limit("30/minute")
async def save_homework_days(request: Request, body: HomeworkDaySaveRequest, user: tuple = Depends(get_current_user)):
    """保存某天的作业清单到后端（清缓存后可从 GET 恢复）。"""
    try:
        parent_id, child_id = user
        _db.save_homework_day(child_id, body.date, body.entries_json)
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
            "code": "homework_save_error",
            "message": str(e),
            "request_id": uuid.uuid4().hex,
        }


# ─── 9. 识图切题历史后端持久化 ─────────────────────────

class ParseHistoryItem(PydanticBase):
    job_id: str
    data_json: str
    updated_at: str


class ParseHistorySaveRequest(PydanticBase):
    job_id: str
    data_json: str  # JSON string of JobHistoryEntry


@app.get("/api/parse-history")
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


@app.post("/api/parse-history")
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
