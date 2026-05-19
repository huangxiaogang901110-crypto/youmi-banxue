"""
悠米伴学 — 权益/支付 API 路由
GET  /api/me/entitlement      — 查询权益状态 & 学豆余额
POST /api/activation/redeem    — 激活码核销
POST /api/payment/create-order — 支付下单（Phase 0 占位）
POST /api/payment/callback     — 支付回调（Phase 2 实现）
GET  /api/billing/credit-ledger— 学豆流水查询
"""

import asyncio
import uuid
import time
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import db as _db
from limiter import limiter
from dependencies import get_current_user
from models import EntitlementStatus, Entitlement

router = APIRouter()

# ─── 6. GET /api/me/entitlement ────────────────────────────

@router.get("/api/me/entitlement")
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


@router.post("/api/activation/redeem")
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


@router.post("/api/payment/create-order")
@limiter.limit("3/minute")
async def create_payment_order(body: CreateOrderRequest, request: Request = None):
    """支付下单占位，Phase 0 返回 Mock"""
    try:
        order_id = f"order-{uuid.uuid4().hex[:12]}"
        return {"ok": True, "data": {"order_id": order_id, "plan_code": body.plan_code, "amount": body.amount, "status": "pending", "payment_url": "https://mock.pay.example.com"}, "request_id": uuid.uuid4().hex}
    except Exception as e:
        return {"ok": False, "code": "payment_error", "message": str(e), "request_id": uuid.uuid4().hex}


# ─── 11. POST /api/payment/callback (Phase 2 实现) ─────────

@router.post("/api/payment/callback")
@limiter.limit("30/minute")
async def payment_callback(request: Request):
    """支付回调占位，Phase 2 实现验签与幂等"""
    return {"ok": True, "message": "callback received (placeholder)", "request_id": uuid.uuid4().hex}


# ─── 12. GET /api/billing/credit-ledger (Phase 0 占位) ─────

@router.get("/api/billing/credit-ledger")
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
