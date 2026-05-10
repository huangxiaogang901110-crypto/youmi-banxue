"""
model_call_log 归档脚本
将 90 天前的日志从 model_calls 表移到 model_calls_archive 表。
用法：python3 archive_model_logs.py
建议 cron：0 3 * * * cd /home/hermes_me/yomi/backend && python3 archive_model_logs.py
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "yomi.db"
ARCHIVE_DAYS = 90


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # 创建归档表（如果不存在）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_calls_archive (
            id TEXT PRIMARY KEY,
            task_id TEXT DEFAULT '',
            feature_code TEXT DEFAULT '',
            data TEXT NOT NULL,
            created_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_archive_created ON model_calls_archive(created_at)")

    # 移动 90 天前的记录
    cursor = conn.execute(
        f"SELECT id, task_id, feature_code, data, created_at FROM model_calls "
        f"WHERE created_at < datetime('now', '-{ARCHIVE_DAYS} days')"
    )
    rows = cursor.fetchall()
    if not rows:
        print(f"[归档] 无过期记录（{ARCHIVE_DAYS} 天前）")
        conn.close()
        return

    for row in rows:
        conn.execute(
            "INSERT OR IGNORE INTO model_calls_archive (id, task_id, feature_code, data, created_at) VALUES (?, ?, ?, ?, ?)",
            (row["id"], row["task_id"], row["feature_code"], row["data"], row["created_at"]),
        )
    conn.execute(
        f"DELETE FROM model_calls WHERE created_at < datetime('now', '-{ARCHIVE_DAYS} days')"
    )
    conn.commit()
    print(f"[归档] 已移动 {len(rows)} 条记录到 model_calls_archive（{ARCHIVE_DAYS} 天前）")
    conn.close()


if __name__ == "__main__":
    main()
