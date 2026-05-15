#!/usr/bin/env python3
"""
悠米伴学 — SQLite → PostgreSQL 迁移脚本
Phase 1 Task 5

用法:
  # 预览 PG DDL（dry run）
  SUPABASE_URL=postgresql://... python3 migrate_to_pg.py --dry-run

  # 执行迁移（创表到 PG）
  SUPABASE_URL=postgresql://... python3 migrate_to_pg.py

  # 仅生成 DDL 文本
  python3 migrate_to_pg.py --ddl-only
"""

import os
import sys

# 确保能导入 backend 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    import db_pg

    dry_run = "--dry-run" in sys.argv
    ddl_only = "--ddl-only" in sys.argv

    if ddl_only:
        ddl = db_pg.generate_pg_migration()
        if not ddl:
            print("⚠️ 无法读取 SQLite schema（可能数据库文件不存在）")
            sys.exit(1)
        print(ddl)
        return

    # 检查 PG 可用性
    if not db_pg.pg_available():
        print("❌ PostgreSQL 不可用")
        print("  请先设置环境变量: SUPABASE_URL=postgresql://user:pass@host:5432/db")
        print("  并安装依赖: pip install psycopg2-binary")
        sys.exit(1)

    url = db_pg.get_pg_url()
    # 隐藏密码显示
    safe_url = url.replace(url.split("@")[0].split(":")[-1], "***") if "@" in url else url
    print(f"🔗 PG: {safe_url}")
    print(f"🏷️  模式: {'DRY RUN (预览 DDL)' if dry_run else '执行迁移'}")

    result = db_pg.migrate_to_pg(dry_run=dry_run)
    if result["ok"]:
        if dry_run:
            print(f"\n✅ DDL 预览（{result['tables']} 张表）:\n")
            print(result["ddl"])
        else:
            print(f"\n✅ {result['message']}")
    else:
        print(f"\n❌ 迁移失败: {result['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
