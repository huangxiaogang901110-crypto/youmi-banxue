"""
悠米伴学 鉴权模块 — JWT + 纯标准库哈希
Phase 1: 用户持久化到 SQLite（db.py）
"""
import hashlib
import hmac
import json
import os
import time
from base64 import urlsafe_b64encode, urlsafe_b64decode
from dataclasses import dataclass
from typing import Optional
from logger import info, warning, error, debug

# ─── JWT ───────────────────────────────────────────────────

JWT_SECRET = os.getenv("JWT_SECRET", "yomi-dev-secret-change-in-production")
JWT_EXPIRE_SECONDS = 7 * 24 * 3600  # 7 天


def _b64encode(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(s: str) -> bytes:
    s += "=" * (4 - len(s) % 4)
    return urlsafe_b64decode(s)


def create_token(payload: dict) -> str:
    """签发 JWT"""
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload["iat"] = int(time.time())
    payload["exp"] = int(time.time()) + JWT_EXPIRE_SECONDS
    body = _b64encode(json.dumps(payload).encode())
    sig = hmac.new(JWT_SECRET.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{_b64encode(sig)}"


def verify_token(token: str) -> Optional[dict]:
    """验证 JWT，返回 payload 或 None"""
    try:
        parts = token.removeprefix("Bearer ").strip().split(".")
        if len(parts) != 3:
            return None
        header_b64, body_b64, sig_b64 = parts
        expected_sig = hmac.new(
            JWT_SECRET.encode(),
            f"{header_b64}.{body_b64}".encode(),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_b64decode(sig_b64), expected_sig):
            return None
        payload = json.loads(_b64decode(body_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ─── 密码哈希 ──────────────────────────────────────────────

def hash_password(password: str) -> str:
    """SHA-256 + salt（Phase 1 够用）"""
    salt = os.urandom(16).hex()
    h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{h}"


def verify_password(password: str, stored: str) -> bool:
    salt, h = stored.split(":", 1)
    return h == hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


# ─── 用户模型 ──────────────────────────────────────────────

@dataclass
class ParentUser:
    id: str
    phone: str
    password_hash: str
    name: str = ""


@dataclass
class ChildProfile:
    id: str
    parent_id: str
    name: str
    avatar: str = ""


# ─── 内存用户存储 + SQLite 持久化 ─────────────────────────

_parents: dict[str, ParentUser] = {}
_children: dict[str, ChildProfile] = {}

import db as _db


def init_seed_users():
    """初始化种子用户。优先从 SQLite 恢复，恢复不到则用内存种子并落库。"""
    if _parents:
        return

    # 尝试从 SQLite 恢复
    try:
        db_parents = _db.load_parent_users()
        db_children = _db.load_child_profiles()
        if db_parents:
            for pid, p in db_parents.items():
                _parents[pid] = ParentUser(id=p["id"], phone=p["phone"], password_hash=p["password_hash"], name=p.get("name", ""))
            for cid, c in db_children.items():
                _children[cid] = ChildProfile(id=c["id"], parent_id=c["parent_id"], name=c["name"], avatar=c.get("avatar", ""))
                # 如果 child 还没有 jwt_secret（老数据），自动生成一个
                if not c.get("jwt_secret"):
                    secret = generate_child_secret()
                    _db.set_child_jwt_secret(cid, secret)
            info(f"[auth] 从 SQLite 恢复 {len(_parents)} 家长, {len(_children)} 孩子")
            return
    except Exception as e:
        info(f"[auth] SQLite 恢复失败: {e}，使用内存种子")

    # Fallback: 内存种子 + 落库
    pid = "p001"
    cid = "c001"
    p = ParentUser(id=pid, phone="13800138000", password_hash=hash_password("123456"), name="测试家长")
    c = ChildProfile(id=cid, parent_id=pid, name="小明")
    _parents[pid] = p
    _children[cid] = c

    # 生成 child jwt_secret（Phase 1 每 child 独立 secret）
    child_secret = generate_child_secret()

    # 落库
    try:
        _db.save_parent_user(pid, p.phone, p.password_hash, p.name)
        _db.save_child_profile(cid, pid, c.name, jwt_secret=child_secret)
        info("[auth] 种子用户已落库 SQLite")
    except Exception as e:
        info(f"[auth] 种子用户落库失败: {e}")


def get_parent_by_phone(phone: str) -> Optional[ParentUser]:
    for p in _parents.values():
        if p.phone == phone:
            return p
    return None


def get_children(parent_id: str) -> list[ChildProfile]:
    return [c for c in _children.values() if c.parent_id == parent_id]


# ─── Child JWT（Phase 1 子 token，每 child 独立 secret）─────

CHILD_JWT_EXPIRE_SECONDS = 30 * 24 * 3600  # 30 天


def generate_child_secret() -> str:
    """生成随机 child jwt_secret。"""
    return os.urandom(32).hex()


def create_child_token(child_id: str, parent_id: str, jwt_secret: str) -> str:
    """签发 child 专用 JWT，用 child 自己的 secret 签名。"""
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = {
        "child_id": child_id,
        "parent_id": parent_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + CHILD_JWT_EXPIRE_SECONDS,
    }
    body = _b64encode(json.dumps(payload).encode())
    sig = hmac.new(jwt_secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{_b64encode(sig)}"


def verify_child_token(token: str, jwt_secret: str) -> Optional[dict]:
    """验证 child JWT，返回 payload 或 None。"""
    try:
        parts = token.removeprefix("Bearer ").strip().split(".")
        if len(parts) != 3:
            return None
        header_b64, body_b64, sig_b64 = parts
        expected_sig = hmac.new(
            jwt_secret.encode(),
            f"{header_b64}.{body_b64}".encode(),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_b64decode(sig_b64), expected_sig):
            return None
        payload = json.loads(_b64decode(body_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None
