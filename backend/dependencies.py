from __future__ import annotations

from fastapi import HTTPException, Header
from typing import Annotated
import db as _db
from auth import verify_token, verify_child_token


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
