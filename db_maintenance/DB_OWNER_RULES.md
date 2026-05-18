# 悠米伴学 共享数据库维护规则

> 创建日期：2026-05-14
> 数据库：/home/hermes_me/yomi/yomi.db
> Hermes 用户：hermes_me (uid 1000)、hermes_colleague (uid 1001)

---

## 数据库路径

| 项目 | 路径 |
|------|------|
| 生产数据库 | `/home/hermes_me/yomi/yomi.db` |
| 兼容 symlink | `/home/hermes_me/yomi/backend/yomi.db` → `/home/hermes_me/yomi/yomi.db` |
| 备份目录 | `/home/hermes_me/yomi/db_backups/` |
| 维护文档 | `/home/hermes_me/yomi/db_maintenance/` |
| 维护锁文件 | `/home/hermes_me/yomi/db_maintenance/db_maintenance.lock` |

## 权限机制

当前使用 `other` 权限（`o+rw`）实现两个 Hermes 共享，因为无 root 权限创建正式 group。

**⚠️ 待迁移**：获得 root 权限后，应创建 `yomi_db_admin` group 替换 `other` 权限。

当前权限：
```
/home/hermes_me/yomi/          757 (o+rwx, SQLite WAL 需目录写权限)
/home/hermes_me/yomi/yomi.db   646 (o+rw)
备份/维护目录                   755 (o+rx)
```

## 并发写入保护

### 维护锁规则

以下操作前**必须**创建维护锁文件：
- Schema 迁移（ALTER TABLE / CREATE TABLE / DROP TABLE）
- 批量数据回填（backfill）
- 批量 UPDATE / DELETE（超过 100 行）
- 手动 PRAGMA 修改

```
锁文件：/home/hermes_me/yomi/db_maintenance/db_maintenance.lock
内容格式：YYYY-MM-DD HH:MM:SS hermes_me 锁原因
例：2026-05-14 09:30:00 hermes_me 添加 user_preferences 表
```

操作步骤：
1. 检查锁文件是否存在 → 存在则等待
2. 创建锁文件（含时间戳+操作者+原因）
3. 执行维护操作
4. 删除锁文件

### 允许并发

- 普通 SELECT 查询 → 无限制
- 小写入（INSERT/UPDATE/DELETE ≤100 行） → 无需锁，失败时尊重 `busy_timeout=5000ms`
- 不得无限重试写入

## SQLite 配置

- `journal_mode=WAL` — 支持并发读写
- `busy_timeout=5000` — 5 秒忙等待
- `foreign_keys=OFF` — 当前关闭，打开需评估影响

## 备份策略

- 迁移前备份：`db_backups/yomi_before_root_migration_YYYYMMDD_HHMMSS.db`
- 建议定期备份：每日 cron 或每次部署前
- 备份命令：`python3 -c "import sqlite3; s=sqlite3.connect('yomi.db'); d=sqlite3.connect('backup.db'); s.backup(d)"`

## 安全边界

- ❌ 不给 hermes_colleague root 权限
- ❌ 不给 hermes_colleague 阿里云账号/支付/API Key 管理权限
- ❌ 不删除旧库（只能归档重命名）
- ❌ 不 DROP 核心业务表
- ❌ 不清空任何表（TRUNCATE/DELETE FROM without WHERE）

## 回滚方案

如新库出问题：
```bash
# 1. 停止 yomi-backend
# 2. 删除 symlink
rm /home/hermes_me/yomi/backend/yomi.db
# 3. 恢复归档旧库
mv /home/hermes_me/yomi/backend/yomi.db.migrated_backup_YYYYMMDD_HHMMSS \
   /home/hermes_me/yomi/backend/yomi.db
# 4. 重启服务
```
