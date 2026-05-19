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
from slowapi.errors import RateLimitExceeded
from limiter import limiter

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
from routes.parse_routes import router as parse_router

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
app.state.limiter = limiter
app.include_router(parse_router)

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