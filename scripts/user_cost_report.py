#!/usr/bin/env python3
"""
用户侧成本汇总表 — 按时间段核算每个用户的成本明细
用法:
  python3 scripts/user_cost_report.py --start 2026-05-13 --end 2026-05-20
  python3 scripts/user_cost_report.py --start 2026-05-13 --end 2026-05-20 --json

口径:
  - 时间: 半开区间 [start, end)
  - call_source: IN ('prod', 'test')
  - Qwen 成本: provider IN ('aliyun_dashscope','aliyun_ocr') OR feature_code LIKE 'qwen_vl_%'
  - DeepSeek 成本: provider='deepseek' OR feature_code LIKE 'deepseek_%'
  - 用户归属: json_extract(data, '$.parent_user_id')
"""

import sqlite3
import json
import sys
import argparse
from datetime import datetime

DB = '/srv/yomi/yomi.db'
CS = "call_source IN ('prod', 'test')"


def mask_phone(phone: str) -> str:
    """手机号脱敏: 138****8000"""
    if not phone or len(phone) < 7:
        return phone or ''
    return phone[:3] + '****' + phone[-4:]


def parse_args():
    parser = argparse.ArgumentParser(description='用户侧成本汇总表')
    parser.add_argument('--start', required=True, help='起始日期（含）YYYY-MM-DD')
    parser.add_argument('--end', required=True, help='截止日期（不含）YYYY-MM-DD')
    parser.add_argument('--json', action='store_true', help='JSON 输出')
    return parser.parse_args()


def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def build_time_clause(alias: str, start: str, end: str) -> str:
    """半开区间: >= start AND < end"""
    col = f"{alias}.created_at" if alias else "created_at"
    return f"{col} >= '{start}' AND {col} < '{end}'"


# ─── 用户基础信息 ─────────────────────────────────────────────

def fetch_users(conn):
    """所有 active 用户 + 孩子名合并 + 学豆余额"""
    sql = """
    SELECT 
        pu.id AS parent_user_id,
        pu.name AS parent_name,
        GROUP_CONCAT(cp.name, ' / ') AS child_names,
        pu.phone,
        pu.created_at AS registered_at,
        COALESCE(ca.balance, 50) AS credit_balance
    FROM parent_users pu
    LEFT JOIN child_profiles cp ON cp.parent_id = pu.id
    LEFT JOIN credit_account ca ON ca.parent_user_id = pu.id
    WHERE pu.status = 'active'
    GROUP BY pu.id
    ORDER BY pu.id
    """
    return {r['parent_user_id']: dict(r) for r in conn.execute(sql).fetchall()}


# ─── 用量统计 ─────────────────────────────────────────────────

def fetch_uploaded_parse_jobs(conn, start, end):
    """上传/解析任务数（近似图片数）"""
    tc = build_time_clause('', start, end)
    sql = f"""
    SELECT parent_id, COUNT(*) AS cnt
    FROM parse_jobs
    WHERE parent_id IS NOT NULL AND parent_id != ''
      AND {tc}
    GROUP BY parent_id
    """
    return {r['parent_id']: r['cnt'] for r in conn.execute(sql).fetchall()}


def fetch_qwen_question_count(conn, start, end):
    """Qwen 识别题目数"""
    tc = build_time_clause('', start, end)
    sql = f"""
    SELECT parent_id, COALESCE(SUM(questions_count), 0) AS cnt
    FROM parse_jobs
    WHERE parser_provider = 'aliyun_dashscope'
      AND status = 'completed'
      AND {tc}
    GROUP BY parent_id
    """
    return {r['parent_id']: r['cnt'] for r in conn.execute(sql).fetchall()}


def fetch_ds_counts(conn, start, end):
    """DeepSeek 解析次数 + 回答次数"""
    tc = build_time_clause('', start, end)
    sql = f"""
    SELECT 
        json_extract(data, '$.parent_user_id') AS puid,
        SUM(CASE WHEN feature_code = 'deepseek_tutor_initial' THEN 1 ELSE 0 END) AS initial_cnt,
        SUM(CASE WHEN feature_code = 'deepseek_tutor_followup' THEN 1 ELSE 0 END) AS followup_cnt
    FROM model_calls
    WHERE provider = 'deepseek'
      AND {CS}
      AND {tc}
      AND json_extract(data, '$.parent_user_id') IS NOT NULL
      AND json_extract(data, '$.parent_user_id') != ''
    GROUP BY puid
    """
    result = {}
    for r in conn.execute(sql).fetchall():
        result[r['puid']] = (r['initial_cnt'], r['followup_cnt'])
    return result


# ─── 成本统计 ─────────────────────────────────────────────────

def fetch_qwen_cost(conn, start, end):
    """Qwen 成本 — 与 App 整体侧一致口径"""
    tc = build_time_clause('', start, end)
    sql = f"""
    SELECT 
        json_extract(data, '$.parent_user_id') AS puid,
        COALESCE(SUM(cost_cny), 0) AS cost
    FROM model_calls
    WHERE (provider IN ('aliyun_dashscope', 'aliyun_ocr') OR feature_code LIKE 'qwen_vl_%')
      AND {CS}
      AND {tc}
    GROUP BY puid
    """
    return {r['puid']: r['cost'] for r in conn.execute(sql).fetchall()}


def fetch_deepseek_cost(conn, start, end):
    """DeepSeek 成本 — 全部 feature_code"""
    tc = build_time_clause('', start, end)
    sql = f"""
    SELECT 
        json_extract(data, '$.parent_user_id') AS puid,
        COALESCE(SUM(cost_cny), 0) AS cost
    FROM model_calls
    WHERE (provider = 'deepseek' OR feature_code LIKE 'deepseek_%')
      AND {CS}
      AND {tc}
    GROUP BY puid
    """
    return {r['puid']: r['cost'] for r in conn.execute(sql).fetchall()}


# ─── App 整体侧成本 ───────────────────────────────────────────

def fetch_app_cost(conn, start, end):
    """App 整体侧成本 — 同一半开区间、同一口径"""
    tc = build_time_clause('', start, end)
    sql_qwen = f"""
    SELECT COALESCE(SUM(cost_cny), 0) AS cost
    FROM model_calls
    WHERE (provider IN ('aliyun_dashscope', 'aliyun_ocr') OR feature_code LIKE 'qwen_vl_%')
      AND {CS}
      AND {tc}
    """
    sql_ds = f"""
    SELECT COALESCE(SUM(cost_cny), 0) AS cost
    FROM model_calls
    WHERE (provider = 'deepseek' OR feature_code LIKE 'deepseek_%')
      AND {CS}
      AND {tc}
    """
    qwen = conn.execute(sql_qwen).fetchone()['cost']
    ds = conn.execute(sql_ds).fetchone()['cost']
    return qwen, ds, qwen + ds


def fetch_unassigned_cost(conn, start, end, known_user_ids):
    """未归属成本：parent_user_id 为空或不在 parent_users 中"""
    tc = build_time_clause('', start, end)
    uid_list = ','.join(f"'{u}'" for u in known_user_ids) if known_user_ids else "''"

    sql_qwen = f"""
    SELECT COALESCE(SUM(cost_cny), 0) AS cost
    FROM model_calls
    WHERE (provider IN ('aliyun_dashscope', 'aliyun_ocr') OR feature_code LIKE 'qwen_vl_%')
      AND {CS}
      AND {tc}
      AND (
          json_extract(data, '$.parent_user_id') IS NULL
          OR json_extract(data, '$.parent_user_id') = ''
          OR json_extract(data, '$.parent_user_id') NOT IN ({uid_list})
      )
    """
    sql_ds = f"""
    SELECT COALESCE(SUM(cost_cny), 0) AS cost
    FROM model_calls
    WHERE (provider = 'deepseek' OR feature_code LIKE 'deepseek_%')
      AND {CS}
      AND {tc}
      AND (
          json_extract(data, '$.parent_user_id') IS NULL
          OR json_extract(data, '$.parent_user_id') = ''
          OR json_extract(data, '$.parent_user_id') NOT IN ({uid_list})
      )
    """
    un_qwen = conn.execute(sql_qwen).fetchone()['cost']
    un_ds = conn.execute(sql_ds).fetchone()['cost']
    return un_qwen, un_ds, un_qwen + un_ds


# ─── 对账 ─────────────────────────────────────────────────────

def reconcile(app_qwen, app_ds, app_total,
              user_qwen, user_ds, user_total,
              un_qwen, un_ds, un_total):
    qwen_diff = round(app_qwen - user_qwen - un_qwen, 4)
    ds_diff = round(app_ds - user_ds - un_ds, 4)
    final_diff = round(app_total - user_total - un_total, 4)

    reasons = []
    if un_total > 0:
        reasons.append("unassigned_user_cost")
    if abs(qwen_diff) <= 0.01 and abs(ds_diff) <= 0.01 and abs(final_diff) <= 0.01:
        if abs(final_diff) > 0:
            reasons.append("rounding_diff")
    else:
        if abs(qwen_diff) > 0.01:
            reasons.append(f"qwen_diff={qwen_diff}")
        if abs(ds_diff) > 0.01:
            reasons.append(f"deepseek_diff={ds_diff}")
        if abs(final_diff) > 0.01:
            reasons.append("unknown_diff")

    return {
        "app_qwen_cost_cny": round(app_qwen, 4),
        "user_qwen_cost_cny": round(user_qwen, 4),
        "unassigned_qwen_cost_cny": round(un_qwen, 4),
        "qwen_diff_cny": qwen_diff,
        "app_deepseek_cost_cny": round(app_ds, 4),
        "user_deepseek_cost_cny": round(user_ds, 4),
        "unassigned_deepseek_cost_cny": round(un_ds, 4),
        "deepseek_diff_cny": ds_diff,
        "app_total_cost_cny": round(app_total, 4),
        "user_total_cost_cny": round(user_total, 4),
        "unassigned_total_cost_cny": round(un_total, 4),
        "final_diff_cny": final_diff,
        "diff_reason": reasons if reasons else ["ok"]
    }


# ─── 主流程 ───────────────────────────────────────────────────

def main():
    args = parse_args()
    start = args.start
    end = args.end

    conn = get_conn()

    # 1. 用户基础信息
    users = fetch_users(conn)
    known_ids = list(users.keys())

    # 2. 用量
    img_map = fetch_uploaded_parse_jobs(conn, start, end)
    qwen_q_map = fetch_qwen_question_count(conn, start, end)
    ds_counts = fetch_ds_counts(conn, start, end)

    # 3. 成本（按用户）
    qwen_cost_map = fetch_qwen_cost(conn, start, end)
    ds_cost_map = fetch_deepseek_cost(conn, start, end)

    # 4. App 整体侧成本
    app_qwen, app_ds, app_total = fetch_app_cost(conn, start, end)

    # 5. 未归属成本
    un_qwen, un_ds, un_total = fetch_unassigned_cost(conn, start, end, known_ids)

    # 6. 组装每用户行
    rows = []
    for uid, u in users.items():
        initial_cnt, followup_cnt = ds_counts.get(uid, (0, 0))
        qcost = qwen_cost_map.get(uid, 0) or 0
        dcost = ds_cost_map.get(uid, 0) or 0

        row = {
            "parent_user_id": uid,
            "parent_name": u['parent_name'],
            "child_names": u['child_names'] or '',
            "phone": u['phone'],
            "registered_at": u['registered_at'],
            "credit_balance": u['credit_balance'],
            "uploaded_parse_job_count": img_map.get(uid, 0),
            "qwen_question_count": qwen_q_map.get(uid, 0),
            "deepseek_tutor_initial_count": initial_cnt,
            "deepseek_tutor_followup_count": followup_cnt,
            "qwen_cost_cny": round(qcost, 4),
            "deepseek_cost_cny": round(dcost, 4),
            "total_cost_cny": round(qcost + dcost, 4),
        }
        rows.append(row)

    # 7. 汇总
    user_qwen = round(sum(r['qwen_cost_cny'] for r in rows), 4)
    user_ds = round(sum(r['deepseek_cost_cny'] for r in rows), 4)
    user_total = round(sum(r['total_cost_cny'] for r in rows), 4)

    # 8. 对账
    rec = reconcile(app_qwen, app_ds, app_total,
                    user_qwen, user_ds, user_total,
                    un_qwen, un_ds, un_total)

    # 9. 输出
    if args.json:
        output = {
            "period": {"start": start, "end": end},
            "rows": rows,
            "summary": {
                "user_count": len(rows),
                "qwen_cost_cny": user_qwen,
                "deepseek_cost_cny": user_ds,
                "total_cost_cny": user_total,
            },
            "reconciliation": rec,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        # 文本表格
        print(f"统计周期：{start} <= created_at < {end}")
        print()
        header = f"{'用户':10s} {'孩子':12s} {'手机号':14s} {'注册时间':20s} {'学豆':5s} {'任务数':5s} {'Qwen题':6s} {'DS解析':5s} {'DS回答':5s} {'Qwen成本':>9s} {'DS成本':>9s} {'总成本':>9s}"
        print(header)
        print("-" * len(header))

        for r in rows:
            print(f"{r['parent_name']:10s} "
                  f"{r['child_names']:12s} "
                  f"{mask_phone(r['phone']):14s} "
                  f"{r['registered_at']:20s} "
                  f"{r['credit_balance']:5d} "
                  f"{r['uploaded_parse_job_count']:5d} "
                  f"{r['qwen_question_count']:6d} "
                  f"{r['deepseek_tutor_initial_count']:5d} "
                  f"{r['deepseek_tutor_followup_count']:5d} "
                  f"¥{r['qwen_cost_cny']:8.4f} "
                  f"¥{r['deepseek_cost_cny']:8.4f} "
                  f"¥{r['total_cost_cny']:8.4f}")

        print("-" * len(header))
        print(f"合计用户数：{len(rows)}")
        print(f"Qwen成本合计：¥{user_qwen:.4f}")
        print(f"DeepSeek成本合计：¥{user_ds:.4f}")
        print(f"总成本合计：¥{user_total:.4f}")
        print()
        print("══════ 与 App 整体侧对账 ══════")
        print(f"App Qwen成本：       ¥{rec['app_qwen_cost_cny']:.4f}")
        print(f"用户侧 Qwen合计：    ¥{rec['user_qwen_cost_cny']:.4f}")
        print(f"未归属 Qwen成本：    ¥{rec['unassigned_qwen_cost_cny']:.4f}")
        print(f"Qwen差异：           ¥{rec['qwen_diff_cny']:.4f}")
        print()
        print(f"App DeepSeek成本：   ¥{rec['app_deepseek_cost_cny']:.4f}")
        print(f"用户侧 DeepSeek合计：¥{rec['user_deepseek_cost_cny']:.4f}")
        print(f"未归属 DeepSeek成本：¥{rec['unassigned_deepseek_cost_cny']:.4f}")
        print(f"DeepSeek差异：       ¥{rec['deepseek_diff_cny']:.4f}")
        print()
        print(f"App总成本：          ¥{rec['app_total_cost_cny']:.4f}")
        print(f"用户侧总成本：       ¥{rec['user_total_cost_cny']:.4f}")
        print(f"未归属总成本：       ¥{rec['unassigned_total_cost_cny']:.4f}")
        print(f"最终差异：           ¥{rec['final_diff_cny']:.4f}")
        print(f"差异原因：           {', '.join(rec['diff_reason'])}")

    conn.close()


if __name__ == '__main__':
    main()
