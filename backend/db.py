"""
悠米伴学 SQLite 持久化 — Phase 0 最小方案
4 张表：parse_jobs / model_calls / tutor_chats / credit_balances
复杂字段用 JSON 存，避免重 Schema 设计。
"""
import json
import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "yomi.db"


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init():
    """建表（幂等）"""
    c = _conn()
    c.executescript("""
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
    """)
    c.commit()
    c.close()


def save_job(job_id: str, data: dict):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO parse_jobs (job_id, data, updated_at) VALUES (?, ?, datetime('now'))",
        (job_id, json.dumps(data, default=str)),
    )
    c.commit()
    c.close()


def save_model_call(data: dict):
    c = _conn()
    call_id = data.get("id", "")
    c.execute(
        "INSERT OR REPLACE INTO model_calls (id, data) VALUES (?, ?)",
        (call_id, json.dumps(data, default=str)),
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

    c.execute("SELECT job_id, data FROM parse_jobs")
    jobs = {row["job_id"]: json.loads(row["data"]) for row in c.fetchall()}

    c.execute("SELECT data FROM model_calls ORDER BY created_at")
    model_calls = [json.loads(row["data"]) for row in c.fetchall()]

    c.execute("SELECT question_id, history FROM tutor_chats")
    tutor_chats = {row["question_id"]: json.loads(row["history"]) for row in c.fetchall()}

    c.execute("SELECT child_id, balance FROM credit_balances")
    credit_balances = {row["child_id"]: row["balance"] for row in c.fetchall()}

    c.close()
    return jobs, model_calls, tutor_chats, credit_balances
