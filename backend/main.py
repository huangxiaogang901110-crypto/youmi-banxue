"""
悠米伴学 API — FastAPI 后端骨架
Phase 0 Mock 模式，所有接口返回假数据
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
from logger import info as _log_info, warning as _log_warn, error as _log_error

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
    created = "created"          # 任务已创建，等待上传图片
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
    student_answer: Optional[str] = None
    is_correct: Optional[bool] = None
    grading_explanation: Optional[str] = None
    section_title: Optional[str] = None
    section_index: Optional[int] = None
    sub_index: Optional[int] = None

class TutorRequest(BaseModel):
    mode: str  # initial | followup
    message: str
    client_message_id: Optional[str] = None
    use_vision: Optional[bool] = False
    action: Optional[str] = None  # hint | step | solve

class TutorResponse(BaseModel):
    reply_text: str
    chat_limit_reached: bool = False
    remaining_rounds: int = 0
    credit_balance: int = -1  # -1 表示未返回（兼容旧版）
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
_credit_balances: dict[str, int]  # parent_user_id -> remaining credits (Phase 1 统一按家长计费)

# 429 延后队列（基准 §9/17 — 独立队列，非同循环 sleep）
_deferred_vision_tasks: list = []
# ─── 管线边界函数（基准 Table 22 R1）────────────────────────
def enqueue_parse_job(jid: str, job_entry: dict):
    """将任务注册到内存队列 + SQLite 持久化。"""
    _jobs[jid] = job_entry
    _db.save_job(jid, job_entry)



def save_result(jid: str, questions: list, now: str, file, status: JobStatus):
    """保存任务最终结果 — SQLite 持久化。先写 questions 再标 completed，防 poll 读到 qcount=0。"""
    # ⚠️ 顺序：先写 questions 再设 status，防止 completed 和 questions 之间的竞态窗口
    _jobs[jid]["questions"] = questions
    _jobs[jid]["job"] = ParseJob(
        job_id=jid, status=status,
        questions_count=len(questions),
        created_at=now, updated_at=_ts(), file_name=file.filename if file else "",
    )
    # 确保 poll_count 存在（旧 save_result 调用后可能丢失）
    if "poll_count" not in _jobs[jid]:
        _jobs[jid]["poll_count"] = 0
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
        "progress": _jobs[jid].get("progress", ""),
        "error_code": _jobs[jid].get("error_code", ""),
        "retry_count": _jobs[jid].get("retry_count", 0),
        "completed_at": _ts() if status.value in ("completed", "completed_with_failures") else "",
    })
    # 同步更新 parse_jobs 表（跨重启查询用）
    child_id = _jobs[jid].get("child_id", "")
    parent_id = _jobs[jid].get("parent_id", "")
    file_name = file.filename if file else ""
    progress = _jobs[jid].get("progress", "")
    error_code = _jobs[jid].get("error_code", "")
    retry_count = _jobs[jid].get("retry_count", 0)
    completed_at = _ts() if status.value in ("completed", "completed_with_failures", "failed") else ""
    parse_mode = _jobs[jid].get("parse_mode", "")
    parser_provider = _jobs[jid].get("parser_provider", "")
    parser_model = _jobs[jid].get("parser_model", "")
    qwen_parse_call_id = _jobs[jid].get("qwen_parse_call_id", "")
    total_parse_cost_cny = _jobs[jid].get("total_parse_cost_cny", 0.0)
    _db.save_parse_job(jid, child_id, parent_id, file_name, len(questions), status, now,
                        progress=progress, error_code=error_code, retry_count=retry_count, completed_at=completed_at,
                        parse_mode=parse_mode, parser_provider=parser_provider, parser_model=parser_model,
                        qwen_parse_call_id=qwen_parse_call_id, total_parse_cost_cny=total_parse_cost_cny,
                        data_json=json.dumps({
                            "job_id": jid, "status": status.value, "questions_count": len(questions),
                            "created_at": now, "updated_at": _ts(), "file_name": file.filename if file else "",
                            "questions": [q.model_dump() for q in questions],
                            "poll_count": _jobs[jid].get("poll_count", 0),
                            "child_id": child_id, "parent_id": parent_id,
                            "client_task_id": _jobs[jid].get("client_task_id", ""),
                            "progress": progress, "error_code": error_code,
                            "retry_count": retry_count, "completed_at": completed_at,
                        }, default=str),
                        client_upload_id=_jobs[jid].get("client_upload_id", _jobs[jid].get("client_task_id", "")))


async def grade_answers(jid: str, questions: list, trace_id: str, parent_id: str, child_id: str) -> float:
    """DeepSeek 批量判对错。返回 grading 总成本 CNY。
    失败时所有题 is_correct=null, grading_explanation='暂未判定'。
    """
    # 只对有 student_answer 的题判题
    gradable = [(i, q) for i, q in enumerate(questions)
                 if hasattr(q, "student_answer") and q.student_answer or
                 isinstance(q, dict) and q.get("student_answer")]
    if not gradable:
        return 0.0

    # 构建批量判题 prompt
    items = []
    for idx, q in gradable:
        _qt = q.question_text if hasattr(q, "question_text") else q.get("question_text", "")
        _sa = q.student_answer if hasattr(q, "student_answer") else q.get("student_answer", "")
        _typ = q.get("type", "") if isinstance(q, dict) else getattr(q, "_type", "")
        items.append({
            "index": idx,
            "number": q.question_number if hasattr(q, "question_number") else q.get("question_number", 0),
            "type": _typ,
            "content": _qt[:200],
            "student_answer": str(_sa)[:200],
        })

    prompt = (
        "你是小学/初中作业批改老师。下面是一个作业的题目和孩子写的答案，请逐题判对错。\n"
        "输出 JSON 数组格式：[{\"index\":序号,\"number\":题号,\"is_correct\":true|false|null,\"grading_explanation\":\"简短解释\"}]\n"
        "如果无法确定对错（答案模糊/不完整），is_correct 填 null。解释要短，面向家长和孩子。\n"
        f"题目列表：\n{json.dumps(items, ensure_ascii=False, default=str)}\n"
        "只输出 JSON 数组，不要其他文字。"
    )

    ds = DeepSeekClient()
    grading_cost = 0.0
    try:
        _log_info(f"grading_start jid={jid} count={len(gradable)}", trace_id=trace_id, parent_id=parent_id, child_id=child_id)
        t_start = time.time()
        result = ds.tutor([
            {"role": "system", "content": "你是作业批改老师，输出纯 JSON 数组。"},
            {"role": "user", "content": prompt},
        ], max_tokens=1024)
        latency_ms = int((time.time() - t_start) * 1000)

        # 记录模型调用
        pricing = get_active_pricing("deepseek", "deepseek-chat")
        usage = result.get("usage", {}) or {}
        glog = make_log_entry(
            task_id=jid, provider_name="deepseek", model_name="deepseek-chat",
            feature_code="deepseek_grade_answers", trace_id=trace_id,
            sub_stage="grading", latency_ms=latency_ms,
            success=result["success"],
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_hit_tokens=usage.get("cache_hit_tokens", 0),
            cache_miss_tokens=usage.get("cache_miss_tokens", 0),
            parent_user_id=parent_id, child_id=child_id,
            error_code=result.get("error"),
            billing_status="free_tier" if result["success"] else "failed",
            pricing=pricing, question_count=len(gradable),
        )
        grading_cost = glog.get("cost_cny", 0.0)
        _model_calls.append(glog)
        _db.save_model_call(glog)

        if not result["success"]:
            print(f"[BG] Grading failed for {jid}: {result.get('error', 'unknown')}", flush=True)
            return grading_cost

        # 解析 DeepSeek 返回的 JSON
        content = result["reply_text"]
        import re as _re
        json_match = _re.search(r'\[.*\]', content, _re.DOTALL)
        grades = json.loads(json_match.group()) if json_match else []
        if not isinstance(grades, list):
            grades = []

        # 回填 by index
        grade_map = {}
        for g in grades:
            if isinstance(g, dict) and "index" in g:
                grade_map[g["index"]] = g

        for idx, q in enumerate(questions):
            g = grade_map.get(idx)
            if g:
                _is_correct = g.get("is_correct")
                if _is_correct not in (True, False):
                    _is_correct = None
                _expl = g.get("grading_explanation", "暂未判定") if g.get("grading_explanation") else "暂未判定"
            else:
                # 无 student_answer 或 grading 未覆盖
                _sa2 = q.student_answer if hasattr(q, "student_answer") else q.get("student_answer") if isinstance(q, dict) else None
                if _sa2:
                    _is_correct = None
                    _expl = "暂未判定"
                else:
                    _is_correct = None
                    _expl = ""
            if hasattr(q, "is_correct"):
                q.is_correct = _is_correct
                q.grading_explanation = _expl
            else:
                q["is_correct"] = _is_correct
                q["grading_explanation"] = _expl

        print(f"[BG] Grading OK for {jid}: {len(grades)}/{len(gradable)} graded", flush=True)
        # 诊断：判对错统计
        _correct = sum(1 for g in grade_map.values() if g.get("is_correct") is True)
        _wrong = sum(1 for g in grade_map.values() if g.get("is_correct") is False)
        _sa_count = len(gradable)
        print(f"[diag] grading_completed jid={jid} total={len(questions)} graded={len(grades)} correct={_correct} wrong={_wrong} has_child_answer={_sa_count}", flush=True)
    except Exception as e:
        print(f"[BG] Grading exception for {jid}: {e}", flush=True)
        _log_error(f"grading_failed jid={jid}: {e}", trace_id=trace_id)
    return grading_cost


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
async def list_children(user: tuple = Depends(get_current_user)):
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

# ─── 后台任务 worker（模块级，基准 Table 22 R1）────────────
async def worker_process_job(jid: str, contents: bytes, file, now: str, parent_id: str, child_id: str):
    """后台异步执行：Qwen-VL 全图识题优先 → OCR+切题回落 → Schema 校验 → 保存"""
    import uuid as _uuid
    trace_id = _uuid.uuid4().hex
    _log_info(f"job_start jid={jid}", trace_id=trace_id, parent_id=parent_id, child_id=child_id)
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

    _jobs[jid]["trace_id"] = trace_id
    total_parse_cost = 0.0  # 累计 Qwen-VL 解析成本

    try:
        # ── Phase 1: Qwen-VL 全图识题优先 ──
        _jobs[jid]["job"].status = JobStatus.enhancing
        qwen_vl = QwenVLClient()
        use_qwen_vl = False
        qwen_parse_call_id = ""  # 默认空，OCR 回落时不填
        questions = []

        if qwen_vl._available():
            _jobs[jid]["job"].status = JobStatus.ocr_running  # 复用状态表示"识别中"
            print(f"[BG] Qwen-VL extracting questions for {jid}...", flush=True)
            qwen_result = qwen_vl.extract_questions(contents)
            qwen_latency = int((time.time() - t_start) * 1000)
            usage = qwen_result.get("usage", {}) if qwen_result.get("success") else {}
            input_tokens = usage.get("prompt_tokens", 0) if isinstance(usage, dict) else 0
            output_tokens = usage.get("completion_tokens", 0) if isinstance(usage, dict) else 0
            image_tokens = usage.get("prompt_tokens_details", {}).get("image_tokens", 0) if isinstance(usage, dict) else 0

            pricing = get_active_pricing("aliyun_dashscope", "qwen-vl-max")

            _log = make_log_entry(
                task_id=jid,
                provider_name="aliyun_dashscope",
                model_name="qwen-vl-max",
                feature_code="qwen_vl_parse_homework",
                trace_id=trace_id,
                sub_stage="fullpage_extract",
                latency_ms=qwen_latency,
                success=qwen_result["success"],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                image_count=1,
                image_total_bytes=len(contents),
                parent_user_id=parent_id,
                child_id=child_id,
                billing_status="free_tier" if qwen_result["success"] else "failed",
                pricing=pricing,
                blocks_count=len(qwen_result.get("questions", [])),
            )
            qwen_parse_call_id = _log["id"]
            total_parse_cost += _log.get("cost_cny", 0.0)
            _model_calls.append(_log)
            _db.save_model_call(_log)

            if qwen_result["success"] and qwen_result["questions"]:
                use_qwen_vl = True
                raw_questions = qwen_result["questions"]
                question_count = len(raw_questions)
                print(f"[BG] Qwen-VL extracted {question_count} questions in {qwen_latency}ms", flush=True)

                # 分组展示用：用 raw_content 作为视觉描述
                shared_vd = qwen_result.get("raw_content", f"Qwen-VL 全图识别，共 {question_count} 题")

                # 按题数平摊 Qwen-VL 解析成本
                parse_cost_per_q = total_parse_cost / question_count if question_count > 0 else 0.0

                for i, rq in enumerate(raw_questions):
                    qid = f"{jid}-{rq.get('number', i+1)}-{i}"
                    q_text = rq.get("content", f"第{rq.get('number', i+1)}题")
                    # 安全提取 student_answer：null/None/空字符串视为无答案
                    student_ans = rq.get("student_answer")
                    if student_ans is None or (isinstance(student_ans, str) and not student_ans.strip()):
                        student_ans = None
                    # 安全提取分组字段
                    section_title = rq.get("section_title")
                    if section_title and isinstance(section_title, str) and not section_title.strip():
                        section_title = None
                    section_index = rq.get("section_index")
                    try:
                        section_index = int(section_index) if section_index is not None else None
                    except (ValueError, TypeError):
                        section_index = None
                    sub_index = rq.get("sub_index")
                    try:
                        sub_index = int(sub_index) if sub_index is not None else None
                    except (ValueError, TypeError):
                        sub_index = None
                    # 安全解析题号：Qwen-VL 可能返回非数字（如 "VOCABULARY 1"）
                    raw_no = rq.get("number", i+1)
                    try:
                        q_no = int(raw_no)
                    except (ValueError, TypeError):
                        q_no = i + 1
                    questions.append(Question(
                        question_id=qid,
                        question_number=q_no,
                        question_text=q_text,
                        bbox=[0, 0, 0, 0],  # 全图识题无精确 bbox
                        visual_description=shared_vd,
                        status=QuestionStatus.completed,
                        student_answer=student_ans,
                        section_title=section_title,
                        section_index=section_index,
                        sub_index=sub_index,
                    ))
                    _db.create_question_item(qid, aid, page_id, q_no, q_text, [0, 0, 0, 0], shared_vd[:200],
                                              source_call_id=qwen_parse_call_id, parse_cost_allocated_cny=parse_cost_per_q)
            else:
                print(f"[BG] Qwen-VL failed: {qwen_result.get('error', 'no questions')}, falling back to OCR", flush=True)

        # ── OCR 回落（Qwen-VL 失败或不可用时）──
        if not use_qwen_vl:
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
                trace_id=trace_id,
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
                _db.create_question_item(qid, aid, page_id, cq["question_number"], cq["question_text"], cq["bbox"])
            print(f"[BG] OCR+Cut: {len(extracted['blocks'])} blocks → {len(questions)} questions", flush=True)

        # Stage: vision_reviewing - Qwen-VL 逐题复审（仅 OCR 回落路径）
        if not use_qwen_vl:
            _jobs[jid]["job"].status = JobStatus.vision_reviewing
            MAX_SCHEMA_RETRIES = 2
            MAX_NETWORK_RETRIES = 3
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
                        _db.create_question_item(q.question_id, aid, page_id, q.question_number, q.question_text, q.bbox or [], q.visual_description or "")

                        _vlog = make_log_entry(
                            task_id=jid,
                            question_id=q.question_id,
                            job_id=jid,
                            provider_name="aliyun_dashscope",
                            model_name="qwen-vl-max",
                            feature_code="qwen_vl_parse_homework",
                            trace_id=trace_id,
                            sub_stage="question_cutting",
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
                        if "429" in err_str or "rate_limit" in err_str or "too many requests" in err_str:
                            network_retries += 1
                            if network_retries > MAX_NETWORK_RETRIES:
                                print(f"[BG] Vision #{qi} RATE LIMITED after {MAX_NETWORK_RETRIES} retries: {ve}", flush=True)
                                q.status = QuestionStatus.failed
                                break
                            delay = 2 ** network_retries
                            _deferred_vision_tasks.append((qi, q, contents, jid, parent_id, child_id, aid, page_id, network_retries, delay))
                            print(f"[BG] Vision #{qi} RATE LIMITED, deferred for {delay}s: {ve}", flush=True)
                            break  # 进延后队列，不阻塞其他题
                        elif "timeout" in err_str or "connection" in err_str or "timed out" in err_str:
                            network_retries += 1
                            if network_retries > MAX_NETWORK_RETRIES:
                                print(f"[BG] Vision #{qi} NETWORK FAILED after {MAX_NETWORK_RETRIES} retries: {ve}", flush=True)
                                q.status = QuestionStatus.failed
                                break
                            wait_s = 2 ** network_retries
                            print(f"[BG] Vision #{qi} network retry {network_retries}/{MAX_NETWORK_RETRIES}, waiting {wait_s}s: {ve}", flush=True)
                            await asyncio.sleep(wait_s)
                        else:
                            schema_retries += 1
                            if schema_retries > MAX_SCHEMA_RETRIES:
                                print(f"[BG] Vision #{qi} SCHEMA FAILED after {MAX_SCHEMA_RETRIES} retries: {ve}", flush=True)
                                q.status = QuestionStatus.failed
                                break
                            print(f"[BG] Vision #{qi} schema retry {schema_retries}/{MAX_SCHEMA_RETRIES}: {ve}", flush=True)
                            await asyncio.sleep(1)
        else:
            print(f"[BG] Qwen-VL full-page mode: skipping per-question vision review", flush=True)

        # ── 延后队列处理（基准 §9/17 — 429 独立队列）────────
        if _deferred_vision_tasks:
            print(f"[BG] Processing {len(_deferred_vision_tasks)} deferred vision tasks...", flush=True)
            MAX_DEFERRED_RETRIES = 3
            deferred_retry = 0
            while _deferred_vision_tasks and deferred_retry < MAX_DEFERRED_RETRIES:
                deferred_retry += 1
                pending = _deferred_vision_tasks[:]
                _deferred_vision_tasks.clear()
                for qi, q, contents, jid, parent_id, child_id, aid, page_id, n_retries, delay in pending:
                    await asyncio.sleep(delay)
                    try:
                        t_vs = time.time()
                        vl_result = qwen_vl.analyze_question(
                            image_bytes=contents, bbox=q.bbox or [0, 0, 0, 0],
                            question_text=q.question_text,
                        )
                        vl_ms = int((time.time() - t_vs) * 1000)
                        q.visual_description = vl_result["visual_description"]
                        q.status = QuestionStatus.completed
                        _db.create_question_item(q.question_id, aid, page_id, q.question_number, q.question_text, q.bbox or [], q.visual_description or "")
                        _vlog = make_log_entry(
                            task_id=jid, question_id=q.question_id, job_id=jid,
                            provider_name="aliyun_dashscope", model_name="qwen-vl-max",
                            feature_code="qwen_vl_parse_homework",
                            trace_id=trace_id, sub_stage="question_cutting",
                            latency_ms=vl_ms,
                            success=vl_result["success"], parent_user_id=parent_id,
                            child_id=child_id, error_code=vl_result.get("error"),
                            billing_status="free_tier" if vl_result["success"] else "failed",
                            prompt_name="qwen_vl_analyze", retry_count=n_retries,
                        )
                        _model_calls.append(_vlog)
                        _db.save_model_call(_vlog)
                        print(f"[BG] Deferred Vision #{qi} succeeded after {n_retries} retries: {vl_ms}ms", flush=True)
                    except Exception as ve:
                        err_str = str(ve).lower()
                        next_retries = n_retries + 1
                        if next_retries > MAX_NETWORK_RETRIES:
                            print(f"[BG] Deferred Vision #{qi} FAILED after {n_retries} retries: {ve}", flush=True)
                            q.status = QuestionStatus.failed
                        elif "429" in err_str or "rate_limit" in err_str:
                            next_delay = 2 ** next_retries
                            _deferred_vision_tasks.append((qi, q, contents, jid, parent_id, child_id, aid, page_id, next_retries, next_delay))
                            print(f"[BG] Deferred Vision #{qi} RE-RATE-LIMITED, re-deferred {next_delay}s", flush=True)
                        else:
                            print(f"[BG] Deferred Vision #{qi} non-429 error: {ve}", flush=True)
                            q.status = QuestionStatus.failed
            if _deferred_vision_tasks:
                print(f"[BG] {len(_deferred_vision_tasks)} deferred tasks abandoned after {MAX_DEFERRED_RETRIES} batch retries", flush=True)

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
        # 存储解析元数据供 save_result 写入 parse_jobs
        _jobs[jid]["parse_mode"] = "qwen_vl"
        _jobs[jid]["parser_provider"] = "aliyun_dashscope"
        _jobs[jid]["parser_model"] = "qwen-vl-max"
        _jobs[jid]["qwen_parse_call_id"] = qwen_parse_call_id
        _jobs[jid]["total_parse_cost_cny"] = total_parse_cost
        # ── Stage: grading ── DeepSeek 批量判对错 ──
        grading_cost = 0.0
        try:
            grading_cost = await grade_answers(jid, questions, trace_id, parent_id, child_id)
        except Exception as _ge:
            print(f"[BG] Grading stage failed (non-blocking): {_ge}", flush=True)
        total_parse_cost += grading_cost
        save_result(jid, questions, now, file, final_status)
        # 诊断：判对错保存统计
        _with_g = sum(1 for q in questions if (getattr(q, "is_correct", None) is not None) or (isinstance(q, dict) and q.get("is_correct") is not None))
        _with_sa = sum(1 for q in questions if (getattr(q, "student_answer", None)) or (isinstance(q, dict) and q.get("student_answer")))
        print(f"[diag] grading_saved jid={jid} questions={len(questions)} with_grading={_with_g} with_child_answer={_with_sa}", flush=True)
        print(f"[diag] worker_completed jid={jid} status={final_status.value} qcount={len(questions)} cost_cny={total_parse_cost:.4f}", flush=True)

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
        _log_error(f"worker_failed jid={jid}: {e}", trace_id=trace_id)
        # 保存错误到 job entry，让 status API 返回可见
        _jobs[jid]["error_code"] = str(e)[:200]
        _jobs[jid]["progress"] = f"worker_crash: {str(e)[:100]}"
        # 防御：job entry 可能被破坏
        try:
            if "job" in _jobs[jid] and hasattr(_jobs[jid]["job"], "status"):
                _jobs[jid]["job"].status = JobStatus.failed
        except Exception:
            pass
        # 尝试保存失败状态，让前端能看到
        try:
            save_result(jid, [], now, file, JobStatus.failed)
        except Exception:
            pass


# ─── 2. GET /api/parse-jobs/recent (before {job_id} to avoid conflict) ──
@app.get("/api/parse-jobs/recent")
@limiter.limit("20/minute")
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
async def recover_by_job_id(job_id: str, user: tuple = Depends(get_current_user)):
    """按 job_id 精确恢复任务状态（poll 失败后前端使用）。查内存+DB。"""
    try:
        _, child_id = user
        print(f"[diag] recover_by_jid jid={job_id} start", flush=True)
        # 1. 内存
        j = _jobs.get(job_id)
        if j:
            job = j.get("job")
            if job:
                st = str(job.status) if hasattr(job, "status") else job.get("status", "?")
                qcount = job.questions_count if hasattr(job, "questions_count") else len(j.get("questions", []))
                print(f"[diag] recover_by_jid jid={job_id} result=memory status={st} qcount={qcount}", flush=True)
                # 诊断 grading 字段
                qs = j.get("questions", [])
                _wg = sum(1 for q in qs if (getattr(q, "is_correct", None) is not None) or (isinstance(q, dict) and q.get("is_correct") is not None))
                print(f"[diag] recover_return jid={job_id} qcount={qcount} with_grading={_wg}", flush=True)
                return {"ok": True, "data": {"job_id": job_id, "status": st, "questions_count": qcount, "file_name": getattr(job, "file_name", "") or job.get("file_name", "")}, "request_id": uuid.uuid4().hex}
        # 2. DB
        import sqlite3, json
        c = _db._conn()
        row = c.execute("SELECT status, questions_count, file_name FROM parse_jobs WHERE job_id=? AND deleted_at IS NULL", (job_id,)).fetchone()
        c.close()
        if row:
            print(f"[diag] recover_by_jid jid={job_id} result=db status={row['status']} qcount={row['questions_count']}", flush=True)
            return {"ok": True, "data": {"job_id": job_id, "status": row["status"], "questions_count": row["questions_count"], "file_name": row["file_name"] or ""}, "request_id": uuid.uuid4().hex}
        print(f"[diag] recover_by_jid jid={job_id} result=not_found", flush=True)
        return {"ok": False, "code": "not_found", "message": "任务不存在", "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "recover_error", "message": str(e), "request_id": uuid.uuid4().hex}


# ─── 2.4. GET /api/parse-jobs/recover?client_upload_id=xxx ──
@app.get("/api/parse-jobs/recover")
@limiter.limit("10/minute")
async def recover_parse_job(client_upload_id: str, user: tuple = Depends(get_current_user)):
    """上传超时后按 client_upload_id 恢复 job。返回 uploaded/processing/completed 状态。"""
    try:
        parent_id, child_id = user
        if not client_upload_id:
            print(f"[diag] recover_get cu=(empty) result=missing_param", flush=True)
            return {"ok": False, "code": "missing_param", "message": "缺少 client_upload_id", "request_id": uuid.uuid4().hex}
        # 先查内存（更新鲜）
        for jid, j in _jobs.items():
            if j.get("child_id") == child_id and j.get("client_upload_id") == client_upload_id:
                job = j.get("job")
                if job and hasattr(job, "status") and str(job.status) != "failed":
                    st = str(job.status)
                    print(f"[diag] recover_get cu={client_upload_id[:16]} result=found_memory jid={jid} status={st}", flush=True)
                    return {"ok": True, "data": {"job_id": jid, "status": st, "questions_count": job.questions_count or len(j.get("questions", [])), "file_name": job.file_name}, "request_id": uuid.uuid4().hex}
        # 再查 DB（跨重启）
        row = _db.get_job_by_client_upload_id(child_id, client_upload_id)
        if row:
            print(f"[diag] recover_get cu={client_upload_id[:16]} result=found_db jid={row.get('job_id','?')} status={row.get('status','?')}", flush=True)
            return {"ok": True, "data": row, "request_id": uuid.uuid4().hex}
        print(f"[diag] recover_get cu={client_upload_id[:16]} result=not_found", flush=True)
        return {"ok": False, "code": "not_found", "message": "未找到对应任务", "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "recover_error", "message": str(e), "request_id": uuid.uuid4().hex}


# ─── 2.5. DELETE /api/parse-jobs/{job_id} (before GET {job_id} to avoid conflict) ──
@app.delete("/api/parse-jobs/{job_id}")
@limiter.limit("5/minute")
async def delete_parse_job(job_id: str, user: tuple = Depends(get_current_user)):
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
async def get_parse_job_status(job_id: str, user: tuple = Depends(get_current_user)):
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
            print(f"[diag] poll_get_job jid={job_id} status={job_status} qcount={job_obj.questions_count} poll=#{pc}", flush=True)
        return {"ok": True, "data": data, "request_id": uuid.uuid4().hex}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "code": "status_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 4. GET /api/parse-jobs/{job_id}/questions ─────────────
@app.get("/api/parse-jobs/{job_id}/questions")
@limiter.limit("30/minute")
async def get_parse_job_questions(job_id: str, user: tuple = Depends(get_current_user)):
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
            print(f"[diag] questions_return jid={job_id} source=memory qcount={len(data)} with_grading={_with_g} with_child_answer={_with_sa}", flush=True)
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
async def get_question_detail(question_id: str):
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
        credits = _credit_balances.setdefault(parent_id, 50)
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
            print(f"[Tutor] ai_tutoring_messages write failed (non-blocking): {_te}", flush=True)

        # Log model call — SQLite 持久化
        pricing = get_active_pricing("deepseek", "deepseek-chat")
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
            model_name="deepseek-chat",
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
        except Exception as _ce:
            print(f"[Tutor] credit_ledger cost write failed (non-blocking): {_ce}", flush=True)

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
async def payment_callback():
    """支付回调占位，Phase 2 实现验签与幂等"""
    return {"ok": True, "message": "callback received (placeholder)", "request_id": uuid.uuid4().hex}

# ─── 12. GET /api/billing/credit-ledger (Phase 0 占位) ─────
@app.get("/api/billing/credit-ledger")
@limiter.limit("20/minute")
async def get_credit_ledger(user: tuple = Depends(get_current_user)):
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
@limiter.limit("10/minute")
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
                print(f"[HW] DeepSeek parsed {len(subjects)} subjects in {ds_result['latency_ms']}ms", flush=True)
                return {
                    "ok": True,
                    "data": HomeworkParseResponse(subjects=subjects, raw_text=body.text).model_dump(),
                    "request_id": uuid.uuid4().hex,
                }
            print(f"[HW] DeepSeek failed: {ds_result.get('error')}, falling back to mock", flush=True)

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
