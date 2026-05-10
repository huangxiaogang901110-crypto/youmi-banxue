"""
model_call_log 工厂函数 — 基准 Table 12 统一字段
消除 main.py 中成功/失败路径的复制粘贴
"""

import uuid
from typing import Optional, Any


def make_log_entry(
    *,
    task_id: str,
    provider_name: str,
    model_name: str,
    feature_code: str,
    latency_ms: int,
    success: bool,
    parent_user_id: str = "demo_parent_001",
    child_id: str = "demo_child_001",
    question_id: Optional[str] = None,
    request_id: Optional[str] = None,
    prompt_name: str = "default",
    prompt_version: str = "1.0",
    schema_version: str = "1.0",
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost: float = 0.0,
    credit_cost: int = 0,
    billing_status: str = "free_tier",
    error_code: Optional[str] = None,
    retry_count: int = 0,
    **extra: Any,
) -> dict:
    """
    Returns a dict matching model_call_log schema (Table 12).
    Extra kwargs become additional fields (e.g. blocks_count for OCR).
    """
    base = {
        "id": uuid.uuid4().hex,
        "request_id": request_id,
        "task_id": task_id,
        "question_id": question_id,
        "parent_user_id": parent_user_id,
        "child_id": child_id,
        "provider_name": provider_name,
        "model_name": model_name,
        "feature_code": feature_code,
        "prompt_name": prompt_name,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": latency_ms,
        "estimated_cost": estimated_cost,
        "credit_cost": credit_cost,
        "billing_status": billing_status,
        "success": success,
        "error_code": error_code,
        "retry_count": retry_count,
    }
    base.update(extra)
    return base
