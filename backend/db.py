"""
悠米伴学 SQLite 持久化 — Phase 1
原 4 表保留（JSON 双写兼容）+ 新增商用表 + 索引 + 图片过期
"""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "yomi.db"


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


# ═══════════════════════════════════════════════════════════════
# Schema
# ═══════════════════════════════════════════════════════════════

def init():
    """建表 + 索引 + 迁移（幂等）"""
    c = _conn()
    c.executescript("""
        -- ── Phase 0 存量表（JSON 双写）─────────────────────
        CREATE TABLE IF NOT EXISTS parse_jobs (
            job_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS model_calls (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS tutor_chats (
            question_id TEXT PRIMARY KEY,
            history TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS credit_balances (
            child_id TEXT PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 50,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- ── Phase 1 用户表 ─────────────────────────────────
        CREATE TABLE IF NOT EXISTS parent_users (
            id TEXT PRIMARY KEY,
            phone TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            name TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS child_profiles (
            id TEXT PRIMARY KEY,
            parent_id TEXT NOT NULL,
            name TEXT NOT NULL,
            avatar TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- ── Phase 1 商用表 ─────────────────────────────────
        CREATE TABLE IF NOT EXISTS activation_codes (
            code TEXT PRIMARY KEY,
            code_type TEXT DEFAULT 'credit',
            credit_amount INTEGER DEFAULT 0,
            plan_code TEXT DEFAULT '',
            status TEXT DEFAULT 'unused',
            used_by_parent_user_id TEXT DEFAULT '',
            expires_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS membership (
            parent_user_id TEXT PRIMARY KEY,
            plan_code TEXT DEFAULT 'free_trial',
            status TEXT DEFAULT 'free_trial',
            member_until TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS credit_account (
            parent_user_id TEXT PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 50,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS credit_ledger (
            id TEXT PRIMARY KEY,
            parent_user_id TEXT NOT NULL,
            child_id TEXT DEFAULT '',
            change_amount INTEGER NOT NULL,
            balance_after INTEGER NOT NULL,
            reason_code TEXT NOT NULL,
            reason_desc TEXT DEFAULT '',
            related_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS payment_order (
            order_id TEXT PRIMARY KEY,
            parent_user_id TEXT NOT NULL,
            plan_code TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            channel TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            provider_order_id TEXT DEFAULT '',
            paid_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- ── Phase 1 图片过期跟踪 ───────────────────────────
        CREATE TABLE IF NOT EXISTS image_registry (
            jid TEXT NOT NULL,
            file_path TEXT NOT NULL,
            oss_key TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            expired INTEGER DEFAULT 0
        );

        -- ── Phase 1 错题本 ───────────────────────────────
        CREATE TABLE IF NOT EXISTS mistake_book_item (
            id TEXT PRIMARY KEY,
            child_id TEXT NOT NULL,
            question_id TEXT NOT NULL,
            error_type_code TEXT DEFAULT 'unknown',
            reason_desc TEXT DEFAULT '',
            mastery_status TEXT DEFAULT 'pending',
            next_review_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            deleted_at TEXT
        );

        -- ── Phase 1 ai_tutoring_chat 结构化（基准 Table 12）──
        CREATE TABLE IF NOT EXISTS ai_tutoring_chat (
            id TEXT PRIMARY KEY,
            question_id TEXT NOT NULL,
            child_id TEXT DEFAULT '',
            sequence_number INTEGER NOT NULL DEFAULT 1,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT DEFAULT '',
            model_call_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- ── Phase 1 作业/页面/题目/尝试（基准 Table 12）────
        CREATE TABLE IF NOT EXISTS assignment (
            id TEXT PRIMARY KEY,
            parent_user_id TEXT NOT NULL,
            child_id TEXT NOT NULL,
            source_type TEXT DEFAULT 'web_upload',
            source_name TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            deleted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS assignment_page (
            id TEXT PRIMARY KEY,
            assignment_id TEXT NOT NULL,
            page_no INTEGER DEFAULT 1,
            original_image_url TEXT,
            enhanced_image_url TEXT,
            image_expired INTEGER DEFAULT 0,
            image_expires_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS parse_jobs (
            job_id TEXT PRIMARY KEY,
            child_id TEXT NOT NULL DEFAULT '',
            parent_id TEXT NOT NULL DEFAULT '',
            file_name TEXT DEFAULT '',
            questions_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'uploaded',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS question_item (
            id TEXT PRIMARY KEY,
            assignment_id TEXT NOT NULL,
            page_id TEXT DEFAULT '',
            question_no INTEGER NOT NULL,
            bbox_json TEXT DEFAULT '[]',
            question_text TEXT DEFAULT '',
            visual_description TEXT DEFAULT '',
            structured_conditions TEXT DEFAULT '',
            crop_url TEXT,
            image_expired INTEGER DEFAULT 0,
            image_expires_at TEXT
        );
        CREATE TABLE IF NOT EXISTS question_attempt (
            id TEXT PRIMARY KEY,
            question_id TEXT NOT NULL,
            child_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            child_answer TEXT DEFAULT '',
            correct_answer TEXT DEFAULT '',
            first_attempt_result TEXT DEFAULT '',
            time_spent_seconds INTEGER DEFAULT 0,
            confidence_self_reported TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- ── 模型价格快照（成本测算基础）──────────────
        CREATE TABLE IF NOT EXISTS model_price_snapshots (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model_name TEXT NOT NULL,
            region TEXT DEFAULT '',
            input_price_per_1m REAL NOT NULL DEFAULT 0.0,
            output_price_per_1m REAL NOT NULL DEFAULT 0.0,
            cache_hit_price_per_1m REAL DEFAULT 0.0,
            cache_miss_price_per_1m REAL DEFAULT 0.0,
            currency TEXT DEFAULT 'CNY',
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            source TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- ── 成本账本：AI 辅导会话汇总（Hermes 规划 §5.4）──
        CREATE TABLE IF NOT EXISTS ai_tutoring_sessions (
            id TEXT PRIMARY KEY,
            question_id TEXT NOT NULL,
            child_id TEXT DEFAULT '',
            session_status TEXT DEFAULT 'active',
            message_count INTEGER DEFAULT 0,
            total_input_tokens INTEGER DEFAULT 0,
            total_output_tokens INTEGER DEFAULT 0,
            total_cost_cny REAL DEFAULT 0.0,
            started_at TEXT DEFAULT (datetime('now')),
            ended_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- ── 成本账本：AI 辅导消息明细（替代旧 ai_tutoring_chat）──
        CREATE TABLE IF NOT EXISTS ai_tutoring_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            question_id TEXT NOT NULL,
            child_id TEXT DEFAULT '',
            sequence_number INTEGER DEFAULT 1,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            call_id TEXT DEFAULT '',
            feature_code TEXT DEFAULT '',
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cost_cny REAL DEFAULT 0.0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- ── Hermes 自用分析库（Hermes 规划 §8）──────────────
        CREATE TABLE IF NOT EXISTS hermes_alerts (
            id TEXT PRIMARY KEY,
            alert_type TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            title TEXT NOT NULL,
            detail TEXT DEFAULT '',
            related_table TEXT DEFAULT '',
            related_id TEXT DEFAULT '',
            status TEXT DEFAULT 'open',
            resolved_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS hermes_daily_cost_reports (
            id TEXT PRIMARY KEY,
            report_date TEXT NOT NULL UNIQUE,
            total_cost_cny REAL DEFAULT 0.0,
            qwen_cost_cny REAL DEFAULT 0.0,
            deepseek_cost_cny REAL DEFAULT 0.0,
            avg_job_cost_cny REAL DEFAULT 0.0,
            zero_cost_calls INTEGER DEFAULT 0,
            total_calls INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS hermes_chain_audit_reports (
            id TEXT PRIMARY KEY,
            report_date TEXT NOT NULL UNIQUE,
            parsejob_count INTEGER DEFAULT 0,
            qwen_call_count INTEGER DEFAULT 0,
            question_item_count INTEGER DEFAULT 0,
            question_attempt_count INTEGER DEFAULT 0,
            tutor_msg_count INTEGER DEFAULT 0,
            credit_ledger_count INTEGER DEFAULT 0,
            break_points TEXT DEFAULT '',
            health_score INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS hermes_campaign_forecasts (
            id TEXT PRIMARY KEY,
            campaign_name TEXT NOT NULL,
            user_count INTEGER DEFAULT 0,
            days INTEGER DEFAULT 0,
            estimated_total_cost REAL DEFAULT 0.0,
            expected_revenue REAL DEFAULT 0.0,
            roi_result TEXT DEFAULT '',
            budget_cap REAL DEFAULT 0.0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS hermes_price_watch (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model_name TEXT NOT NULL,
            old_price REAL DEFAULT 0.0,
            new_price REAL DEFAULT 0.0,
            change_rate REAL DEFAULT 0.0,
            source_url TEXT DEFAULT '',
            suggested_action TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS hermes_backfill_candidates (
            id TEXT PRIMARY KEY,
            source_table TEXT NOT NULL,
            source_id TEXT NOT NULL,
            missing_type TEXT NOT NULL,
            suggested_action TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    c.commit()

    # ── 存量表迁移（给旧表加新列，幂等）────────────────────
    migrations = [
        "ALTER TABLE parse_jobs ADD COLUMN child_id TEXT DEFAULT ''",
        "ALTER TABLE parse_jobs ADD COLUMN parent_id TEXT DEFAULT ''",
        "ALTER TABLE parse_jobs ADD COLUMN client_task_id TEXT DEFAULT ''",
        "ALTER TABLE model_calls ADD COLUMN task_id TEXT DEFAULT ''",
        "ALTER TABLE model_calls ADD COLUMN feature_code TEXT DEFAULT ''",
        "ALTER TABLE model_calls ADD COLUMN prompt_version TEXT DEFAULT ''",
        "ALTER TABLE model_calls ADD COLUMN schema_version TEXT DEFAULT ''",
        "ALTER TABLE model_calls ADD COLUMN credit_cost REAL DEFAULT 0.0",
        "ALTER TABLE model_calls ADD COLUMN retry_count INTEGER DEFAULT 0",
        "ALTER TABLE child_profiles ADD COLUMN grade TEXT DEFAULT ''",
        "ALTER TABLE child_profiles ADD COLUMN semester TEXT DEFAULT ''",
        "ALTER TABLE child_profiles ADD COLUMN textbook_version TEXT DEFAULT ''",
        "ALTER TABLE child_profiles ADD COLUMN deleted_at TEXT",
        "ALTER TABLE image_registry ADD COLUMN oss_key TEXT DEFAULT ''",
        "ALTER TABLE parse_jobs ADD COLUMN file_name TEXT DEFAULT ''",
        "ALTER TABLE parse_jobs ADD COLUMN questions_count INTEGER DEFAULT 0",
        "ALTER TABLE parse_jobs ADD COLUMN status TEXT DEFAULT 'uploaded'",
        "ALTER TABLE parse_jobs ADD COLUMN created_at TEXT DEFAULT ''",
        # parse_jobs 补齐字段（基准 Table 12）
        "ALTER TABLE parse_jobs ADD COLUMN progress TEXT DEFAULT ''",
        "ALTER TABLE parse_jobs ADD COLUMN error_code TEXT DEFAULT ''",
        "ALTER TABLE parse_jobs ADD COLUMN retry_count INTEGER DEFAULT 0",
        "ALTER TABLE parse_jobs ADD COLUMN completed_at TEXT DEFAULT ''",
        # ai_tutoring_chat 软删除（基准 §5.4 联级）
        "ALTER TABLE ai_tutoring_chat ADD COLUMN deleted_at TEXT",
        # model_calls 成本与关联字段（幂等）
        "ALTER TABLE model_calls ADD COLUMN provider TEXT DEFAULT ''",
        "ALTER TABLE model_calls ADD COLUMN model_name TEXT DEFAULT ''",
        "ALTER TABLE model_calls ADD COLUMN input_tokens INTEGER DEFAULT 0",
        "ALTER TABLE model_calls ADD COLUMN output_tokens INTEGER DEFAULT 0",
        "ALTER TABLE model_calls ADD COLUMN image_count INTEGER DEFAULT 0",
        "ALTER TABLE model_calls ADD COLUMN image_total_bytes INTEGER DEFAULT 0",
        "ALTER TABLE model_calls ADD COLUMN cache_hit_tokens INTEGER DEFAULT 0",
        "ALTER TABLE model_calls ADD COLUMN cache_miss_tokens INTEGER DEFAULT 0",
        "ALTER TABLE model_calls ADD COLUMN unit_price_input REAL DEFAULT 0.0",
        "ALTER TABLE model_calls ADD COLUMN unit_price_output REAL DEFAULT 0.0",
        "ALTER TABLE model_calls ADD COLUMN unit_price_cache_hit REAL DEFAULT 0.0",
        "ALTER TABLE model_calls ADD COLUMN unit_price_cache_miss REAL DEFAULT 0.0",
        "ALTER TABLE model_calls ADD COLUMN currency TEXT DEFAULT 'CNY'",
        "ALTER TABLE model_calls ADD COLUMN raw_cost REAL DEFAULT 0.0",
        "ALTER TABLE model_calls ADD COLUMN cost_cny REAL DEFAULT 0.0",
        "ALTER TABLE model_calls ADD COLUMN pricing_snapshot_id TEXT DEFAULT ''",
        "ALTER TABLE model_calls ADD COLUMN latency_ms INTEGER DEFAULT 0",
        "ALTER TABLE model_calls ADD COLUMN status TEXT DEFAULT 'success'",
        "ALTER TABLE model_calls ADD COLUMN error_code TEXT DEFAULT ''",
        "ALTER TABLE model_calls ADD COLUMN request_id TEXT DEFAULT ''",
        "ALTER TABLE model_calls ADD COLUMN job_id TEXT DEFAULT ''",
        "ALTER TABLE model_calls ADD COLUMN question_id TEXT DEFAULT ''",
        # 成本账本补字段（Hermes 工作流规划 P0）
        "ALTER TABLE model_calls ADD COLUMN trace_id TEXT DEFAULT ''",
        "ALTER TABLE model_calls ADD COLUMN parent_trace_id TEXT DEFAULT ''",
        "ALTER TABLE model_calls ADD COLUMN sub_stage TEXT DEFAULT ''",
        # call_source 分账字段
        "ALTER TABLE model_calls ADD COLUMN call_source TEXT DEFAULT 'prod'",
        # credit_ledger 补字段
        "ALTER TABLE credit_ledger ADD COLUMN feature_code TEXT DEFAULT ''",
        "ALTER TABLE credit_ledger ADD COLUMN job_id TEXT DEFAULT ''",
        "ALTER TABLE credit_ledger ADD COLUMN question_id TEXT DEFAULT ''",
        "ALTER TABLE credit_ledger ADD COLUMN call_id TEXT DEFAULT ''",
        "ALTER TABLE credit_ledger ADD COLUMN actual_cost_cny REAL DEFAULT 0.0",
        "ALTER TABLE credit_ledger ADD COLUMN credit_delta INTEGER DEFAULT 0",
        "ALTER TABLE credit_ledger ADD COLUMN billing_status TEXT DEFAULT ''",
        # question_attempt 补字段
        "ALTER TABLE question_attempt ADD COLUMN is_correct INTEGER DEFAULT -1",
        "ALTER TABLE question_attempt ADD COLUMN score REAL DEFAULT 0.0",
        "ALTER TABLE question_attempt ADD COLUMN check_call_id TEXT DEFAULT ''",
        "ALTER TABLE question_attempt ADD COLUMN check_cost_cny REAL DEFAULT 0.0",
        # parse_jobs 补字段
        "ALTER TABLE parse_jobs ADD COLUMN parse_mode TEXT DEFAULT ''",
        "ALTER TABLE parse_jobs ADD COLUMN parser_provider TEXT DEFAULT ''",
        "ALTER TABLE parse_jobs ADD COLUMN parser_model TEXT DEFAULT ''",
        "ALTER TABLE parse_jobs ADD COLUMN qwen_parse_call_id TEXT DEFAULT ''",
        "ALTER TABLE parse_jobs ADD COLUMN total_parse_cost_cny REAL DEFAULT 0.0",
        # question_item 补字段
        "ALTER TABLE question_item ADD COLUMN source_call_id TEXT DEFAULT ''",
        "ALTER TABLE question_item ADD COLUMN parse_source TEXT DEFAULT ''",
        "ALTER TABLE question_item ADD COLUMN confidence REAL DEFAULT 0.0",
        "ALTER TABLE question_item ADD COLUMN parse_cost_allocated_cny REAL DEFAULT 0.0",
        # parse_jobs 软删除（左划删除服务端持久化）
        "ALTER TABLE parse_jobs ADD COLUMN deleted_at TEXT",
        # parse_jobs 上传追踪 ID（超时恢复）
        "ALTER TABLE parse_jobs ADD COLUMN client_upload_id TEXT DEFAULT ''",
    ]
    for sql in migrations:
        try:
            c.execute(sql)
        except sqlite3.OperationalError:
            pass  # 列已存在，跳过
    c.commit()

    # ── 索引（Table 21 对照）────────────────────────────────
    indexes = [
        # parse_jobs 查询加速
        "CREATE INDEX IF NOT EXISTS idx_parse_jobs_child_id ON parse_jobs(child_id)",
        "CREATE INDEX IF NOT EXISTS idx_parse_jobs_client_task ON parse_jobs(child_id, client_task_id)",
        "CREATE INDEX IF NOT EXISTS idx_parse_job_child_status_created ON parse_jobs(child_id, status, created_at)",
        # model_calls 查询加速
        "CREATE INDEX IF NOT EXISTS idx_model_calls_created ON model_calls(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_model_calls_feature ON model_calls(feature_code)",
        "CREATE INDEX IF NOT EXISTS idx_model_calls_call_source ON model_calls(call_source)",
        # 用户查询
        "CREATE INDEX IF NOT EXISTS idx_parent_users_phone ON parent_users(phone)",
        "CREATE INDEX IF NOT EXISTS idx_child_profiles_parent ON child_profiles(parent_id)",
        # 商用查询
        "CREATE INDEX IF NOT EXISTS idx_activation_used_by ON activation_codes(used_by_parent_user_id)",
        "CREATE INDEX IF NOT EXISTS idx_credit_ledger_parent ON credit_ledger(parent_user_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_payment_order_parent ON payment_order(parent_user_id)",
        # 图片过期扫描
        "CREATE INDEX IF NOT EXISTS idx_image_expires ON image_registry(expires_at, expired)",
        # 错题查询
        "CREATE INDEX IF NOT EXISTS idx_mistake_child ON mistake_book_item(child_id, mastery_status)",
        "CREATE INDEX IF NOT EXISTS idx_mistake_question ON mistake_book_item(question_id, child_id)",
        # 作业查询（基准 Table 21）
        "CREATE INDEX IF NOT EXISTS idx_assignment_child_created ON assignment(child_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_assignment_page_assignment ON assignment_page(assignment_id)",
        "CREATE INDEX IF NOT EXISTS idx_question_item_assignment ON question_item(assignment_id)",
        "CREATE INDEX IF NOT EXISTS idx_question_attempt_question ON question_attempt(question_id, child_id)",
        "CREATE INDEX IF NOT EXISTS idx_ai_chat_question_seq ON tutor_chats(question_id)",
        # 成本账本索引
        "CREATE INDEX IF NOT EXISTS idx_model_calls_trace ON model_calls(trace_id)",
        "CREATE INDEX IF NOT EXISTS idx_model_calls_job ON model_calls(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_tutoring_sessions_question ON ai_tutoring_sessions(question_id)",
        "CREATE INDEX IF NOT EXISTS idx_tutoring_messages_session ON ai_tutoring_messages(session_id, sequence_number)",
        "CREATE INDEX IF NOT EXISTS idx_tutoring_messages_call ON ai_tutoring_messages(call_id)",
        "CREATE INDEX IF NOT EXISTS idx_credit_ledger_feature ON credit_ledger(feature_code)",
        "CREATE INDEX IF NOT EXISTS idx_credit_ledger_call ON credit_ledger(call_id)",
    ]
    for sql in indexes:
        c.execute(sql)
    c.commit()
    c.close()


# ═══════════════════════════════════════════════════════════════
# Phase 0 存量操作（保持兼容）
# ═══════════════════════════════════════════════════════════════

def save_job(job_id: str, data: dict):
    c = _conn()
    child_id = data.get("child_id", "")
    parent_id = data.get("parent_id", "")
    client_task_id = data.get("client_task_id", "")
    progress = data.get("progress", "")
    error_code = data.get("error_code", "")
    retry_count = data.get("retry_count", 0)
    completed_at = data.get("completed_at", "")
    c.execute(
        "INSERT OR REPLACE INTO parse_jobs "
        "(job_id, child_id, parent_id, client_task_id, client_upload_id, progress, error_code, retry_count, completed_at, data, updated_at) "
        "VALUES (?, ?, ?, ?, COALESCE((SELECT client_upload_id FROM parse_jobs WHERE job_id = ?), ?), ?, ?, ?, ?, ?, datetime('now'))",
        (job_id, child_id, parent_id, client_task_id, job_id, client_task_id, progress, error_code, retry_count, completed_at, json.dumps(data, default=str)),
    )
    c.commit()
    c.close()


def save_model_call(data: dict):
    c = _conn()
    call_id = data.get("id", "")
    task_id = data.get("task_id", "")
    feature_code = data.get("feature_code", "")
    job_id = data.get("job_id", "")
    question_id = data.get("question_id", "")
    provider = data.get("provider_name", data.get("provider", ""))
    model_name = data.get("model_name", "")
    prompt_version = data.get("prompt_version", data.get("prompt_name", ""))
    schema_version = data.get("schema_version", "")
    request_id = data.get("request_id", "")
    input_tokens = int(data.get("input_tokens", 0))
    output_tokens = int(data.get("output_tokens", 0))
    image_count = int(data.get("image_count", 0))
    image_total_bytes = int(data.get("image_total_bytes", 0))
    cache_hit_tokens = int(data.get("cache_hit_tokens", data.get("cached_tokens", 0)))
    cache_miss_tokens = int(data.get("cache_miss_tokens", 0))
    unit_price_input = float(data.get("unit_price_input", 0.0))
    unit_price_output = float(data.get("unit_price_output", 0.0))
    unit_price_cache_hit = float(data.get("unit_price_cache_hit", 0.0))
    unit_price_cache_miss = float(data.get("unit_price_cache_miss", 0.0))
    currency = data.get("currency", "CNY")
    raw_cost = float(data.get("raw_cost", data.get("estimated_cost", 0.0)))
    cost_cny = float(data.get("cost_cny", data.get("credit_cost", 0.0)))
    pricing_snapshot_id = data.get("pricing_snapshot_id", "")
    latency_ms = int(data.get("latency_ms", 0))
    status = data.get("status", "success" if data.get("success", True) else "failed")
    error_code = data.get("error_code", "")
    credit_cost = float(data.get("credit_cost", data.get("estimated_cost", 0.0)))
    retry_count = int(data.get("retry_count", 0))
    trace_id = data.get("trace_id", "")
    parent_trace_id = data.get("parent_trace_id", "")
    sub_stage = data.get("sub_stage", "")
    call_source = os.environ.get("YOMICALL_SOURCE", "prod")
    c.execute(
        "INSERT OR REPLACE INTO model_calls "
        "(id, task_id, feature_code, job_id, question_id, provider, model_name, "
        "prompt_version, schema_version, request_id, "
        "input_tokens, output_tokens, image_count, image_total_bytes, "
        "cache_hit_tokens, cache_miss_tokens, "
        "unit_price_input, unit_price_output, unit_price_cache_hit, unit_price_cache_miss, "
        "currency, raw_cost, cost_cny, pricing_snapshot_id, "
        "latency_ms, status, error_code, credit_cost, retry_count, trace_id, parent_trace_id, sub_stage, call_source, data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (call_id, task_id, feature_code, job_id, question_id, provider, model_name,
         prompt_version, schema_version, request_id,
         input_tokens, output_tokens, image_count, image_total_bytes,
         cache_hit_tokens, cache_miss_tokens,
         unit_price_input, unit_price_output, unit_price_cache_hit, unit_price_cache_miss,
         currency, raw_cost, cost_cny, pricing_snapshot_id,
         latency_ms, status, error_code, credit_cost, retry_count,
         trace_id, parent_trace_id, sub_stage, call_source,
         json.dumps(data, default=str)),
    )
    c.commit()
    c.close()


def save_tutor_chat(question_id: str, history: list):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO tutor_chats (question_id, history, updated_at) VALUES (?, ?, datetime('now'))",
        (question_id, json.dumps(history, default=str)),
    )
    c.commit()
    c.close()


def save_credit_balance(parent_user_id: str, balance: int):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO credit_account (parent_user_id, balance, updated_at) VALUES (?, ?, datetime('now'))",
        (parent_user_id, balance),
    )
    c.commit()
    c.close()


def _update_model_call_credit(call_id: str, credit_delta: int, actual_cost_cny: float):
    """回写 model_calls.credit_cost（配合 credit_ledger 落账）"""
    c = _conn()
    c.execute(
        "UPDATE model_calls SET credit_cost = ? WHERE id = ?",
        (credit_delta, call_id),
    )
    c.commit()
    c.close()


def load_all():
    """启动时从 SQLite 恢复到内存 — 排除已删除的任务"""
    c = _conn()

    rows = c.execute("SELECT job_id, data FROM parse_jobs WHERE deleted_at IS NULL").fetchall()
    jobs = {}
    for row in rows:
        entry = json.loads(row["data"])
        # 规范化：扁平旧格式 → 嵌套新格式（统一数据结构，根治端点兼容问题）
        if "job" not in entry and "status" in entry:
            entry = {
                "job": {
                    "status": entry.get("status", ""),
                    "questions_count": entry.get("questions_count", 0),
                    "file_name": entry.get("file_name", ""),
                    "created_at": entry.get("created_at", ""),
                    "updated_at": entry.get("updated_at", ""),
                    "completed_at": entry.get("completed_at", ""),
                },
                "questions": entry.get("questions", []),
                "poll_count": entry.get("poll_count", 0),
                "child_id": entry.get("child_id", ""),
                "parent_id": entry.get("parent_id", ""),
                "client_task_id": entry.get("client_task_id", ""),
                "progress": entry.get("progress", ""),
                "error_code": entry.get("error_code", ""),
                "retry_count": entry.get("retry_count", 0),
            }
        jobs[row["job_id"]] = entry

    rows = c.execute("SELECT data FROM model_calls ORDER BY created_at").fetchall()
    model_calls = [json.loads(row["data"]) for row in rows]

    rows = c.execute("SELECT question_id, history FROM tutor_chats").fetchall()
    tutor_chats = {row["question_id"]: json.loads(row["history"]) for row in rows}

    rows = c.execute("SELECT parent_user_id, balance FROM credit_account").fetchall()
    credit_balances = {row["parent_user_id"]: row["balance"] for row in rows}

    c.close()
    return jobs, model_calls, tutor_chats, credit_balances


# ═══════════════════════════════════════════════════════════════
# Phase 1 用户持久化
# ═══════════════════════════════════════════════════════════════

def save_parent_user(pid: str, phone: str, password_hash: str, name: str = ""):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO parent_users (id, phone, password_hash, name) VALUES (?, ?, ?, ?)",
        (pid, phone, password_hash, name),
    )
    c.commit()
    c.close()


def load_parent_users() -> dict[str, dict]:
    c = _conn()
    rows = c.execute("SELECT * FROM parent_users").fetchall()
    result = {row["id"]: dict(row) for row in rows}
    c.close()
    return result


def save_child_profile(cid: str, parent_id: str, name: str, avatar: str = ""):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO child_profiles (id, parent_id, name, avatar) VALUES (?, ?, ?, ?)",
        (cid, parent_id, name, avatar),
    )
    c.commit()
    c.close()


def load_child_profiles() -> dict[str, dict]:
    c = _conn()
    rows = c.execute("SELECT * FROM child_profiles").fetchall()
    result = {row["id"]: dict(row) for row in rows}
    c.close()
    return result


# ═══════════════════════════════════════════════════════════════
# 图片过期注册（Table 19/20）
# ═══════════════════════════════════════════════════════════════

def register_image(jid: str, file_path: str, created_at: str, oss_key: str = ""):
    """注册图片，写入 expires_at = created_at + 7 days"""
    c = _conn()
    c.execute(
        "INSERT INTO image_registry (jid, file_path, oss_key, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, datetime(?, '+7 days'))",
        (jid, file_path, oss_key, created_at, created_at),
    )
    c.commit()
    c.close()


def cleanup_expired_images() -> list[str]:
    """扫描过期图片，删除本地文件 + OSS 对象，标记 expired=1。"""
    import os as _os
    c = _conn()
    rows = c.execute(
        "SELECT jid, file_path, oss_key FROM image_registry WHERE expired = 0 AND expires_at < datetime('now')"
    ).fetchall()
    deleted = []
    for row in rows:
        # 删除本地文件
        try:
            if _os.path.exists(row["file_path"]):
                _os.remove(row["file_path"])
        except OSError:
            pass
        # 删除 OSS 对象
        oss_key = row["oss_key"] or ""
        if oss_key:
            try:
                import oss_client as _oss_cl
                _oss_cl.delete_object(oss_key)
            except Exception:
                pass
        deleted.append(row["file_path"] or oss_key)
        c.execute("UPDATE image_registry SET expired = 1 WHERE jid = ?", (row["jid"],))
    c.commit()
    c.close()
    return deleted


# ═══════════════════════════════════════════════════════════════
# 错题本
# ═══════════════════════════════════════════════════════════════

def save_mistake(child_id: str, question_id: str, error_type: str = "unknown", reason: str = ""):
    c = _conn()
    import uuid
    mid = uuid.uuid4().hex[:12]
    c.execute(
        "INSERT OR REPLACE INTO mistake_book_item (id, child_id, question_id, error_type_code, reason_desc, mastery_status, next_review_at) "
        "VALUES (?, ?, ?, ?, ?, 'pending', datetime('now', '+1 day'))",
        (mid, child_id, question_id, error_type, reason),
    )
    c.commit()
    c.close()
    return mid

def get_mistakes(child_id: str) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM mistake_book_item WHERE child_id = ? AND deleted_at IS NULL ORDER BY created_at DESC",
        (child_id,),
    ).fetchall()
    result = [dict(r) for r in rows]
    c.close()
    return result

def delete_mistake(mistake_id: str):
    """软删除错题 + 联级删除关联的辅导对话（基准 §5.4）"""
    c = _conn()
    # 获取关联 question_id
    row = c.execute("SELECT question_id FROM mistake_book_item WHERE id = ?", (mistake_id,)).fetchone()
    question_id = row["question_id"] if row else None
    # 软删除错题
    c.execute("UPDATE mistake_book_item SET deleted_at = datetime('now') WHERE id = ?", (mistake_id,))
    # 联级软删除辅导对话
    if question_id:
        c.execute("UPDATE ai_tutoring_chat SET deleted_at = datetime('now') WHERE question_id = ? AND deleted_at IS NULL", (question_id,))
    c.commit()
    c.close()

# ═══════════════════════════════════════════════════════════════
# ai_tutoring_chat 结构化
# ═══════════════════════════════════════════════════════════════

def save_tutor_message(msg_id: str, question_id: str, child_id: str, seq: int, role: str, content: str, model_call_id: str = ""):
    c = _conn()
    c.execute(
        "INSERT INTO ai_tutoring_chat (id, question_id, child_id, sequence_number, role, content, model_call_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (msg_id, question_id, child_id, seq, role, content, model_call_id),
    )
    c.commit()
    c.close()

def get_tutor_chat(question_id: str) -> list[dict]:
    "按 sequence_number 排序获取对话"
    c = _conn()
    rows = c.execute(
        "SELECT * FROM ai_tutoring_chat WHERE question_id = ? ORDER BY sequence_number",
        (question_id,),
    ).fetchall()
    result = [dict(r) for r in rows]
    c.close()
    return result

# ═══════════════════════════════════════════════════════════════
# ai_tutoring_sessions + messages（成本账本 — Hermes 规划 §5.4）
# ═══════════════════════════════════════════════════════════════

def upsert_tutoring_session(session_id: str, question_id: str, child_id: str = ""):
    """创建或更新辅导会话。"""
    c = _conn()
    c.execute(
        "INSERT OR IGNORE INTO ai_tutoring_sessions (id, question_id, child_id, session_status) VALUES (?, ?, ?, 'active')",
        (session_id, question_id, child_id),
    )
    c.commit()
    c.close()

def save_tutoring_message(msg_id: str, session_id: str, question_id: str, child_id: str,
                           seq: int, role: str, content: str,
                           call_id: str = "", feature_code: str = "",
                           input_tokens: int = 0, output_tokens: int = 0, cost_cny: float = 0.0):
    """写入 ai_tutoring_messages 并更新 session 汇总。"""
    c = _conn()
    c.execute(
        "INSERT INTO ai_tutoring_messages (id, session_id, question_id, child_id, sequence_number, role, content, call_id, feature_code, input_tokens, output_tokens, cost_cny) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (msg_id, session_id, question_id, child_id, seq, role, content, call_id, feature_code, input_tokens, output_tokens, cost_cny),
    )
    # 更新 session 汇总
    c.execute(
        "UPDATE ai_tutoring_sessions SET message_count = message_count + 1, "
        "total_input_tokens = total_input_tokens + ?, total_output_tokens = total_output_tokens + ?, "
        "total_cost_cny = total_cost_cny + ? WHERE id = ?",
        (input_tokens, output_tokens, cost_cny, session_id),
    )
    c.commit()
    c.close()

# ═══════════════════════════════════════════════════════════════
# 作业/页面/题目/尝试
# ═══════════════════════════════════════════════════════════════

def create_assignment(aid: str, parent_id: str, child_id: str, source_type: str = "web_upload", source_name: str = ""):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO assignment (id, parent_user_id, child_id, source_type, source_name, status) VALUES (?, ?, ?, ?, ?, 'pending')",
        (aid, parent_id, child_id, source_type, source_name),
    )
    c.commit()
    c.close()
    return aid

def create_assignment_page(pid: str, assignment_id: str, page_no: int = 1, oss_key: str = ""):
    c = _conn()
    c.execute(
        "INSERT INTO assignment_page (id, assignment_id, page_no, original_image_url, image_expires_at) "
        "VALUES (?, ?, ?, ?, datetime('now', '+7 days'))",
        (pid, assignment_id, page_no, oss_key),
    )
    c.commit()
    c.close()
    return pid

def create_question_item(qid: str, assignment_id: str, page_id: str, question_no: int, question_text: str, bbox: list, visual_description: str = "",
                          source_call_id: str = "", parse_cost_allocated_cny: float = 0.0):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO question_item (id, assignment_id, page_id, question_no, bbox_json, question_text, visual_description, source_call_id, parse_cost_allocated_cny) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (qid, assignment_id, page_id, question_no, json.dumps(bbox or []), question_text, visual_description or "", source_call_id, parse_cost_allocated_cny),
    )
    c.commit()
    c.close()
    return qid

def save_parse_job(job_id: str, child_id: str, parent_id: str, file_name: str, questions_count: int, status: str, created_at: str,
                    progress: str = "", error_code: str = "", retry_count: int = 0, completed_at: str = "",
                    parse_mode: str = "", parser_provider: str = "", parser_model: str = "",
                    qwen_parse_call_id: str = "", total_parse_cost_cny: float = 0.0, data_json: str = "",
                    client_upload_id: str = ""):
    """持久化解析任务元数据，供 getRecent 跨重启查询用。
    data_json: 若不传，用 COALESCE 保留已有 data（向后兼容）。
    但 save_result 已持有完整 data dict，应显式传入避免 INSERT OR REPLACE 丢失。
    ⛔ 幂等保护：如果 job 已存在且为 completed，拒绝覆盖（防止 worker 完成态被初始 uploaded 覆盖）。
    """
    c = _conn()
    # 幂等保护：completed 不可被覆盖
    existing = c.execute(
        "SELECT status FROM parse_jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    if existing and existing["status"] == "completed":
        c.close()
        return  # 已完成任务，拒接覆盖
    if data_json:
        c.execute(
            "INSERT OR REPLACE INTO parse_jobs (job_id, child_id, parent_id, file_name, questions_count, status, created_at, progress, error_code, retry_count, completed_at, parse_mode, parser_provider, parser_model, qwen_parse_call_id, total_parse_cost_cny, data, updated_at, client_upload_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "COALESCE((SELECT client_upload_id FROM parse_jobs WHERE job_id = ?), ?))",
            (job_id, child_id, parent_id, file_name, questions_count, status, created_at, progress, error_code, retry_count, completed_at, parse_mode, parser_provider, parser_model, qwen_parse_call_id, total_parse_cost_cny, data_json, created_at, job_id, client_upload_id),
        )
    else:
        c.execute(
            "INSERT OR REPLACE INTO parse_jobs (job_id, child_id, parent_id, file_name, questions_count, status, created_at, progress, error_code, retry_count, completed_at, parse_mode, parser_provider, parser_model, qwen_parse_call_id, total_parse_cost_cny, data, updated_at, client_upload_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "COALESCE((SELECT data FROM parse_jobs WHERE job_id = ?), '{}'), ?, "
            "COALESCE((SELECT client_upload_id FROM parse_jobs WHERE job_id = ?), ?))",
            (job_id, child_id, parent_id, file_name, questions_count, status, created_at, progress, error_code, retry_count, completed_at, parse_mode, parser_provider, parser_model, qwen_parse_call_id, total_parse_cost_cny, job_id, created_at, job_id, client_upload_id),
        )
    c.commit()
    c.close()

def get_recent_parse_jobs(child_id: str, limit: int = 20):
    """从 DB 查询指定 child 的最近 N 条可展示的解析任务。
    排除：已删除 (deleted_at IS NOT NULL)、失败 (status='failed')。
    """
    c = _conn()
    rows = c.execute(
        "SELECT job_id, file_name, questions_count, status, created_at, progress, error_code, retry_count, completed_at "
        "FROM parse_jobs WHERE child_id = ? "
        "AND deleted_at IS NULL "
        "AND status != 'failed' "
        "ORDER BY created_at DESC LIMIT ?",
        (child_id, limit),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def soft_delete_parse_job(job_id: str, child_id: str) -> bool:
    """软删除：deleted_at 写入当前时间。返回 True 表示删除成功（有行受影响）。"""
    from datetime import datetime
    c = _conn()
    cur = c.execute(
        "UPDATE parse_jobs SET deleted_at = ? WHERE job_id = ? AND child_id = ? AND deleted_at IS NULL",
        (datetime.now().isoformat(), job_id, child_id),
    )
    affected = cur.rowcount
    c.commit()
    c.close()
    return affected > 0


def get_existing_job_by_client_upload(child_id: str, client_upload_id: str):
    """去重查询：按 child_id + client_upload_id 查找已有任务（不含 failed/deleted）。
    返回 (job_id, status, questions_count, file_name) 或 None。用于 POST handler 幂等保护。"""
    if not client_upload_id:
        return None
    c = _conn()
    row = c.execute(
        "SELECT job_id, status, questions_count, file_name FROM parse_jobs "
        "WHERE child_id = ? AND client_upload_id = ? AND deleted_at IS NULL AND status != 'failed' "
        "ORDER BY created_at DESC LIMIT 1",
        (child_id, client_upload_id),
    ).fetchone()
    c.close()
    return (row["job_id"], row["status"], row["questions_count"], row["file_name"]) if row else None


def get_job_by_client_upload_id(child_id: str, client_upload_id: str):
    """按 client_upload_id 查找未被删除的解析任务。返回 dict 或 None。"""
    if not client_upload_id:
        return None
    c = _conn()
    row = c.execute(
        "SELECT job_id, status, questions_count, file_name, created_at FROM parse_jobs "
        "WHERE child_id = ? AND client_upload_id = ? AND deleted_at IS NULL AND status != 'failed' "
        "ORDER BY created_at DESC LIMIT 1",
        (child_id, client_upload_id),
    ).fetchone()
    c.close()
    return dict(row) if row else None

def get_job_data(job_id: str):
    """读取 parse_jobs 的 data 列（JSON），用于跨重启恢复 questions 等。"""
    import json as _json
    c = _conn()
    row = c.execute("SELECT data FROM parse_jobs WHERE job_id = ?", (job_id,)).fetchone()
    c.close()
    if row and row[0]:
        try:
            return _json.loads(row[0])
        except Exception:
            return None
    return None

def save_question_attempt(attempt_id: str, question_id: str, child_id: str, status: str, child_answer: str = ""):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO question_attempt (id, question_id, child_id, status, child_answer) VALUES (?, ?, ?, ?, ?)",
        (attempt_id, question_id, child_id, status, child_answer),
    )
    c.commit()
    c.close()
    return attempt_id

def get_assignments(child_id: str) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM assignment WHERE child_id = ? AND deleted_at IS NULL ORDER BY created_at DESC",
        (child_id,),
    ).fetchall()
    result = [dict(r) for r in rows]
    c.close()
    return result

# ═══════════════════════════════════════════════════════════════
# 激活码管理
# ═══════════════════════════════════════════════════════════════

def create_activation_code(code: str, credit_amount: int = 100, expires_days: int = 365):
    "注册新激活码"
    c = _conn()
    c.execute(
        "INSERT OR IGNORE INTO activation_codes (code, code_type, credit_amount, status, expires_at) "
        "VALUES (?, 'credit', ?, 'unused', datetime('now', ?))",
        (code, credit_amount, f'+{expires_days} days'),
    )
    c.commit()
    c.close()

def redeem_activation_code(code: str, parent_user_id: str) -> dict | None:
    "核销激活码，返回 {credit_amount, code} 或 None（无效/已用/过期）"
    c = _conn()
    row = c.execute(
        "SELECT * FROM activation_codes WHERE code = ?", (code,)
    ).fetchone()
    if not row:
        c.close()
        return None
    if row["status"] != "unused":
        c.close()
        return None
    if row["expires_at"] and row["expires_at"] < datetime.now().isoformat():
        c.close()
        return None
    # 核销
    c.execute(
        "UPDATE activation_codes SET status='used', used_by_parent_user_id=? WHERE code=?",
        (parent_user_id, code),
    )
    c.commit()
    result = {"credit_amount": row["credit_amount"], "code": code}
    c.close()
    return result

def add_credit_ledger_entry(parent_user_id: str, child_id: str, amount: int, reason_code: str, reason_desc: str = "", related_id: str = "",
                            feature_code: str = "", job_id: str = "", question_id: str = "", call_id: str = "",
                            actual_cost_cny: float = 0.0, credit_delta: int = 0, billing_status: str = ""):
    "写学豆流水（成本账本完整字段）"
    import uuid
    c = _conn()
    # 更新余额
    bal_row = c.execute("SELECT balance FROM credit_account WHERE parent_user_id = ?", (parent_user_id,)).fetchone()
    current = bal_row["balance"] if bal_row else 50
    new_balance = current + amount
    c.execute(
        "INSERT OR REPLACE INTO credit_account (parent_user_id, balance, updated_at) VALUES (?, ?, datetime('now'))",
        (parent_user_id, new_balance),
    )
    c.execute(
        "INSERT INTO credit_ledger (id, parent_user_id, child_id, change_amount, balance_after, reason_code, reason_desc, related_id, feature_code, job_id, question_id, call_id, actual_cost_cny, credit_delta, billing_status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex[:12], parent_user_id, child_id, amount, new_balance, reason_code, reason_desc, related_id, feature_code, job_id, question_id, call_id, actual_cost_cny, credit_delta or amount, billing_status),
    )
    c.commit()
    c.close()
    return new_balance

# ═══════════════════════════════════════════════════════════════
# 模型价格快照
# ═══════════════════════════════════════════════════════════════

def save_price_snapshot(provider: str, model_name: str, input_price: float, output_price: float,
                        region: str = "", cache_hit_price: float = 0.0, cache_miss_price: float = 0.0,
                        currency: str = "CNY", source: str = "") -> str:
    """写入新价格快照（旧快照自动标记 is_active=0）"""
    import uuid
    c = _conn()
    now = datetime.now().isoformat()
    sid = uuid.uuid4().hex[:12]
    # 停用同 provider+model 的旧快照
    c.execute("UPDATE model_price_snapshots SET is_active=0, updated_at=? WHERE provider=? AND model_name=? AND is_active=1",
              (now, provider, model_name))
    c.execute(
        "INSERT INTO model_price_snapshots (id, provider, model_name, region, "
        "input_price_per_1m, output_price_per_1m, cache_hit_price_per_1m, cache_miss_price_per_1m, "
        "currency, effective_from, source, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (sid, provider, model_name, region,
         input_price, output_price, cache_hit_price, cache_miss_price,
         currency, now, source, now, now),
    )
    c.commit()
    c.close()
    return sid


def get_active_pricing(provider: str, model_name: str) -> dict | None:
    """获取当前生效的价格快照"""
    c = _conn()
    row = c.execute(
        "SELECT * FROM model_price_snapshots WHERE provider=? AND model_name=? AND is_active=1 ORDER BY created_at DESC LIMIT 1",
        (provider, model_name),
    ).fetchone()
    c.close()
    return dict(row) if row else None


def seed_default_pricing():
    """初始化默认价格。
    ⚠️ 价格由外部 Agent 每日巡查模型厂商后填入，此处不做硬编码。
    仅创建表结构，不插入种子数据。
    """
    pass  # 价格由外部 Agent 维护，不硬编码
