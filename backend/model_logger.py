"""
model_call_log 工厂函数 — 基准 Table 12 统一字段
消除 main.py 中成功/失败路径的复制粘贴
支持真实成本计算：tokens × pricing_snapshot → cost_cny
"""

import os
import uuid
from typing import Optional, Any


def _compute_cost_cny(
    input_tokens: int,
    output_tokens: int,
    pricing: dict | None,
    image_count: int = 0,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
) -> tuple[float, str, float, float, float, float]:
    """根据 token 用量和价格快照计算 CNY 成本。
    返回 (cost_cny, pricing_snapshot_id, unit_price_input, unit_price_output,
           unit_price_cache_hit, unit_price_cache_miss)
    输入 token 按缓存命中/未命中分拆计价。
    """
    if not pricing:
        return 0.0, "", 0.0, 0.0, 0.0, 0.0

    pid = pricing.get("id", "")
    up_cache_hit = float(pricing.get("cache_hit_price_per_1m", 0.0))
    up_cache_miss = float(pricing.get("cache_miss_price_per_1m", 0.0))
    up_out = float(pricing.get("output_price_per_1m", 0.0))
    up_in = float(pricing.get("input_price_per_1m", 0.0))

    # 如果缓存字段有值，使用缓存分拆；否则回退到统一输入价
    if cache_hit_tokens > 0 or cache_miss_tokens > 0:
        input_cost = (cache_hit_tokens * up_cache_hit + cache_miss_tokens * up_cache_miss) / 1_000_000
    else:
        input_cost = input_tokens * up_in / 1_000_000

    output_cost = output_tokens * up_out / 1_000_000
    cost = round(input_cost + output_cost, 6)
    return cost, pid, up_in, up_out, up_cache_hit, up_cache_miss


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
    job_id: Optional[str] = None,
    request_id: Optional[str] = None,
    trace_id: str = "",
    parent_trace_id: str = "",
    sub_stage: str = "",
    prompt_name: str = "default",
    prompt_version: str = "1.0",
    schema_version: str = "1.0",
    input_tokens: int = 0,
    output_tokens: int = 0,
    image_count: int = 0,
    image_total_bytes: int = 0,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
    estimated_cost: float = 0.0,
    credit_cost: int = 0,
    billing_status: str = "billed",
    call_source: Optional[str] = os.environ.get("YOMICALL_SOURCE", "prod"),
    error_code: Optional[str] = None,
    retry_count: int = 0,
    pricing: Optional[dict] = None,
    **extra: Any,
) -> dict:
    """
    Returns a dict matching model_call_log schema (Table 12).
    Extra kwargs become additional fields (e.g. blocks_count for OCR).
    If pricing is provided, computes cost_cny from tokens × prices.
    """
    cost_cny, pricing_snapshot_id, up_in, up_out, up_cache_hit, up_cache_miss = _compute_cost_cny(
        input_tokens, output_tokens, pricing, image_count,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
    )

    base = {
        "id": uuid.uuid4().hex,
        "request_id": request_id,
        "task_id": task_id,
        "trace_id": trace_id,
        "parent_trace_id": parent_trace_id,
        "sub_stage": sub_stage,
        "job_id": job_id or task_id,
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
        "image_count": image_count,
        "image_total_bytes": image_total_bytes,
        "cache_hit_tokens": cache_hit_tokens,
        "cache_miss_tokens": cache_miss_tokens,
        "latency_ms": latency_ms,
        "raw_cost": estimated_cost,
        "cost_cny": cost_cny if cost_cny > 0 else float(credit_cost or estimated_cost),
        "unit_price_input": up_in,
        "unit_price_output": up_out,
        "unit_price_cache_hit": up_cache_hit,
        "unit_price_cache_miss": up_cache_miss,
        "currency": "CNY",
        "pricing_snapshot_id": pricing_snapshot_id,
        "credit_cost": credit_cost,
        "billing_status": billing_status,
        "call_source": call_source,
        "success": success,
        "status": "success" if success else "failed",
        "error_code": error_code,
        "retry_count": retry_count,
    }
    base.update(extra)
    return base
