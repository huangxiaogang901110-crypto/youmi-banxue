"""
悠米伴学 鉴权模块 — JWT + bcrypt
Phase 1 最小方案：无外部依赖，纯标准库 JWT + hashlib
"""
import hashlib
import hmac
import json
import os
import time
from base64 import urlsafe_b64encode, urlsafe_b64decode
from dataclasses import dataclass
from typing import Optional

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
    """SHA-256 + salt（Phase 1 够用，生产切 bcrypt）"""
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


# ─── 内存用户存储（Phase 1 过渡，后续迁 SQLite）───────────

_parents: dict[str, ParentUser] = {}
_children: dict[str, ChildProfile] = {}


def init_seed_users():
    """初始化种子用户（idempotent）"""
    if _parents:
        return
    pid = "p001"
    cid = "c001"
    _parents[pid] = ParentUser(
        id=pid,
        phone="13800138000",
        password_hash=hash_password("123456"),
        name="测试家长",
    )
    _children[cid] = ChildProfile(id=cid, parent_id=pid, name="小明")


def get_parent_by_phone(phone: str) -> Optional[ParentUser]:
    for p in _parents.values():
        if p.phone == phone:
            return p
    return None


def get_children(parent_id: str) -> list[ChildProfile]:
    return [c for c in _children.values() if c.parent_id == parent_id]
