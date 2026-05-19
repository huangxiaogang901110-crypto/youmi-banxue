"""
悠米后端轻量自动测试器 v4
目标：8001 开发后端基础链路回归测试
红线：禁止 8000、禁止生产库 /srv/yomi/yomi.db
"""
import os
import sys
import sqlite3
import pytest
import httpx


# ═══════════════════════════════════════
# 生产库保护（最先执行）
# ═══════════════════════════════════════
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "yomi.db")
DB_REAL = os.path.realpath(DB_PATH)

if DB_REAL == "/srv/yomi/yomi.db" or "/srv/yomi/" in DB_REAL:
    raise RuntimeError(
        f"⛔ 生产库保护触发！\n"
        f"   当前 yomi.db realpath: {DB_REAL}\n"
        f"   禁止连接生产库。请确认测试环境。"
    )


# ═══════════════════════════════════════
# 配置
# ═══════════════════════════════════════
BASE = os.environ.get("TEST_BASE", "http://localhost:8001")

# 禁止 8000
if "8000" in BASE:
    raise RuntimeError(
        f"⛔ TEST_BASE 包含 8000（生产端口），禁止测试。当前值: {BASE}"
    )


def get_test_user(order="DESC"):
    """从测试库取一个稳定用户
    order: DESC=最新, ASC=最早
    """
    if not os.path.exists(DB_PATH):
        raise RuntimeError(f"❌ 测试库不存在: {DB_PATH}")

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT phone FROM parent_users ORDER BY id {order} LIMIT 1")
        row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise RuntimeError(
            "❌ 测试库无用户。请先在 3001 注册一个测试账号。"
        )
    return row[0]


TEST_PHONE = os.environ.get("TEST_PHONE") or get_test_user("DESC")
TEST_PHONE_ALT = os.environ.get("TEST_PHONE_ALT") or get_test_user("ASC")
TEST_PWD = os.environ.get("TEST_PWD", "123456")
TEST_PWD_ALT = os.environ.get("TEST_PWD_ALT", "123456")

# ═══════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════

@pytest.fixture
async def client():
    """HTTP 客户端，绕过代理直连测试后端"""
    async with httpx.AsyncClient(proxy=None, trust_env=False) as c:
        yield c


@pytest.fixture
async def token(client):
    """登录获取 Bearer token"""
    r = await client.post(f"{BASE}/api/auth/login", json={
        "phone": TEST_PHONE, "password": TEST_PWD
    })
    data = r.json()
    assert data.get("ok") is True or data.get("code") == 0, (
        f"登录失败: {data}\n▶ 手机号={TEST_PHONE} 密码={TEST_PWD}"
    )
    return data["data"]["token"]


# ═══════════════════════════════════════
# 基础端点
# ═══════════════════════════════════════

@pytest.mark.asyncio
async def test_health(client):
    """GET /health 应返回 200"""
    r = await client.get(f"{BASE}/health")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_login_success(client):
    """正确账号密码登录 → ok:true（使用备选账号避免限流）"""
    r = await client.post(f"{BASE}/api/auth/login", json={
        "phone": TEST_PHONE_ALT, "password": TEST_PWD_ALT
    })
    data = r.json()
    assert data.get("ok") is True or data.get("code") == 0, (
        f"登录失败: {data}\n▶ 手机号={TEST_PHONE_ALT}"
    )


@pytest.mark.asyncio
async def test_login_wrong_pwd(client):
    """错误密码 → code != 0"""
    r = await client.post(f"{BASE}/api/auth/login", json={
        "phone": TEST_PHONE, "password": "wrongpassword"
    })
    assert r.json()["code"] != 0


@pytest.mark.asyncio
async def test_login_empty_phone(client):
    """空手机号 → code != 0"""
    r = await client.post(f"{BASE}/api/auth/login", json={
        "phone": "", "password": "123456"
    })
    assert r.json()["code"] != 0


@pytest.mark.asyncio
async def test_register_taken_phone(client):
    """已注册手机号重复注册 → code != 0"""
    r = await client.post(f"{BASE}/api/auth/register", json={
        "phone": TEST_PHONE, "password": "123456", "role": "parent"
    })
    assert r.json()["code"] != 0


# ═══════════════════════════════════════
# 鉴权端点
# ═══════════════════════════════════════

@pytest.mark.asyncio
async def test_me_entitlement_authorized(client, token):
    """带 Bearer token 查权益 → 200"""
    r = await client.get(f"{BASE}/api/me/entitlement", headers={
        "Authorization": f"Bearer {token}"
    })
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_mistakes_authorized(client, token):
    """带 Bearer token 查错题 → 200"""
    r = await client.get(f"{BASE}/api/mistakes", headers={
        "Authorization": f"Bearer {token}"
    })
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_unauthorized(client):
    """无 token → 被拒绝"""
    r = await client.get(f"{BASE}/api/me/entitlement")
    if r.status_code in [401, 403]:
        return
    data = r.json()
    assert data.get("code", 0) != 0, (
        f"未授权请求未被拒绝: status={r.status_code} body={data}"
    )
