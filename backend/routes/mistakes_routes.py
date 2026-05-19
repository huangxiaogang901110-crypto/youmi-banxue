"""
悠米伴学 — 错题本 API 路由
GET /api/mistakes — 获取错题列表
DELETE /api/mistakes/{mistake_id} — 软删除错题
PATCH /api/mistakes/{mistake_id} — 更新错题状态/类型
"""

from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
import uuid

import db as _db
from limiter import limiter
from dependencies import get_current_user

router = APIRouter()


class UpdateMistakeRequest(BaseModel):
    mastery_status: str | None = None
    error_type_code: str | None = None
    reason_desc: str | None = None


# ─── 13. GET /api/mistakes ──────────────────────────────────
@router.get("/api/mistakes")
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
@router.delete("/api/mistakes/{mistake_id}")
@limiter.limit("10/minute")
async def delete_mistake_item(mistake_id: str, request: Request, user: tuple = Depends(get_current_user)):
    """软删除错题（联级删除辅导对话）"""
    try:
        _db.delete_mistake(mistake_id)
        return {"ok": True, "data": {"id": mistake_id}, "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "mistakes_delete_error", "message": str(e), "request_id": uuid.uuid4().hex}


# ─── 13c. PATCH /api/mistakes/{mistake_id} ──────────────────
@router.patch("/api/mistakes/{mistake_id}")
@limiter.limit("10/minute")
async def update_mistake_item(mistake_id: str, body: UpdateMistakeRequest, request: Request, user: tuple = Depends(get_current_user)):
    """更新错题状态/类型"""
    try:
        _db.update_mistake(mistake_id, body.mastery_status, body.error_type_code, body.reason_desc)
        return {"ok": True, "data": {"id": mistake_id}, "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "mistakes_update_error", "message": str(e), "request_id": uuid.uuid4().hex}
