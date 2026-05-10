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
from tutor_prompt import build_tutor_messages
import db as _db

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
    allow_origins=["http://localhost:3000", "http://39.107.119.136:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    return JSONResponse(status_code=exc.status_code, content={
        "ok": False, "code": f"http_{exc.status_code}",
        "message": str(exc.detail), "request_id": uuid.uuid4().hex
    })

@app.exception_handler(Exception)
async def universal_exc(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={
        "ok": False, "code": "internal_error",
        "message": "服务器内部错误", "request_id": uuid.uuid4().hex
    })

# ─── Mock 数据 ─────────────────────────────────────────────
MOCK_ENTITLEMENT = Entitlement(
    user_id="demo_parent_001", child_id="demo_child_001",
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
_db.init()
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


# ─── 健康检查 ──────────────────────────────────────────────
@app.get("/health")
def health_check():
    return {"ok": True, "message": "悠米伴学 API 运行中"}

# ─── 1. POST /api/parse-jobs ───────────────────────────────
@app.post("/api/parse-jobs")
async def create_parse_job(
    file: UploadFile = File(...),
    client_task_id: str = Form(None),
    page_range: str = Form("1"),
    source_type: str = Form("web_upload"),
    request: Request = None,
    background_tasks: BackgroundTasks = None,
):
    try:
        child_id = request.headers.get("X-Child-Id", "demo_child_001") if request else "demo_child_001"
        parent_id = request.headers.get("X-Demo-User-Id", "demo_parent_001") if request else "demo_parent_001"

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
        # 启动后台 worker
        asyncio.create_task(worker_process_job(jid, contents, file, now))
        return {"ok": True, "data": {"job_id": jid, "status": "uploaded", "file_name": file.filename}, "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "parse_failed", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 后台任务 worker（模块级，基准 Table 22 R1）────────────
async def worker_process_job(jid: str, contents: bytes, file, now: str):
    """后台异步执行：OCR → 切题 → Qwen-VL → Schema 校验 → 保存"""
    print(f"[BG] Starting OCR for {jid}...", flush=True)
    t_start = time.time()

    # 持久化原始图片供 vision 二次路由使用
    import os as _osp
    _osp.makedirs("/tmp/yomi", exist_ok=True)
    with open(f"/tmp/yomi/{jid}.jpg", "wb") as _pf:
        _pf.write(contents)

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
        print(f"[BG] Cut {len(extracted['blocks'])} blocks → {len(questions)} questions", flush=True)

        # Stage: vision_reviewing - Qwen-VL (Table 11 R3)
        _jobs[jid]["job"].status = JobStatus.vision_reviewing
        qwen_vl = QwenVLClient()
        for qi, q in enumerate(questions):
            try:
                t_vs = time.time()
                vl_result = qwen_vl.analyze_question(
                    image_bytes=contents,
                    bbox=q.bbox or [0, 0, 0, 0],
                    question_text=q.question_text,
                )
                vl_ms = int((time.time() - t_vs) * 1000)
                q.visual_description = vl_result["visual_description"]

                _vlog = make_log_entry(
                    task_id=jid,
                    question_id=q.question_id,
                    provider_name="dashscope",
                    model_name="qwen-vl-plus",
                    feature_code="vision_cutting",
                    latency_ms=vl_ms,
                    success=vl_result["success"],
                    error_code=vl_result.get("error"),
                    billing_status="free_tier" if vl_result["success"] else "failed",
                    prompt_name="qwen_vl_analyze",
                )
                _model_calls.append(_vlog)
                _db.save_model_call(_vlog)
                print(f"[BG] Vision #{qi}: {vl_ms}ms success={vl_result['success']}", flush=True)
            except Exception as ve:
                print(f"[BG] Vision #{qi} FAILED: {ve}", flush=True)
                q.status = QuestionStatus.failed

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
            error_code=str(e)[:100],
            billing_status="failed",
        )
        _model_calls.append(_flog)
        _db.save_model_call(_flog)
        print(f"[BG] OCR FAILED: {e}", flush=True)
        _jobs[jid]["job"].status = JobStatus.failed

# ─── 2. GET /api/parse-jobs/{job_id} ───────────────────────
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

# ─── 3. GET /api/parse-jobs/{job_id}/questions ─────────────
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

@app.post("/api/questions/{question_id}/status")
async def update_question_status(question_id: str, body: QuestionStatusRequest):
    """更新题目状态：已掌握 / 加入错题本"""
    try:
        if body.status not in ("mastered", "mistake_book", "needs_review"):
            return {"ok": False, "code": "invalid_status", "message": "无效状态", "request_id": uuid.uuid4().hex}
        return {"ok": True, "data": {"question_id": question_id, "status": body.status}, "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "status_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 4. POST /api/questions/{question_id}/tutor ────────────
@app.post("/api/questions/{question_id}/tutor")
async def tutor_question(question_id: str, body: TutorRequest):
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
        child_id_for_credit = "demo_child_001"  # Phase 0
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

        # Chat limit check (Phase 0: 10 rounds)
        history = _tutor_chats.get(question_id, [])
        MAX_ROUNDS = 10
        if len(history) >= MAX_ROUNDS * 2:  # user+assistant pairs
            # Phase 0: 超限截断保留最近轮次（基准 Table 24 摘要降级简化版）
            keep = (MAX_ROUNDS - 2) * 2
            history = history[-keep:]
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

        # 从持久化存储读取原始图片
        import os as _osp
        jid_from_q = question_id[:12]  # question_id = {jid}-{n}-{i}
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
            image_bytes = _pf.read()

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
        vl_result = qwen_vl.analyze_question(
            image_bytes=image_bytes,
            bbox=q_bbox or [0, 0, 0, 0],
            question_text=q_text,
        )
        q_found.visual_description = vl_result["visual_description"]

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
async def get_entitlement():
    try:
        await asyncio.sleep(0.2)
        return {"ok": True, "data": MOCK_ENTITLEMENT.model_dump(), "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "entitlement_error", "message": str(e), "request_id": uuid.uuid4().hex}

# ─── 7. POST /api/activation/redeem ────────────────────────
class ActivationRequest(BaseModel):
    code: str

@app.post("/api/activation/redeem")
async def redeem_activation(body: ActivationRequest):
    try:
        await asyncio.sleep(0.3)
        if body.code == "YOMI-FREE-2024":
            return {"ok": True, "data": {"activated": True, "message": "激活成功，获得 100 学豆", "credit_added": 100}, "request_id": uuid.uuid4().hex}
        return {"ok": False, "code": "invalid_code", "message": "激活码无效", "request_id": uuid.uuid4().hex}
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
