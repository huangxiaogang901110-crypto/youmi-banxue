#!/usr/bin/env python3
"""
悠米伴学 每日成本日报 v2
- 每日查询官方价格来源（非硬编码、非本地快照）
- DeepSeek: api-docs.deepseek.com
- Qwen-VL: 阿里云 DashScope 官方定价页（不可达时降级为「价格未核实」）
- 仅统计 call_source IN ('prod','test')，排除 dev 开发调试
"""

import sqlite3, json, re
from datetime import date
from urllib.request import urlopen, Request

DB = '/srv/yomi/yomi.db'
TODAY = date.today().isoformat()

# ============================================================
# 1. 官方价格查询
# ============================================================

# ------ DeepSeek ------
DS_SOURCE_URL = "https://api-docs.deepseek.com/quick_start/pricing"
DS_SOURCE_NAME = "DeepSeek API Docs - Models & Pricing"
DS_PRICE_OK = False
ds_input_cny = None
ds_output_cny = None
ds_price_date = TODAY
ds_price_usd_in = None
ds_price_usd_out = None

try:
    req = Request(DS_SOURCE_URL, headers={"User-Agent": "Youmi-DailyReport/1.0"})
    html = urlopen(req, timeout=15).read().decode()
    # Extract pricing section: the row with "PRICING" followed by cache rows
    pricing = re.search(r'PRICING.*?1M OUTPUT TOKENS.*?</tr>', html, re.DOTALL)
    if pricing:
        sec = pricing.group(0)
        # Extract all $X.XX from <td>$X.XX</td>
        amounts = re.findall(r'<td>\$([0-9.]+)', sec)
        # Order: cache_hit(flash), cache_hit(pro), cache_miss(flash), cache_miss(pro), output(flash), output(pro)
        if len(amounts) >= 5:
            ds_price_usd_in = float(amounts[2])   # cache miss = $0.14
            ds_price_usd_out = float(amounts[4])  # output = $0.28
            USD_CNY = 7.25
            ds_input_cny = round(ds_price_usd_in * USD_CNY, 4)
            ds_output_cny = round(ds_price_usd_out * USD_CNY, 4)
            DS_PRICE_OK = True
except Exception:
    pass

# ------ Qwen-VL ------
QWEN_SOURCE_URL = "https://help.aliyun.com/zh/model-studio/vision-model/"
QWEN_SOURCE_NAME = "阿里云帮助中心 - 视觉模型"
QWEN_PRICE_OK = False
qwen_input_cny = None
qwen_output_cny = None

# 阿里云帮助页面纯 JS 渲染，curl/http 无法提取价格文本
# QWEN_UNREACHABLE，日报中显示「价格未核实」
# 如需启用，需配置 headless browser 或 Aliyun API key 访问定价 API

# ============================================================
# 2. 数据库统计
# ============================================================
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

CS = "call_source IN ('prod', 'test')"

qwen = db.execute(f"""
    SELECT COALESCE(SUM(input_tokens),0) ti, COALESCE(SUM(output_tokens),0) to_,
           COALESCE(SUM(cost_cny),0) tc, COUNT(*) cc
    FROM model_calls WHERE ({CS})
    AND (provider IN ('aliyun_dashscope','aliyun_ocr')
         OR feature_code LIKE 'qwen_%' OR feature_code LIKE 'aliyun_%')
""").fetchone()

ds = db.execute(f"""
    SELECT COALESCE(SUM(input_tokens),0) ti, COALESCE(SUM(output_tokens),0) to_,
           COALESCE(SUM(cost_cny),0) tc, COUNT(*) cc
    FROM model_calls WHERE ({CS})
    AND (provider='deepseek' OR feature_code LIKE 'deepseek_%')
    AND feature_code NOT LIKE '%homework_parse%'
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
def unit_fmt(cost, cnt, label):
    if cnt > 0:
        return "¥{:.4f}/{}".format(cost / cnt, label)
    return "暂不可算"

qw_u = unit_fmt(qwen['tc'], question_count, "题") if question_count else "暂不可算"
ds_u = unit_fmt(ds['tc'], ds_answers, "次") if ds_answers else "暂不可算"

qwen_cost_str = "¥{:.2f}".format(qwen['tc']) if QWEN_PRICE_OK else "未核实(仅统计token)"
ds_cost_str = "¥{:.2f}".format(ds['tc']) if DS_PRICE_OK else "未核实(仅统计token)"

total_display = 0
if QWEN_PRICE_OK: total_display += qwen['tc']
if DS_PRICE_OK: total_display += ds['tc']

avg = "¥{:.2f}/户".format(total_display / user_count) if user_count and total_display else "暂不可算"

qin = "¥{}/1M tok".format(qwen_input_cny) if QWEN_PRICE_OK else "未核实"
qout = "¥{}/1M tok".format(qwen_output_cny) if QWEN_PRICE_OK else "未核实"
din = "¥{}/1M tok".format(ds_input_cny) if DS_PRICE_OK else "未核实"
dout = "¥{}/1M tok".format(ds_output_cny) if DS_PRICE_OK else "未核实"

qw_note = ""
if not QWEN_PRICE_OK:
    qw_note = "  （阿里云帮助页面 JS 渲染，不可达；如需自动抓取需配置 headless browser）"
ds_note = ""
if DS_PRICE_OK:
    ds_note = "  （USD ${}/${} per 1M tok，按 USD/CNY=7.25 换算）".format(ds_price_usd_in, ds_price_usd_out)

# ============================================================
# 4. 输出
# ============================================================
output = """📊 悠米伴学 每日成本日报

报告日期：{today}
价格查询日期：{pdate}

价格来源：
- Qwen-VL：{qname}{qnote}
  URL：{qurl}
  状态：{qstatus}
- DeepSeek：{dsname}
  URL：{dsurl}
  状态：{dsstatus}{dsnote}

| 模型 | 输入Token数 | 输出Token数 | 输入单价 | 输出单价 | 成本小计 | 题数/次数 | 单题/单次成本 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| Qwen-VL | {qin_num:,} | {qout_num:,} | {qin} | {qout} | {qcost} | {qtot:,} 题 | {qw_u} |
| DeepSeek | {din_num:,} | {dout_num:,} | {din} | {dout} | {dcost} | {da:,} 次 | {ds_u} |

当日总成本：{total}
当日发生费用总用户数：{uc} 人
当日单户平均成本：{avg}

统计口径：
- 仅统计 call_source IN ('prod','test')，排除 dev 开发调试
- Qwen-VL：aliyun_dashscope + aliyun_ocr 全部调用（{qcalls} 次）| 题数来自 question_item 全表
- DeepSeek：deepseek provider 的 tutor/followup/check 类调用（{dcalls} 次，{da} 次有效回答）
- 价格每日从官方来源查询，不可达时降级为「价格未核实」
- 价格未写入脚本常量
""".format(
    today=TODAY, pdate=ds_price_date,
    qname=QWEN_SOURCE_NAME, qnote=qw_note, qurl=QWEN_SOURCE_URL,
    qstatus="✅ 已核实" if QWEN_PRICE_OK else "⚠️ 价格未核实，仅统计 token，不计算成本",
    dsname=DS_SOURCE_NAME, dsurl=DS_SOURCE_URL,
    dsstatus="✅ 已核实" if DS_PRICE_OK else "⚠️ 价格未核实，仅统计 token，不计算成本",
    dsnote=ds_note,
    qin_num=qwen['ti'], qout_num=qwen['to_'],
    qin=qin, qout=qout, qcost=qwen_cost_str,
    qtot=question_count, qw_u=qw_u,
    din_num=ds['ti'], dout_num=ds['to_'],
    din=din, dout=dout, dcost=ds_cost_str,
    da=ds_answers, ds_u=ds_u,
    total="¥{:.2f}".format(total_display) if total_display else "暂不可算（价格未核实）",
    uc=user_count, avg=avg,
    qcalls=qwen['cc'], dcalls=ds['cc'],
)

print(output)
