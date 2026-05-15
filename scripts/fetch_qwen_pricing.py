#!/usr/bin/env python3
"""
DashScope qwen-vl-max 实时单价
来源: https://dashscope.aliyuncs.com/api/v1/models
用法: python3 fetch_qwen_pricing.py
"""

import json, os, re, sys
from datetime import date
from urllib.request import urlopen, Request

DASHSCOPE_API = "https://dashscope.aliyuncs.com/api/v1/models"


def get_api_key():
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if key:
        return key
    for fp in ["/home/hermes_me/yomi/backend/.env",
               os.path.expanduser("~/yomi/.env"),
               os.path.expanduser("~/yomi/backend/.env")]:
        try:
            with open(fp) as f:
                for line in f:
                    if "QWEN_DASHSCOPE_API_KEY" in line:
                        key = re.sub(r'.*?=\s*["\']?([^"\'\s]+)["\']?\s*', r'\1', line.strip())
                        if key and len(key) > 10:
                            return key
        except Exception:
            pass
    return ""


def find_model(api_key, model_id, max_pages=5):
    """分页查找指定模型。"""
    for page in range(1, max_pages + 1):
        url = f"{DASHSCOPE_API}?page_no={page}&page_size=100"
        req = Request(url, headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        })
        resp = urlopen(req, timeout=30)
        data = json.loads(resp.read())
        models = data.get("output", {}).get("models", [])
        for m in models:
            if m.get("model") == model_id:
                return m
        total = data.get("output", {}).get("total", 0)
        if page * 100 >= total:
            break
    return None


def parse_pricing(prices_list):
    """解析模型定价，处理 input_token_cache / input_token_cache_read 两种缓存命中字段。"""
    if not prices_list:
        return None
    tier = prices_list[0]
    items = tier.get("prices", [])
    p = {}
    for item in items:
        t = item.get("type", "")
        raw = item.get("price", "0")
        try:
            val = float(raw) if raw else 0.0
        except (ValueError, TypeError):
            val = 0.0
        if t == "input_token":
            p["input"] = val
        elif t == "output_token":
            p["output"] = val
        elif t in ("input_token_cache", "input_token_cache_read"):
            p["cache_hit"] = val
        elif t == "input_token_batch":
            p["batch_input"] = val
        elif t == "output_token_batch":
            p["batch_output"] = val
    if "input" in p:
        p["cache_miss"] = p["input"]
    return p if p.get("input") and p.get("output") else None


def main():
    api_key = get_api_key()
    if not api_key:
        print(json.dumps({"error": "API key not found"}, ensure_ascii=False))
        sys.exit(1)

    model = find_model(api_key, "qwen-vl-max")
    if not model:
        print(json.dumps({"error": "qwen-vl-max not found"}, ensure_ascii=False))
        sys.exit(1)

    pricing = parse_pricing(model.get("prices", []))

    result = {
        "source_url": DASHSCOPE_API,
        "fetch_date": date.today().isoformat(),
        "model_id": "qwen-vl-max",
        "model_name": model.get("name", "Qwen-VL-Max"),
        "currency": "CNY",
        "price_unit": "每百万tokens",
        "pricing": pricing,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
