"""
悠米伴学 — 作业清单 API 路由
POST /api/homework/parse — DeepSeek 解析作业文本
GET  /api/homework/days  — 获取作业清单历史
POST /api/homework/days  — 保存某天作业清单
"""

import asyncio
import uuid
import re
from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel as PydanticBase

import db as _db
from limiter import limiter
from dependencies import get_current_user
from deepseek_client import DeepSeekClient
from model_logger import make_log_entry
from db import get_active_pricing
from logger import info, warning, error, debug
from job_store import _model_calls

router = APIRouter()

# ─── 8. POST /api/homework/parse ──────────────────────────

class HomeworkParseRequest(PydanticBase):
    text: str

class HomeworkSubjectModel(PydanticBase):
    name: str
    tasks: list[str]

class HomeworkParseResponse(PydanticBase):
    subjects: list[HomeworkSubjectModel]
    raw_text: str


def mock_parse_homework(text: str) -> list[HomeworkSubjectModel]:
    """本地 Mock 解析：科目识别 + 编号列表提取。
    Phase 1 保留作为 DeepSeek 的回落，同时用于演示。
    返回空列表表示『未识别到作业格式』。"""
    subjects: list[HomeworkSubjectModel] = []
    if not text or not text.strip():
        return subjects

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return subjects

    current_subject: str | None = None
    in_homework = False

    for line in lines:
        # 格式A: 「语文」单独一行（科目标题）
        subj_match = re.match(
            r"^(语文|数学|英语|物理|化学|生物|历史|地理|政治|科学|道法|品德|社会"
            r"|美术|音乐|体育|信息技术|信息|编程|书法|劳动|手工|综合|阅读|写作"
            r"|练习册|白皮|卷子|试卷|作业本|口算|听写|默写|背诵|预习|复习|订正)$",
            line
        )
        if subj_match:
            current_subject = subj_match.group(1)
            in_homework = False
            continue

        # 格式B: 「Homework:」/「作业:」区段标记
        if re.match(r"^(Homework|作业|功课|任务清单|今日作业)[：:]", line, re.IGNORECASE):
            raw_tasks = re.sub(r"^(Homework|作业|功课|任务清单|今日作业)[：:]\s*", "", line, re.IGNORECASE)
            in_homework = True
            name = current_subject or "作业"
            tasks = [t.strip() for t in re.split(r"[，,、;；]", raw_tasks) if t.strip()]
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


@router.post("/api/homework/parse")
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
                billing_status="billed" if ds_result["success"] else "failed",
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


@router.get("/api/homework/days")
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


@router.post("/api/homework/days")
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
