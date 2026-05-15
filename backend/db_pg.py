"""
悠米伴学 — PostgreSQL/Supabase 适配层
Phase 1 Task 5: SQLite → PG 迁移，保留 SQLite fallback

使用方式：
  SUPABASE_URL=postgresql://user:pass@host:5432/db python3 main.py
  不设 SUPABASE_URL → 自动走 SQLite
"""

import os
import sqlite3
import threading

# 懒加载 psycopg2（未安装时 PG 不可用，不报错）
_psycopg2 = None
_PG_AVAILABLE = False

def _try_import_psycopg2():
    global _psycopg2, _PG_AVAILABLE
    if _psycopg2 is not None:
        return _PG_AVAILABLE
    try:
        import psycopg2
        import psycopg2.extras
        _psycopg2 = psycopg2
        _PG_AVAILABLE = True
    except ImportError:
        _PG_AVAILABLE = False
    return _PG_AVAILABLE

# ─── PG 连接池（单连接，Phase 1 够用）─────────────────────

_pg_conn = None
_pg_lock = threading.Lock()

def get_pg_url() -> str:
    """从环境变量获取 PG 连接串。"""
    return os.getenv("SUPABASE_URL", "") or os.getenv("DATABASE_URL", "")

def _pg_connect():
    """建立 PG 连接（线程安全）。"""
    global _pg_conn
    url = get_pg_url()
    if not url:
        return None
    if not _try_import_psycopg2():
        return None
    with _pg_lock:
        if _pg_conn is None or _pg_conn.closed:
            try:
                _pg_conn = _psycopg2.connect(url)
                _pg_conn.autocommit = True
                print("[db_pg] PostgreSQL connected")
            except Exception as e:
                print(f"[db_pg] PG connect failed: {e}, fallback to SQLite")
                _pg_conn = None
    return _pg_conn if _pg_conn and not _pg_conn.closed else None

def pg_available() -> bool:
    """PG 是否已配置且可用。"""
    return bool(get_pg_url()) and _try_import_psycopg2()

def pg_conn():
    """获取 PG 连接（首次调用时建立）。"""
    conn = _pg_conn
    if conn and not conn.closed:
        return conn
    return _pg_connect()

def pg_execute(sql: str, params: tuple = ()):
    """执行 PG SQL，返回 cursor。失败回退 SQLite。"""
    conn = pg_conn()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur
    except Exception as e:
        print(f"[db_pg] execute failed: {e}")
        return None

def pg_fetchone(sql: str, params: tuple = ()):
    cur = pg_execute(sql, params)
    if cur:
        row = cur.fetchone()
        cur.close()
        return row
    return None

def pg_fetchall(sql: str, params: tuple = ()):
    cur = pg_execute(sql, params)
    if cur:
        rows = cur.fetchall()
        cur.close()
        return rows
    return []

def pg_commit():
    conn = pg_conn()
    if conn:
        try:
            conn.commit()
        except Exception as e:
            print(f"[db_pg] commit failed: {e}")

# ─── PG DDL 生成 ──────────────────────────────────────────

def sqlite_to_pg_ddl(sqlite_ddl: str) -> str:
    """将 SQLite CREATE TABLE 转为 PostgreSQL 兼容 DDL。

    转换规则：
    - INTEGER PRIMARY KEY → SERIAL PRIMARY KEY
    - TEXT DEFAULT (datetime(...)) → TIMESTAMPTZ DEFAULT NOW()
    - TEXT → TEXT（不变）
    - REAL → DOUBLE PRECISION
    - 去掉 SQLite 特有的 AUTOINCREMENT（SERIAL 已自带）
    """
    import re

    ddl = sqlite_ddl

    # 去掉 SQLite 特有的 IF NOT EXISTS（PG 也支持）
    # AUTOINCREMENT → 去掉（SERIAL 自带）
    ddl = re.sub(r'\bAUTOINCREMENT\b', '', ddl, flags=re.IGNORECASE)

    # INTEGER PRIMARY KEY → SERIAL PRIMARY KEY
    ddl = re.sub(r'\bINTEGER\s+PRIMARY\s+KEY\b', 'SERIAL PRIMARY KEY', ddl, flags=re.IGNORECASE)

    # 其他 INTEGER → INTEGER（PG 兼容）
    # REAL → DOUBLE PRECISION
    ddl = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', ddl, flags=re.IGNORECASE)

    # TEXT DEFAULT (datetime('now')) → TIMESTAMPTZ DEFAULT NOW()
    ddl = re.sub(
        r"TEXT\s+DEFAULT\s*\(\s*datetime\s*\(\s*'now'\s*\)\s*\)",
        "TIMESTAMPTZ DEFAULT NOW()",
        ddl,
        flags=re.IGNORECASE,
    )
    ddl = re.sub(
        r"TEXT\s+DEFAULT\s*datetime\s*\(\s*'now'\s*\)",
        "TIMESTAMPTZ DEFAULT NOW()",
        ddl,
        flags=re.IGNORECASE,
    )

    # 其他 TEXT DEFAULT '...' → TEXT DEFAULT '...'（不变）
    # TEXT → TEXT（不变）

    # 清理多余空格
    ddl = re.sub(r' +', ' ', ddl)
    ddl = re.sub(r'\s*,\s*', ', ', ddl)

    return ddl

def generate_pg_migration() -> str:
    """从当前 SQLite schema 生成 PG DDL。"""
    sqlite_path = os.path.join(os.path.dirname(__file__), "yomi.db")
    tables = []
    try:
        conn = sqlite3.connect(sqlite_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        conn.close()
        for row in rows:
            if row["sql"]:
                tables.append(row["sql"])
    except Exception as e:
        print(f"[db_pg] Can't read SQLite schema: {e}")
        return ""

    pg_ddls = []
    for sql in tables:
        pg = sqlite_to_pg_ddl(sql)
        pg_ddls.append(pg)

    return "\n\n".join(pg_ddls)

# ─── 迁移执行 ────────────────────────────────────────────

def migrate_to_pg(dry_run: bool = False):
    """将 SQLite 数据迁移到 PostgreSQL。"""
    if not pg_available():
        return {"ok": False, "error": "PG 不可用，请设置 SUPABASE_URL 并安装 psycopg2"}

    ddl = generate_pg_migration()
    if not ddl:
        return {"ok": False, "error": "无法读取 SQLite schema"}

    if dry_run:
        return {"ok": True, "ddl": ddl, "tables": ddl.count("CREATE TABLE")}

    conn = pg_conn()
    if not conn:
        return {"ok": False, "error": "PG 连接失败"}

    try:
        cur = conn.cursor()
        # 逐条执行 CREATE TABLE（IF NOT EXISTS 安全）
        for stmt in ddl.split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt + ";")
        conn.commit()
        cur.close()
        return {"ok": True, "message": "PG 表结构已创建"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
