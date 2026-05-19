"""
悠米伴学 — 鉴权 API 路由
POST /api/auth/login          — 手机号 + 密码登录，返回 JWT
POST /api/auth/register       — 注册新账户
GET  /api/auth/children       — 获取当前家长的所有孩子
POST /api/auth/switch-child   — 切换活跃孩子，返回新 JWT
"""

import uuid
from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel

from auth import (
    create_token, hash_password, verify_password,
    get_parent_by_phone, get_children,
    ParentUser, ChildProfile, _parents, _children,
)
import db as _db
from limiter import limiter
from dependencies import get_current_user

router = APIRouter()

# ─── 鉴权端点 ──────────────────────────────────────────────

class LoginRequest(BaseModel):
    phone: str
    password: str

class RegisterRequest(BaseModel):
    phone: str
    password: str
    name: str = ""


@router.post("/api/auth/login")
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


@router.post("/api/auth/register")
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


@router.get("/api/auth/children")
@limiter.limit("10/minute")
async def list_children(request: Request, user: tuple = Depends(get_current_user)):
    """获取当前家长的所有孩子"""
    parent_id, _ = user
    children = get_children(parent_id)
    return {"ok": True, "data": [{"id": c.id, "name": c.name, "avatar": c.avatar} for c in children]}


# ─── 14. POST /api/auth/switch-child ────────────────────────

class SwitchChildRequest(BaseModel):
    child_id: str


@router.post("/api/auth/switch-child")
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
