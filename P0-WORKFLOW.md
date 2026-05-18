# 悠米伴学 — 项目 P0 级开发原则

> 版本：v1.0 | 制定：刚哥 | 2026-05-14
> 适用：hermes_me、hermes_colleague
> 地位：本文件为项目最高开发流程规则。每轮工作必须**先读本文件**，再开始具体任务。

---

## 一、核心原则

**公共区域是唯一版本收口。** 任何代码改动必须先在自己的房间完成并验证，得到明确指令后才能上传到公共区域。

---

## 二、区域定义

| 区域 | 路径 | 使用者 | 性质 |
|------|------|--------|------|
| 公共区 | `/home/hermes_me/yomi/` | 生产部署 | **唯一版本权威**，yomi-backend 运行于此 |
| 阿石开发区 | `/home/hermes_me/yomi-dev/` | hermes_me | 个人 clone |
| 小张开发区 | `/home/hermes_colleague/yomi/` | hermes_colleague | 个人 clone |
| 生产数据库 | `/srv/yomi/yomi.db` | 共享读写 | 唯一生产库 |

---

## 三、工作流程（强制执行）

### 3.1 开工前：版本对账

**每轮任务第一步**：确认自己房间的代码版本与公共区一致。

```bash
# 1. 查看公共区最新 commit
cd /home/hermes_me/yomi && git log --oneline -1

# 2. 查看自己房间最新 commit
cd ~/yomi-dev && git log --oneline -1   # (hermes_me)
cd ~/yomi && git log --oneline -1       # (hermes_colleague)
```

**对账结果处理**：

| 状态 | 操作 |
|------|------|
| 一致 | 在自己房间直接开工 |
| 不一致 | 以公共区为准，先拉取再开工 |

### 3.2 拉取流程（版本不一致时）

```bash
# 公共区 → GitHub
cd /home/hermes_me/yomi
git push origin master

# GitHub → 自己房间
cd ~/yomi-dev   # (hermes_me 用 ~/yomi-dev)
git pull origin master
```

拉取后再次确认 commit 一致，再开工。

### 3.3 开发阶段

- 所有代码编写、修改、测试在自己房间完成
- 不直接修改公共区文件
- 数据库操作同样在自己的测试副本上验证，不动 `/srv/yomi/yomi.db`

### 3.4 上传流程（得到指令后）

```bash
# 1. 自己房间 push 到 GitHub
cd ~/yomi-dev
git add -A
git commit -m "描述改动"
git push origin master

# 2. 通知刚哥核验
# 刚哥确认后，由刚哥或指定人在公共区执行合并
```

---

## 四、公共区 Git 管理（刚哥 / 指定人执行）

### 4.1 核验原则

- **不得无脑覆盖。** 每次上传必须核验后再合并。
- 公共区 git 是融合了 hermes_me 和 hermes_colleague 双方改动的**最新融合版**。

### 4.2 合并流程

```bash
cd /home/hermes_me/yomi

# 1. 保存当前状态
git stash

# 2. 拉取双方最新
git fetch origin master

# 3. 查看差异
git diff master..origin/master

# 4. 核验通过后合并
git merge origin/master

# 5. 恢复本地未提交修改
git stash pop

# 6. 如有冲突，手动解决后重新提交
```

---

## 五、数据库操作原则（小张全权负责）

小张负责数据库的增、查、改、删操作，但必须遵守：

1. 所有 DB 操作先在 `/srv/yomi/db_backups/` 做完整备份
2. 涉及 DDL（ALTER/CREATE/DROP）必须先创建维护锁：`/srv/yomi/db_maintenance/db_maintenance.lock`
3. 改完验证通过后删除维护锁
4. 详见 `/srv/yomi/db_maintenance/DB_OWNER_RULES.md`

---

## 六、禁止事项

| 禁止 | 说明 |
|------|------|
| 无指令上传 | 未得到刚哥明确指令，不得将代码推到公共区 |
| 直改公共区 | 不得直接在 `/home/hermes_me/yomi/` 下编辑代码 |
| 直改生产库 | 不得未经备份和维护锁直接动 `/srv/yomi/yomi.db` |
| 无脑覆盖 | 合并时不得 `git push --force` 或直接覆盖公共区 |
| 越界操作 | hermes_colleague 不读 hermes_me 的家目录，反之亦然 |
| 泄露密钥 | 不得输出 .env / API Key 明文 |

---

## 七、与本文件冲突时的优先级

1. 刚哥当前明确指令（最高优先）
2. 本文件（P0 级工作流）
3. DB_OWNER_RULES.md（数据库操作规则）
4. 各人 AGENTS.md / SOUL.md

---
