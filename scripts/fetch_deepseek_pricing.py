#!/usr/bin/env python3
"""
DeepSeek 官方实时单价抓取（含缓存命中/未命中）
来源: https://api-docs.deepseek.com/quick_start/pricing
输出: JSON — 所有模型 × 所有价格维度（输入/输出/缓存命中/缓存未命中），CNY
用法: python3 fetch_deepseek_pricing.py
环境变量: FETCH_DS_USDCNY=7.30  覆盖汇率
"""

import json, re, os, sys
from datetime import date
from urllib.request import urlopen, Request

DS_PRICING_URL = "https://api-docs.deepseek.com/quick_start/pricing"


def extract_first_usd(text):
    """从文本提取第一个 $X.XX 金额。"""
    m = re.search(r'\$([0-9.]+)', text)
    return float(m.group(1)) if m else None


def clean_model_name(raw):
    """清理模型名：去脚注标记如 (1)(2)(3)。"""
    return re.sub(r'\([0-9]+\)', '', raw).strip()


def main():
    usd_cny = float(os.environ.get("FETCH_DS_USDCNY", "7.25"))

    req = Request(DS_PRICING_URL, headers={"User-Agent": "Youmi-PricingFetcher/1.0"})
    html = urlopen(req, timeout=15).read().decode()

    # 找到 MODEL 行开始的价格表区域
    idx = html.find(">MODEL<")
    if idx < 0:
        print(json.dumps({"error": "MODEL marker not found"}, ensure_ascii=False))
        sys.exit(1)

    section = html[idx - 200:idx + 2500]
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", section, re.DOTALL)

    def get_cells(row_html):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.DOTALL)
        return [re.sub(r"<[^>]+>", "", c).strip() for c in cells]

    # Row 0: MODEL header
    header_cells = get_cells(rows[0])
    model_names = [clean_model_name(n) for n in header_cells[1:]]

    # Row 11: PRICING + cache hit
    ch_cells = get_cells(rows[11])
    # Row 12: cache miss
    cm_cells = get_cells(rows[12])
    # Row 13: output
    out_cells = get_cells(rows[13])

    # PRICING 行结构: ["PRICING", "1M INPUT TOKENS (CACHE HIT)(2)", "$0.0028", "$0.003625 ..."]
    # cache miss 行:   ["1M INPUT TOKENS (CACHE MISS)", "$0.14", ...]
    # output 行:       ["1M OUTPUT TOKENS", "$0.28", ...]
    # 模型价格从 index 2 开始 (ch) 或 index 1 开始 (cm, out)

    result = {
        "source_url": DS_PRICING_URL,
        "fetch_date": date.today().isoformat(),
        "usd_cny_rate": usd_cny,
        "models": {},
    }

    for i, name in enumerate(model_names):
        if not name:
            continue

        ch_usd = extract_first_usd(ch_cells[2 + i]) if len(ch_cells) > 2 + i else None
        cm_usd = extract_first_usd(cm_cells[1 + i]) if len(cm_cells) > 1 + i else None
        out_usd = extract_first_usd(out_cells[1 + i]) if len(out_cells) > 1 + i else None

        if cm_usd is None or out_usd is None:
            continue

        entry = {
            "cache_hit_usd": ch_usd,
            "cache_hit_cny": round(ch_usd * usd_cny, 4) if ch_usd is not None else None,
            "cache_miss_usd": cm_usd,
            "cache_miss_cny": round(cm_usd * usd_cny, 4),
            "output_usd": out_usd,
            "output_cny": round(out_usd * usd_cny, 4),
        }
        result["models"][name] = entry

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
