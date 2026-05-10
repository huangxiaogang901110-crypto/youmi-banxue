"""
激活码管理脚本
用法：python3 manage_codes.py create CODE AMOUNT [DAYS]
      python3 manage_codes.py list
"""
import sys
sys.path.insert(0, '/home/hermes_me/.local/lib/python3.10/site-packages')
import db as _db

_db.init()

if len(sys.argv) < 2:
    print("用法: python3 manage_codes.py create CODE AMOUNT [DAYS]")
    print("      python3 manage_codes.py list")
    sys.exit(1)

cmd = sys.argv[1]
if cmd == "create":
    if len(sys.argv) < 4:
        print("用法: python3 manage_codes.py create CODE AMOUNT [DAYS]")
        sys.exit(1)
    code = sys.argv[2]
    amount = int(sys.argv[3])
    days = int(sys.argv[4]) if len(sys.argv) > 4 else 365
    _db.create_activation_code(code, amount, days)
    print(f"✅ 已生成: {code} ({amount} 学豆, {days} 天有效)")
elif cmd == "list":
    import sqlite3
    c = sqlite3.connect(_db.DB_PATH)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT code, credit_amount, status, used_by_parent_user_id, expires_at FROM activation_codes ORDER BY created_at DESC").fetchall()
    for r in rows:
        print(f"  {r['code']:20s} | {r['credit_amount']:4d} 学豆 | {r['status']:8s} | {r['used_by_parent_user_id'] or '-':10s} | 过期: {r['expires_at'] or '无'}")
    c.close()
else:
    print(f"未知命令: {cmd}")
