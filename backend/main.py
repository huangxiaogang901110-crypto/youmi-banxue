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
from fastapi.staticfiles import StaticFiles
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
from routes.mistakes_routes import router as mistakes_router
from routes.homework_routes import router as homework_router
from routes.auth_routes import router as auth_router
from routes.entitlement_routes import router as entitlement_router

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
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://localhost:3003",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:3002",
        "http://127.0.0.1:3003",
        "http://39.107.119.136:3000",
        "http://39.107.119.136:3001",
        "https://youmi.xyz",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── 限流 ──────────────────────────────────────────────────
app.state.limiter = limiter
app.mount("/job-images", StaticFiles(directory="/tmp/yomi"), name="job-images")
app.include_router(parse_router)
app.include_router(mistakes_router)
app.include_router(homework_router)
app.include_router(auth_router)
app.include_router(entitlement_router)

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
