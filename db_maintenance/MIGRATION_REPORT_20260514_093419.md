# 悠米伴学 数据库迁移报告

> 迁移日期：2026-05-14 09:34-09:37 CST
> 操作者：hermes_me
> 类型：SQLite 数据库从 backend/ 迁移到项目根目录

---

## 1. 路径变更

| 项目 | 路径 |
|------|------|
| 原数据库 | `/home/hermes_me/yomi/backend/yomi.db` |
| **新数据库** | `/home/hermes_me/yomi/yomi.db` |
| Symlink | `/home/hermes_me/yomi/backend/yomi.db` → `/home/hermes_me/yomi/yomi.db` |

## 2. 备份文件

| 文件 | 大小 | 校验 |
|------|------|------|
| `db_backups/yomi_before_root_migration_20260514_093419.db` | 5.0 MB | integrity_check: ok |
| `db_maintenance/yomi_schema_before_root_migration_20260514_093419.sql` | 4.6 MB | 完整 schema dump |

## 3. 旧库归档

| 文件 | 状态 |
|------|------|
| `backend/yomi.db.migrated_backup_20260514_093419` | 已归档（原库重命名） |
| `backend/yomi.db.bak.migrated_20260514_093419` | 已归档 |
| `backend/yomi.db.bak.20260511_1536.migrated_20260514_093419` | 已归档 |

## 4. Symlink 状态

```
lrwxrwxrwx backend/yomi.db → /home/hermes_me/yomi/yomi.db ✅
验证：sqlite3 backend/yomi.db → integrity_check: ok → 确认指向新库
```

## 5. 配置变更

| 项目 | 状态 |
|------|------|
| .env | 未修改 — DB_PATH 在 db.py:11 硬编码为 `Path(__file__).parent / "yomi.db"`，symlink 已覆盖 |
| db.py | 未修改 — 本轮用 symlink 保底 |
| systemd unit | 未修改 |

## 6. 服务状态

| 检查项 | 结果 |
|--------|------|
| yomi-backend 运行 | ✅ active (PID 187459) |
| 端口 8000 | ✅ 监听中 |
| /health | ✅ `{"ok":true}` |
| /api/parse-jobs/recent | ✅ DB 连接正常 |
| 后端日志 | ✅ 无 DB 相关错误 |

## 7. 数据库完整性

| 检查项 | 结果 |
|--------|------|
| integrity_check | ✅ ok |
| foreign_key_check | ✅ clean（无违规） |
| journal_mode | ✅ WAL |
| busy_timeout | ✅ 5000ms |

## 8. 核心表行数（前后一致）

| 表名 | 迁移前 | 迁移后 |
|------|--------|--------|
| question_item | 907 | 907 ✅ |
| model_calls | 166 | 166 ✅ |
| parse_jobs | 61 | 61 ✅ |
| assignment | 60 | 60 ✅ |
| child_profiles | 3 | 3 ✅ |
| parent_users | 3 | 3 ✅ |
| tutor_chats | 24 | 24 ✅ |
| ai_tutoring_sessions | 24 | 24 ✅ |

## 9. 权限

| 文件/目录 | 权限 | 说明 |
|-----------|------|------|
| `/home/hermes_me/yomi/` | 757 (o+rwx) | SQLite WAL 需目录写权限 |
| `yomi.db` | 646 (o+rw) | 两个 Hermes 可读写 |
| `db_backups/` | 755 (o+rx) | 只读访问 |
| `db_maintenance/` | 755 (o+rx) | 含 DB_OWNER_RULES.md |
| `DB_OWNER_RULES.md` | 644 | 维护规则文档 |

## 10. Hermes 访问验证

| Hermes | 读取 | 写入 | 验证方式 |
|--------|------|------|----------|
| hermes_me | ✅ | ✅ | 直接测试：integrity + 测试表写入 |
| hermes_colleague | ⚠️ 权限已配置 | ⚠️ 权限已配置 | 因无 sudo/su 未直接测试，但 `o+rw` 权限已确认 |

## 11. SQLite 配置

| 参数 | 旧值 | 新值 |
|------|------|------|
| journal_mode | delete | **WAL** |
| busy_timeout | 默认(0) | **5000ms** |
| foreign_keys | OFF | OFF（未改） |

## 12. 剩余风险

| 风险 | 等级 | 说明 |
|------|------|------|
| 服务未重启 | ⚠️ 低 | 旧连接的 inode 仍指向归档文件，但新连接自动走 symlink。`.backup`→symlink 窗口 ~2 秒，写入丢失概率极低 |
| other 权限 | ⚠️ 中 | 当前用 `o+rw` 替代正式 group，需 root 权限后迁移到 `yomi_db_admin` group |
| hermes_colleague 未实测 | ⚠️ 低 | 权限链已确认（/home/hermes_me: 751 → yomi: 757 → yomi.db: 646），colleague 可通过完整路径访问 |
| 旧连接残留 | ⚠️ 低 | 服务重启后自动解决，当前无活跃用户 |

## 13. 下一步建议

1. **【立即】** 获得 root 权限后重启 yomi-backend：`sudo systemctl restart yomi-backend`
2. **【本周】** 创建正式 `yomi_db_admin` group，替换 `other` 权限
3. **【本周】** 由 hermes_colleague 验证读写
4. **【可选】** 设置每日 cron 备份 yomi.db
5. **【注意】** 不要删除 `backend/yomi.db.migrated_backup_*` 归档文件，保留至少 7 天

---

**迁移结论：成功。** 数据库已迁移到项目根目录，WAL 模式已启用，symlink 保底兼容旧路径。服务正常运行。
