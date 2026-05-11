"""
悠米伴学 API — FastAPI 后端骨架
Phase 0 Mock 模式，所有接口返回假数据
"""

import asyncio
import uuid
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
from vision_client import QwenVLClient
from deepseek_client import DeepSeekClient
from tutor_prompt import build_tutor_messages
import oss_client as _oss
from auth import (
    create_token, verify_token, hash_password, verify_password,
    init_seed_users, get_parent_by_phone, get_children,
    ParentUser, ChildProfile, _parents, _children,
)
import db as _db

# 确保 root Python 能找到 hermes_me 安装的包（systemd 以 root 运行）
import sys
_site = "/home/hermes_me/.local/lib/python3.10/site-packages"
if _site not in sys.path:
    sys.path.insert(0, _site)

from slowapi import Limiter
from slowapi.util import get_remote_address

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
    allow_origins=["http://localhost:3000", "http://39.107.119.136:3000", "https://youmi.xyz"],
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

def get_current_user(authorization: Annotated[str | None, Header()] = None):
    """FastAPI 依赖：验证 JWT，返回 (parent_id, child_id)"""
    if not authorization:
        raise HTTPException(status_code=401, detail={"ok": False, "code": "unauthorized", "message": "请先登录"})
    payload = verify_token(authorization)
    if not payload:
        raise HTTPException(status_code=401, detail={"ok": False, "code": "token_expired", "message": "登录已过期，请重新登录"})
    return payload.get("parent_id", ""), payload.get("child_id", "")

# ─── 枚举 ──────────────────────────────────────────────────
class EntitlementStatus(str, Enum):
    free_trial = "free_trial"
    member_active = "member_active"
    member_expired = "member_expired"
    credit_enough = "credit_enough"
    credit_low = "credit_low"
    credit_empty = "credit_empty"
    activation_mock_only = "activation_mock_only"

class JobStatus(str, Enum):
    uploaded = "uploaded"
    enhancing = "enhancing"
    ocr_running = "ocr_running"
    cutting = "cutting"
    vision_reviewing = "vision_reviewing"
    schema_validating = "schema_validating"
    completed = "completed"
    needs_review = "needs_review"
    failed = "failed"

class QuestionStatus(str, Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"

# ─── 泛型响应 ──────────────────────────────────────────────
T = TypeVar("T")

class ApiResponse(BaseModel, Generic[T]):
    ok: bool = Field(True)
    data: Optional[T] = Field(None)
    code: Optional[str] = Field(None)
    message: Optional[str] = Field(None)
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)

# ─── 业务模型 ──────────────────────────────────────────────
class ParseJob(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.uploaded
    questions_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    file_name: Optional[str] = None

class Question(BaseModel):
    question_id: str
    question_number: int
    question_text: str
    bbox: Optional[List[float]] = None
    crop_url: Optional[str] = None
    visual_description: Optional[str] = None
    status: QuestionStatus = QuestionStatus.pending

class TutorRequest(BaseModel):
    mode: str  # initial | followup
    message: str
    client_message_id: Optional[str] = None
    use_vision: Optional[bool] = False

class TutorResponse(BaseModel):
    reply_text: str
    chat_limit_reached: bool = False
    remaining_rounds: int = 0
    request_id: Optional[str] = None

class Entitlement(BaseModel):
    user_id: str
    child_id: Optional[str] = None
    is_member: bool = False
    member_until: Optional[str] = None
    credit_balance: int = 0
    status: EntitlementStatus = EntitlementStatus.free_trial

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

# ─── Mock 数据 ─────────────────────────────────────────────
MOCK_ENTITLEMENT = Entitlement(
    user_id="p001", child_id="c001",
    is_member=False, credit_balance=50,
    status=EntitlementStatus.free_trial,
)

MOCK_QUESTIONS = [
    Question(question_id=f"q-{i:03d}", question_number=i,
                question_text=f"第{i}题：请计算 {i}×{i+1} 的结果。",
                bbox=[100, 50+60*i, 400, 48],
                status=QuestionStatus.completed)
    for i in range(1, 4)
]

def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()

# job 状态存储（SQLite 持久化 + 内存缓存）
_jobs, _model_calls, _tutor_chats, _credit_balances = _db.load_all()
# 兼容旧内存模式：load_all 返回的类型
_jobs: dict[str, dict]
_model_calls: list[dict]
_tutor_chats: dict[str, list[dict]]  # question_id -> chat history
_credit_balances: dict[str, int]  # child_id -> remaining credits (Phase 0 Mock, 后端独立计数)
# ─── 管线边界函数（基准 Table 22 R1）────────────────────────
def enqueue_parse_job(jid: str, job_entry: dict):
    """将任务注册到内存队列 + SQLite 持久化。"""
    _jobs[jid] = job_entry
    _db.save_job(jid, job_entry)



def save_result(jid: str, questions: list, now: str, file, status: JobStatus):
    """保存任务最终结果 — SQLite 持久化"""
    _jobs[jid]["job"] = ParseJob(
        job_id=jid, status=status,
        questions_count=len(questions),
        created_at=now, updated_at=_ts(), file_name=file.filename if file else "",
    )
    _jobs[jid]["questions"] = questions
    # 持久化：把 Pydantic 对象序列化
    _db.save_job(jid, {
        "job_id": jid,
        "status": status.value,
        "questions_count": len(questions),
        "created_at": now,
        "updated_at": _ts(),
        "file_name": file.filename if file else "",
        "questions": [q.model_dump() for q in questions],
        "poll_count": _jobs[jid].get("poll_count", 0),
        "child_id": _jobs[jid].get("child_id", ""),
        "parent_id": _jobs[jid].get("parent_id", ""),
        "client_task_id": _jobs[jid].get("client_task_id", ""),
    })
    # 同步更新 parse_jobs 表（跨重启查询用）
    child_id = _jobs[jid].get("child_id", "")
    parent_id = _jobs[jid].get("parent_id", "")
    file_name = file.filename if file else ""
    _db.save_parse_job(jid, child_id, parent_id, file_name, len(questions), status, now)


# ─── 鉴权端点 ──────────────────────────────────────────────

class LoginRequest(BaseModel):
    phone: str
    password: str

class RegisterRequest(BaseModel):
    phone: str
    password: str
    name: str = ""


@app.post("/api/auth/login")
async def login(body: LoginRequest):
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
async def register(body: RegisterRequest):
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
async def list_children(user: tuple = Depends(get_current_user)):
    """获取当前家长的所有孩子"""
    parent_id, _ = user
    children = get_children(parent_id)
    return {"ok": True, "data": [{"id": c.id, "name": c.name, "avatar": c.avatar} for c in children]}


# ─── 健康检查 ──────────────────────────────────────────────
@app.get("/health")
def health_check():
    return {"ok": True, "message": "悠米伴学 API 运行中"}

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
    try:
        parent_id, child_id = user

        # ─── #8: client_task_id 幂等 ───
        if client_task_id:
            for jid_existing, j_existing in _jobs.items():
                if j_existing.get("client_task_id") == client_task_id:
                    return {"ok": True, "data": {"job_id": jid_existing, "status": j_existing["job"].status, "file_name": file.filename}, "request_id": uuid.uuid4().hex}

        jid = uuid.uuid4().hex[:12]
        now = _ts()

        # Read file contents NOW (before BackgroundTasks runs, or handle closes)
        contents = await file.read()

        # 注册任务到内存队列
        enqueue_parse_job(jid, {
            "job": ParseJob(job_id=jid, status=JobStatus.uploaded,
                            questions_count=0, created_at=now, file_name=file.filename),
            "questions": [], "poll_count": 0,
            "child_id": child_id,
            "parent_id": parent_id,
            "client_task_id": client_task_id,
        })
        # 持久化到 DB（跨重启存活）
        _db.save_parse_job(jid, child_id, parent_id, file.filename, 0, JobStatus.uploaded, now)
        # 启动后台 worker
        asyncio.create_task(worker_process_job(jid, contents, file, now, parent_id, child_id))
        return {"ok": True, "data": {"job_id": jid, "status": "uploaded", "file_name": file.filename}, "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "parse_failed", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 后台任务 worker（模块级，基准 Table 22 R1）────────────
async def worker_process_job(jid: str, contents: bytes, file, now: str, parent_id: str, child_id: str):
    """后台异步执行：OCR → 切题 → Qwen-VL → Schema 校验 → 保存"""
    print(f"[BG] Starting OCR for {jid}...", flush=True)
    t_start = time.time()

    # 持久化原始图片供 vision 二次路由使用
    import os as _osp
    _osp.makedirs("/tmp/yomi", exist_ok=True)
    with open(f"/tmp/yomi/{jid}.jpg", "wb") as _pf:
        _pf.write(contents)

    try:
        # 上传到 OSS（异步，不阻塞主流程）
        oss_key = _oss.upload_image(contents, jid)
    except Exception as _e:
        print(f"[BG] OSS upload error: {_e}", flush=True)
        oss_key = None

    # register_image 可能因 DB schema 问题失败，不阻塞主流程
    try:
        _db.register_image(jid, f"/tmp/yomi/{jid}.jpg", now, oss_key or "")
    except Exception as _e:
        print(f"[BG] register_image failed (non-blocking): {_e}", flush=True)

    if oss_key:
        _jobs[jid]["oss_key"] = oss_key
        print(f"[BG] OSS upload OK: {oss_key}", flush=True)
    else:
        print(f"[BG] OSS unavailable, using local only", flush=True)

    # ── 创建 assignment + page（基准 Table 12）──
    import uuid as _uuid
    aid = _uuid.uuid4().hex[:12]
    try:
        _db.create_assignment(aid, parent_id, child_id, "web_upload", file.filename)
    except Exception as _e:
        print(f"[BG] create_assignment failed: {_e}", flush=True)
    page_id = _uuid.uuid4().hex[:12]
    try:
        _db.create_assignment_page(page_id, aid, 1, oss_key or f"/tmp/yomi/{jid}.jpg")
    except Exception as _e:
        print(f"[BG] create_assignment_page failed: {_e}", flush=True)
    _jobs[jid]["assignment_id"] = aid
    _jobs[jid]["page_id"] = page_id

    try:
        # Stage: enhancing
        _jobs[jid]["job"].status = JobStatus.enhancing

        # Stage: ocr_running
        _jobs[jid]["job"].status = JobStatus.ocr_running
        ocr = AliyunOCRClient()
        result = ocr.recognize(contents)
        extracted = ocr.extract_text_and_blocks(result)
        latency_ms = int((time.time() - t_start) * 1000)

        _log = make_log_entry(
            task_id=jid,
            provider_name="aliyun_ocr",
            model_name="ocr_api20210707",
            feature_code="ocr",
            latency_ms=latency_ms,
            success=True,
            parent_user_id=parent_id,
            child_id=child_id,
            request_id=result.get("RequestId", ""),
            billing_status="free_tier",
            blocks_count=len(extracted["blocks"]),
        )
        _model_calls.append(_log)
        _db.save_model_call(_log)

        # Stage: cutting — 智能切题（题号规则 + 版面规则）
        _jobs[jid]["job"].status = JobStatus.cutting
        cut_results = cut_to_questions(extracted["blocks"])
        questions = []
        for i, cq in enumerate(cut_results):
            qid = f"{jid}-{cq['question_number']}-{i}"
            questions.append(Question(
                question_id=qid,
                question_number=cq["question_number"],
                question_text=cq["question_text"],
                bbox=cq["bbox"],
                visual_description=None,
                status=QuestionStatus.completed,
            ))
            # 写入 question_item 独立表（基准 Table 12）
            _db.create_question_item(qid, aid, page_id, cq["question_number"], cq["question_text"], cq["bbox"])
        print(f"[BG] Cut {len(extracted['blocks'])} blocks → {len(questions)} questions", flush=True)

        # Stage: vision_reviewing - Qwen-VL (Table 11 R3), 失败重试分流 (Table 14)
        _jobs[jid]["job"].status = JobStatus.vision_reviewing
        MAX_SCHEMA_RETRIES = 2     # Schema 校验失败
        MAX_NETWORK_RETRIES = 3    # 网络超时（指数退避）
        qwen_vl = QwenVLClient()
        for qi, q in enumerate(questions):
            schema_retries = 0
            network_retries = 0
            while True:
                try:
                    t_vs = time.time()
                    vl_result = qwen_vl.analyze_question(
                        image_bytes=contents,
                        bbox=q.bbox or [0, 0, 0, 0],
                        question_text=q.question_text,
                    )
                    vl_ms = int((time.time() - t_vs) * 1000)
                    q.visual_description = vl_result["visual_description"]
                    q.status = QuestionStatus.completed
                    # 同时更新 question_item 的 visual_description
                    _db.create_question_item(q.question_id, aid, page_id, q.question_number, q.question_text, q.bbox or [], q.visual_description or "")

                    _vlog = make_log_entry(
                        task_id=jid,
                        question_id=q.question_id,
                        provider_name="dashscope",
                        model_name="qwen-vl-plus",
                        feature_code="vision_cutting",
                        latency_ms=vl_ms,
                        success=vl_result["success"],
                        parent_user_id=parent_id,
                        child_id=child_id,
                        error_code=vl_result.get("error"),
                        billing_status="free_tier" if vl_result["success"] else "failed",
                        prompt_name="qwen_vl_analyze",
                        retry_count=schema_retries + network_retries,
                    )
                    _model_calls.append(_vlog)
                    _db.save_model_call(_vlog)
                    print(f"[BG] Vision #{qi}: {vl_ms}ms success={vl_result['success']} retries={schema_retries + network_retries}", flush=True)
                    break
                except Exception as ve:
                    err_str = str(ve).lower()
                    # 429 / rate_limit → 不立即重试，延后 5s
                    if "429" in err_str or "rate_limit" in err_str or "too many requests" in err_str:
                        print(f"[BG] Vision #{qi} RATE LIMITED, sleeping 5s: {ve}", flush=True)
                        await asyncio.sleep(5)
                        network_retries += 1
                    # 网络超时 → 指数退避，最多 3 次
                    elif "timeout" in err_str or "connection" in err_str or "timed out" in err_str:
                        network_retries += 1
                        if network_retries > MAX_NETWORK_RETRIES:
                            print(f"[BG] Vision #{qi} NETWORK FAILED after {MAX_NETWORK_RETRIES} retries: {ve}", flush=True)
                            q.status = QuestionStatus.failed
                            break
                        wait_s = 2 ** network_retries
                        print(f"[BG] Vision #{qi} network retry {network_retries}/{MAX_NETWORK_RETRIES}, waiting {wait_s}s: {ve}", flush=True)
                        await asyncio.sleep(wait_s)
                    # Schema 校验失败 → 最多 2 次
                    else:
                        schema_retries += 1
                        if schema_retries > MAX_SCHEMA_RETRIES:
                            print(f"[BG] Vision #{qi} SCHEMA FAILED after {MAX_SCHEMA_RETRIES} retries: {ve}", flush=True)
                            q.status = QuestionStatus.failed
                            break
                        print(f"[BG] Vision #{qi} schema retry {schema_retries}/{MAX_SCHEMA_RETRIES}: {ve}", flush=True)
                        await asyncio.sleep(1)

        # Stage: schema_validating — 基准 Table 21
        _jobs[jid]["job"].status = JobStatus.schema_validating
        has_failures = False
        for q in questions:
            if q.status == QuestionStatus.failed:
                has_failures = True
                continue
            # Phase 0 轻量校验：题目文本 + 视觉描述完整性
            if not q.question_text or len(q.question_text.strip()) < 2:
                q.status = QuestionStatus.failed
                has_failures = True
            elif q.visual_description is None:
                q.status = QuestionStatus.failed
                has_failures = True

        final_status = JobStatus.needs_review if has_failures else JobStatus.completed
        save_result(jid, questions, now, file, final_status)

    except Exception as e:
        latency_ms = int((time.time() - t_start) * 1000)
        _flog = make_log_entry(
            task_id=jid,
            provider_name="aliyun_ocr",
            model_name="ocr_api20210707",
            feature_code="ocr",
            latency_ms=latency_ms,
            success=False,
            parent_user_id=parent_id,
            child_id=child_id,
            error_code=str(e)[:100],
            billing_status="failed",
        )
        _model_calls.append(_flog)
        _db.save_model_call(_flog)
        print(f"[BG] OCR FAILED: {e}", flush=True)
        _jobs[jid]["job"].status = JobStatus.failed


# ─── 2. GET /api/parse-jobs/recent (before {job_id} to avoid conflict) ──
@app.get("/api/parse-jobs/recent")
async def get_recent_parse_jobs(user: tuple = Depends(get_current_user)):
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
        # 优先内存（更新鲜）
        for jid, j in _jobs.items():
            if j.get("child_id") == child_id:
                job = j["job"]
                entry = add_job(jid, job.status, job.questions_count or len(j.get("questions", [])),
                                job.file_name, job.created_at)
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


# ─── 3. GET /api/parse-jobs/{job_id} ───────────────────────
@app.get("/api/parse-jobs/{job_id}")
async def get_parse_job_status(job_id: str):
    try:
        await asyncio.sleep(0.3)
        if job_id not in _jobs:
            raise HTTPException(status_code=404, detail={"ok": False, "code": "not_found", "message": "任务不存在", "request_id": uuid.uuid4().hex})
        j = _jobs[job_id]
        j["poll_count"] += 1
        # State driven by real OCR pipeline, not auto-advanced
        j["job"].updated_at = _ts()
        return {"ok": True, "data": j["job"].model_dump(), "request_id": uuid.uuid4().hex}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "code": "status_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 4. GET /api/parse-jobs/{job_id}/questions ─────────────
@app.get("/api/parse-jobs/{job_id}/questions")
async def get_parse_job_questions(job_id: str):
    try:
        await asyncio.sleep(0.3)
        if job_id not in _jobs:
            raise HTTPException(status_code=404, detail={"ok": False, "code": "not_found", "message": "任务不存在", "request_id": uuid.uuid4().hex})
        return {"ok": True, "data": [q.model_dump() for q in _jobs[job_id]["questions"]], "request_id": uuid.uuid4().hex}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "code": "questions_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 3.5. GET /api/questions/{question_id} ──────────────────
@app.get("/api/questions/{question_id}")
async def get_question_detail(question_id: str):
    """获取单题详情，从所有 job 中查找"""
    try:
        for jid, j in _jobs.items():
            for q in j.get("questions", []):
                if q.question_id == question_id:
                    return {"ok": True, "data": q.model_dump(), "request_id": uuid.uuid4().hex}
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
async def update_question_status(question_id: str, body: QuestionStatusRequest, user: tuple = Depends(get_current_user)):
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
        # Find question from all jobs
        q_found = None
        for j in _jobs.values():
            for q in j.get("questions", []):
                if q.question_id == question_id:
                    q_found = q
                    break

        if not q_found:
            raise HTTPException(status_code=404, detail={
                "ok": False, "code": "not_found",
                "message": "题目不存在", "request_id": uuid.uuid4().hex,
            })

        # Credit check — 基准 Table 24: 后端二次校验
        parent_id, child_id_for_credit = user
        credits = _credit_balances.setdefault(child_id_for_credit, 50)
        if credits <= 0:
            return {
                "ok": True,
                "data": TutorResponse(
                    reply_text="学豆不足，请联系家长充值",
                    chat_limit_reached=True,
                    remaining_rounds=0,
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
        messages = build_tutor_messages(
            mode=body.mode,
            question_text=q_found.question_text,
            visual_description=q_found.visual_description or "",
            chat_history=history if body.mode == "followup" else None,
            user_message=body.message,
        )
        result = ds.tutor(messages)
        latency_ms = int((time.time() - t_start) * 1000)

        # Save to chat history + deduct credit — SQLite 持久化
        history.append({"role": "user", "content": body.message or q_found.question_text})
        history.append({"role": "assistant", "content": result["reply_text"]})
        _tutor_chats[question_id] = history
        _credit_balances[child_id_for_credit] = credits - 1
        _db.save_tutor_chat(question_id, history)
        _db.save_credit_balance(child_id_for_credit, credits - 1)

        # 写 credit_ledger 流水（基准 Table 12）
        _db.add_credit_ledger_entry(parent_id, child_id_for_credit, -1, "tutor_call", f"辅导: {question_id}", question_id)

        # 写入结构化 ai_tutoring_chat（基准 Table 12，按 sequence_number）
        seq = len(history)
        call_id = uuid.uuid4().hex[:12]
        _db.save_tutor_message(uuid.uuid4().hex[:12], question_id, child_id_for_credit, seq - 1, "user", body.message or q_found.question_text)
        _db.save_tutor_message(call_id, question_id, child_id_for_credit, seq, "assistant", result["reply_text"], call_id)

        # Log model call — SQLite 持久化
        _tlog = make_log_entry(
            task_id="tutor",
            question_id=question_id,
            provider_name="deepseek",
            model_name="deepseek-chat",
            feature_code="tutor",
            latency_ms=result.get("latency_ms", latency_ms),
            success=result["success"],
            error_code=result.get("error"),
            billing_status="free_tier" if result["success"] else "failed",
            prompt_name=f"tutor_{body.mode}",
            input_tokens=(result.get("usage") or {}).get("input_tokens", 0),
            output_tokens=(result.get("usage") or {}).get("output_tokens", 0),
            estimated_cost=0.0,
        )
        _model_calls.append(_tlog)
        _db.save_model_call(_tlog)

        remaining = MAX_ROUNDS - len(history) // 2
        return {
            "ok": True,
            "data": TutorResponse(
                reply_text=result["reply_text"],
                chat_limit_reached=False,
                remaining_rounds=remaining,
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
async def vision_retry(question_id: str, request: Request = None):
    """视觉二次路由：对已有题目的裁切图做 Qwen-VL 重读"""
    try:
        # Find the question from all jobs
        q_found = None
        q_bbox = None
        q_text = ""
        for jid, j in _jobs.items():
            for q in j.get("questions", []):
                if q.question_id == question_id:
                    q_found = q
                    q_bbox = q.bbox
                    q_text = q.question_text
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
                    print(f"[Vision] loaded from OSS: {oss_key}", flush=True)
            except Exception:
                print(f"[Vision] OSS load failed, try local", flush=True)

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
        _vlog = make_log_entry(
            task_id="vision_retry",
            question_id=question_id,
            provider_name="dashscope",
            model_name="qwen-vl-plus",
            feature_code="vision_retry",
            latency_ms=vl_ms,
            success=vl_result["success"],
            error_code=vl_result.get("error"),
            billing_status="free_tier" if vl_result["success"] else "failed",
            prompt_name="qwen_vl_vision_retry",
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
async def get_entitlement(user: tuple = Depends(get_current_user)):
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
async def redeem_activation(body: ActivationRequest, request: Request):
    try:
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
        result = _db.redeem_activation_code(body.code, "demo_parent_001")  # TODO: 从 JWT 取 parent_id
        if result:
            # 写学豆流水
            _db.add_credit_ledger_entry(
                "demo_parent_001", "",
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
async def create_payment_order(body: CreateOrderRequest):
    """支付下单占位，Phase 0 返回 Mock"""
    try:
        order_id = f"order-{uuid.uuid4().hex[:12]}"
        return {"ok": True, "data": {"order_id": order_id, "plan_code": body.plan_code, "amount": body.amount, "status": "pending", "payment_url": "https://mock.pay.example.com"}, "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "payment_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 11. POST /api/payment/callback (Phase 2 实现) ─────────
@app.post("/api/payment/callback")
async def payment_callback():
    """支付回调占位，Phase 2 实现验签与幂等"""
    return {"ok": True, "message": "callback received (placeholder)", "request_id": uuid.uuid4().hex}

# ─── 12. GET /api/billing/credit-ledger (Phase 0 占位) ─────
@app.get("/api/billing/credit-ledger")
async def get_credit_ledger():
    """学豆流水查询占位"""
    return {"ok": True, "data": {"entries": [], "total": 0}, "request_id": uuid.uuid4().hex}

# ─── 13. GET /api/mistakes ──────────────────────────────────
@app.get("/api/mistakes")
async def get_mistakes(user: tuple = Depends(get_current_user)):
    """获取当前孩子的错题列表"""
    try:
        _, child_id = user
        mistakes = _db.get_mistakes(child_id)
        return {"ok": True, "data": mistakes, "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "mistakes_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 14. POST /api/auth/switch-child ────────────────────────
class SwitchChildRequest(BaseModel):
    child_id: str

@app.post("/api/auth/switch-child")
async def switch_child(body: SwitchChildRequest, user: tuple = Depends(get_current_user)):
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
async def parse_homework(body: HomeworkParseRequest):
    try:
        await asyncio.sleep(0.3)
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
