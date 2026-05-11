"""
悠米伴学 SQLite 持久化 — Phase 1
原 4 表保留（JSON 双写兼容）+ 新增商用表 + 索引 + 图片过期
"""
import json
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
        # model_calls 查询加速
        "CREATE INDEX IF NOT EXISTS idx_model_calls_created ON model_calls(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_model_calls_feature ON model_calls(feature_code)",
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
    c.execute(
        "INSERT OR REPLACE INTO parse_jobs (job_id, child_id, parent_id, client_task_id, data, updated_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (job_id, child_id, parent_id, client_task_id, json.dumps(data, default=str)),
    )
    c.commit()
    c.close()


def save_model_call(data: dict):
    c = _conn()
    call_id = data.get("id", "")
    task_id = data.get("task_id", "")
    feature_code = data.get("feature_code", "")
    prompt_version = data.get("prompt_version", data.get("prompt_name", ""))
    schema_version = data.get("schema_version", "")
    credit_cost = data.get("credit_cost", data.get("estimated_cost", 0.0))
    retry_count = data.get("retry_count", 0)
    c.execute(
        "INSERT OR REPLACE INTO model_calls (id, task_id, feature_code, prompt_version, schema_version, credit_cost, retry_count, data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (call_id, task_id, feature_code, prompt_version, schema_version, float(credit_cost), int(retry_count), json.dumps(data, default=str)),
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


def save_credit_balance(child_id: str, balance: int):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO credit_balances (child_id, balance, updated_at) VALUES (?, ?, datetime('now'))",
        (child_id, balance),
    )
    c.commit()
    c.close()


def load_all():
    """启动时从 SQLite 恢复到内存"""
    c = _conn()

    rows = c.execute("SELECT job_id, data FROM parse_jobs").fetchall()
    jobs = {row["job_id"]: json.loads(row["data"]) for row in rows}

    rows = c.execute("SELECT data FROM model_calls ORDER BY created_at").fetchall()
    model_calls = [json.loads(row["data"]) for row in rows]

    rows = c.execute("SELECT question_id, history FROM tutor_chats").fetchall()
    tutor_chats = {row["question_id"]: json.loads(row["history"]) for row in rows}

    rows = c.execute("SELECT child_id, balance FROM credit_balances").fetchall()
    credit_balances = {row["child_id"]: row["balance"] for row in rows}

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
    c = _conn()
    c.execute("UPDATE mistake_book_item SET deleted_at = datetime('now') WHERE id = ?", (mistake_id,))
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

def create_question_item(qid: str, assignment_id: str, page_id: str, question_no: int, question_text: str, bbox: list, visual_description: str = ""):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO question_item (id, assignment_id, page_id, question_no, bbox_json, question_text, visual_description) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (qid, assignment_id, page_id, question_no, json.dumps(bbox or []), question_text, visual_description or ""),
    )
    c.commit()
    c.close()
    return qid

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

def add_credit_ledger_entry(parent_user_id: str, child_id: str, amount: int, reason_code: str, reason_desc: str = "", related_id: str = ""):
    "写学豆流水"
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
        "INSERT INTO credit_ledger (id, parent_user_id, child_id, change_amount, balance_after, reason_code, reason_desc, related_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex[:12], parent_user_id, child_id, amount, new_balance, reason_code, reason_desc, related_id),
    )
    c.commit()
    c.close()
    return new_balance
