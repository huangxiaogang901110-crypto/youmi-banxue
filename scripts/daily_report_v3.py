#!/usr/bin/env python3
"""
悠米伴学 每日成本日报 v3 — 缓存命中/未命中拆账版
- DeepSeek: api-docs.deepseek.com 实时价格
- Qwen-VL: DashScope API 实时价格
- 仅统计 call_source IN ('prod','test')
"""

import sqlite3, json, re, os, subprocess, sys
from datetime import date
from urllib.request import urlopen, Request

DB = '/srv/yomi/yomi.db'
TODAY = date.today().isoformat()
SCRIPTS = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 1. 实时价格查询
# ============================================================

def fetch_deepseek_pricing():
    """调用 fetch_deepseek_pricing.py 获取最近模型价格。"""
    try:
        r = subprocess.run(
            [sys.executable, f"{SCRIPTS}/fetch_deepseek_pricing.py"],
            capture_output=True, text=True, timeout=20
        )
        data = json.loads(r.stdout)
        # 取第一个模型（通常 deepseek-v4-flash）
        models = data.get("models", {})
        if models:
            name = list(models.keys())[0]
            m = models[name]
            return {
                "ok": True,
                "model": name,
                "input": m["cache_miss_cny"],       # 缓存未命中=标准输入价
                "output": m["output_cny"],
                "cache_hit": m.get("cache_hit_cny"),
                "cache_miss": m["cache_miss_cny"],
                "source": data["source_url"],
                "usd_cny": data["usd_cny_rate"],
            }
    except Exception as e:
        pass
    return {"ok": False}

def fetch_qwen_pricing():
    """调用 fetch_qwen_pricing.py 获取 qwen-vl-max 价格。"""
    try:
        r = subprocess.run(
            [sys.executable, f"{SCRIPTS}/fetch_qwen_pricing.py"],
            capture_output=True, text=True, timeout=20
        )
        data = json.loads(r.stdout)
        p = data.get("pricing", {})
        if p:
            return {
                "ok": True,
                "model": data.get("model_id", "qwen-vl-max"),
                "input": p["cache_miss"],
                "output": p["output"],
                "cache_hit": p.get("cache_hit"),
                "cache_miss": p["cache_miss"],
                "source": data["source_url"],
            }
    except Exception:
        pass
    return {"ok": False}

ds_p = fetch_deepseek_pricing()
qw_p = fetch_qwen_pricing()

# ============================================================
# 2. 数据库统计（含缓存拆账）
# ============================================================

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
CS = "call_source IN ('prod', 'test')"

# DeepSeek
ds = db.execute(f"""
    SELECT 
        COALESCE(SUM(input_tokens),0) as ti,
        COALESCE(SUM(output_tokens),0) as to_,
        COALESCE(SUM(cache_hit_tokens),0) as ch,
        COALESCE(SUM(cache_miss_tokens),0) as cm,
        COALESCE(SUM(cost_cny),0) as tc,
        COUNT(*) as cc
    FROM model_calls WHERE ({CS})
    AND (provider='deepseek' OR feature_code LIKE 'deepseek_%')
    AND feature_code NOT LIKE '%homework_parse%'
""").fetchone()

# Qwen-VL
qwen = db.execute(f"""
    SELECT 
        COALESCE(SUM(input_tokens),0) as ti,
        COALESCE(SUM(output_tokens),0) as to_,
        COALESCE(SUM(cache_hit_tokens),0) as ch,
        COALESCE(SUM(cache_miss_tokens),0) as cm,
        COALESCE(SUM(cost_cny),0) as tc,
        COUNT(*) as cc
    FROM model_calls WHERE ({CS})
    AND (provider IN ('aliyun_dashscope','aliyun_ocr')
         OR feature_code LIKE 'qwen_%' OR feature_code LIKE 'aliyun_%')
""").fetchone()

question_count = db.execute("SELECT COUNT(*) FROM question_item").fetchone()[0]

ds_answers = db.execute(f"""
    SELECT COUNT(*) FROM model_calls WHERE ({CS})
    AND (provider='deepseek' OR feature_code LIKE 'deepseek_%')
    AND feature_code IN ('deepseek_tutor_initial','deepseek_tutor_followup','deepseek_check_attempt')
""").fetchone()[0]

uids = set()
for row in db.execute(f"SELECT data FROM model_calls WHERE ({CS}) AND data IS NOT NULL"):
    try:
        pid = json.loads(row['data']).get('parent_user_id')
        if pid: uids.add(pid)
    except: pass
user_count = len(uids)
db.close()

# ============================================================
# 3. 计算
# ============================================================

# 当缓存拆账为 0 但有 input_tokens 时，视为全部未命中
if qwen['ch'] == 0 and qwen['cm'] == 0 and qwen['ti'] > 0:
    qwen_display = {"ch": 0, "cm": qwen['ti'], "to_": qwen['to_']}
else:
    qwen_display = {"ch": qwen['ch'], "cm": qwen['cm'], "to_": qwen['to_']}

if ds['ch'] == 0 and ds['cm'] == 0 and ds['ti'] > 0:
    ds_display = {"ch": 0, "cm": ds['ti'], "to_": ds['to_']}
else:
    ds_display = {"ch": ds['ch'], "cm": ds['cm'], "to_": ds['to_']}

# Qwen 成本：优先用库内 cost_cny，价格不可达时用 token×单价自己算
if qw_p["ok"]:
    qwen_input_price = f"¥{qw_p['input']}/1M"
    qwen_output_price = f"¥{qw_p['output']}/1M"
    qwen_ch_price = f"¥{qw_p['cache_hit']}/1M" if qw_p['cache_hit'] else "—"
    qwen_cm_price = f"¥{qw_p['cache_miss']}/1M"
    if qwen['tc'] == 0 and qwen['ti'] > 0:
        # 库内无成本，自己算
        if qwen['ch'] > 0 or qwen['cm'] > 0:
            cost = (qwen['ch'] * qw_p['cache_hit'] + qwen['cm'] * qw_p['cache_miss']) / 1_000_000
        else:
            cost = qwen['ti'] * qw_p['input'] / 1_000_000
        cost += qwen['to_'] * qw_p['output'] / 1_000_000
        qwen_cost_str = f"¥{cost:.4f}"
        qwen_cost_num = cost
    else:
        qwen_cost_str = f"¥{qwen['tc']:.4f}"
        qwen_cost_num = qwen['tc']
    qwen_status = "✅"
else:
    qwen_input_price = "⚠️ 不可达"
    qwen_output_price = "⚠️"
    qwen_ch_price = "⚠️"
    qwen_cm_price = "⚠️"
    qwen_cost_str = "未核实"
    qwen_cost_num = 0
    qwen_status = "⚠️"

# DeepSeek 成本
if ds_p["ok"]:
    ds_input_price = f"¥{ds_p['input']}/1M"
    ds_output_price = f"¥{ds_p['output']}/1M"
    ds_ch_price = f"¥{ds_p['cache_hit']}/1M" if ds_p['cache_hit'] else "—"
    ds_cm_price = f"¥{ds_p['cache_miss']}/1M"
    if ds['tc'] == 0 and ds['ti'] > 0:
        if ds['ch'] > 0 or ds['cm'] > 0:
            cost = (ds['ch'] * ds_p['cache_hit'] + ds['cm'] * ds_p['cache_miss']) / 1_000_000
        else:
            cost = ds['ti'] * ds_p['input'] / 1_000_000
        cost += ds['to_'] * ds_p['output'] / 1_000_000
        ds_cost_str = f"¥{cost:.4f}"
        ds_cost_num = cost
    else:
        ds_cost_str = f"¥{ds['tc']:.4f}"
        ds_cost_num = ds['tc']
    ds_status = "✅"
else:
    ds_input_price = "⚠️ 不可达"
    ds_output_price = "⚠️"
    ds_ch_price = "⚠️"
    ds_cm_price = "⚠️"
    ds_cost_str = "未核实"
    ds_cost_num = 0
    ds_status = "⚠️"

total = 0
if qw_p["ok"]: total += qwen_cost_num
if ds_p["ok"]: total += ds_cost_num

def unit_fmt(cost, cnt, label):
    if cnt > 0: return "¥{:.4f}/{}".format(cost / cnt, label)
    return "—"

qw_u = unit_fmt(qwen_cost_num, question_count, "题") if question_count else "—"
ds_u = unit_fmt(ds_cost_num, ds_answers, "次") if ds_answers else "—"

avg = "¥{:.2f}/户".format(total / user_count) if user_count and total else "—"

# ============================================================
# 4. 输出
# ============================================================

# DS model name
ds_model = ds_p.get("model", "deepseek-chat") if ds_p["ok"] else "deepseek-chat"

output = f"""📊 悠米伴学 每日成本日报 v3（缓存拆账）

报告日期：{TODAY}
价格来源：
- DeepSeek：{ds_p.get('source','api-docs.deepseek.com') if ds_p['ok'] else '⚠️ 不可达'}  {ds_status}
  模型：{ds_model} | USD/CNY={ds_p.get('usd_cny','—') if ds_p['ok'] else '—'}
- Qwen-VL：{qw_p.get('source','DashScope API') if qw_p['ok'] else '⚠️ 不可达'}  {qwen_status}
  模型：qwen-vl-max | 原生 CNY

| 模型 | 缓存命中 | 缓存未命中 | 输出Token | 缓存命中单价 | 缓存未命中单价 | 输出单价 | 成本小计 | 题数/次数 | 单题/单次 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Qwen-VL | {qwen_display['ch']:,} | {qwen_display['cm']:,} | {qwen_display['to_']:,} | {qwen_ch_price} | {qwen_cm_price} | {qwen_output_price} | {qwen_cost_str} | {question_count:,} 题 | {qw_u} |
| DeepSeek | {ds_display['ch']:,} | {ds_display['cm']:,} | {ds_display['to_']:,} | {ds_ch_price} | {ds_cm_price} | {ds_output_price} | {ds_cost_str} | {ds_answers:,} 次 | {ds_u} |

当日总成本：{"¥{:.4f}".format(total) if total else "暂不可算"}
当日用户数：{user_count} 人 | 单户平均：{avg}

口径：call_source IN ('prod','test')
Qwen: {qwen_display['ch']+qwen_display['cm']:,} 总输入 + {qwen_display['to_']:,} 输出，{qwen['cc']} 次调用
DeepSeek: {ds_display['ch']+ds_display['cm']:,} 总输入 + {ds_display['to_']:,} 输出，{ds['cc']} 次调用，{ds_answers} 次有效回答
"""

print(output)
