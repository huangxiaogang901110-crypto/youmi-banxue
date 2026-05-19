# 悠米项目 — Git 多人协作同步规则

> 本规则适用于悠米项目 ECS 上两个开发 agent 的工作区 git 同步操作。
> 公共区 `~/yomi/` 设有 **pre-receive hook 门禁**：无令牌时拒绝所有 push。令牌由 hermes_me 在刚哥明确「开门」指令下创建，push 完成后 post-receive hook 自动销毁令牌并上锁。

## 角色与工作区

| 角色 | 工作区路径 | public区 origin | GitHub remote |
|------|-----------|-----------------|---------------|
| **hermes_me** | `~/yomi-dev/` | origin | github |
| **hermes_colleague** | `/home/hermes_colleague/yomi/` | origin | 无（禁止） |

本规则仅覆盖 **个人工作区 ↔ 公共区 origin** 的同步流程。**公共区 → GitHub 的推送见第五节。**

---

## 〇、Hook 门禁机制（不可绕过）

公共区 `.git/hooks/pre-receive` 拦截所有 push 操作。

```
push 请求 → pre-receive 检查 /tmp/yomi_git_unlocked
              ├── 不存在 → ⛔ 拒绝："公共区 git 已锁定，需刚哥开门"
              └── 存在   → ✅ 通过 → 推送 → post-receive 自动 rm 令牌 → 🔒 上锁
```

**令牌是一次性的**：一次 push 完成后自动销毁。下次要推必须刚哥再说「开门」。

**令牌也约束 hermes_me 自己**：即使是在公共区本地执行 git reset / pull / rebase / merge 等直接操作，也必须先开门。hermes_me 不能绕过门禁。

### ⚠️ 仅靠令牌不够 → 必须用 open-gate.service

公共区 `.git/objects` 属 root:root（`drwxr-xr-x`），hermes_me 无写权限。令牌只过 hook 关卡，**不解决文件系统权限**。直接 push 会报 `unable to create temporary object directory`。

**正确做法**：先启动 `open-gate.service`（oneshot，root 执行），它一次性完成：
1. `chown -R hermes_me:hermes_me /home/hermes_me/yomi/.git/`
2. `chmod -R o+w` 所有 objects/refs/logs（兼容性）
3. 创建令牌 `/tmp/yomi_git_unlocked`
4. 设 5 分钟超时自动关门（`at` 调度 `close-gate.sh`）

```bash
# hermes_me 有 NOPASSWD systemctl
sudo -n /usr/bin/systemctl start open-gate
# → 门已开，5 分钟内 push 即可，push 完 post-receive 自动销毁令牌并上锁
cd ~/yomi-dev && git push origin master
```

开门命令（仅 hermes_me 执行）：
```bash
sudo -n /usr/bin/systemctl start open-gate
```

---

## 一、操作前强制检查

### 1. 确认自己在自己的房间

```bash
pwd
```

- hermes_me **必须**在 `~/yomi-dev/`。不在则 `cd ~/yomi-dev/`，否则停。
- hermes_colleague **必须**在 `/home/hermes_colleague/yomi/`。不在则停。
- ⛔ 严禁在 `~/yomi/`（公共区/生产收口）执行任何 git 写操作。

### 2. 确保工作区干净

```bash
git status
```

- 有未提交改动 → `git stash` 先暂存，操作完再 `git stash pop`。
- 工作区不干净直接 rebase 会报错拒绝。

---

## 二、标准同步流程（工作区 → 公共区 origin）

### 前置：确认令牌

push 到公共区前，**必须**由刚哥下达「开门」指令，hermes_me 创建令牌后通知对方。

### 第一步：探路

```bash
git fetch origin
```

拿到公共区最新快照。**fetch 完必须立刻判断、立刻操作，中间不要做别的事。**

### 第二步：判断是否可以直接 push

```bash
git log origin/main..HEAD --oneline
```

- **无输出** → 公共区无新增，本地可以直接 push。
- **有输出** → 公共区有新增（对方已推送），进入分叉状态，走第三步。

### 第三步：合并对方提交

**推荐 rebase（保持提交线整洁）：**

```bash
git rebase origin/main
```

- 无冲突 → 直接走第四步。
- 有冲突 → 按第三节《冲突处理》执行。

**备选 merge（冲突集中解决）：**

```bash
git merge origin/main
```

### 第四步：推送

```bash
git push origin main
```

- 如果被 hook 拒绝 → 等刚哥开门后重试。
- push 被 reject（fast-forward 冲突）→ 说明 fetch 到 push 之间对方又推了。重走第一步 → 第二步 → 第三步 → 第四步。

---

## 三、冲突处理（按层级递进）

### 第一层：让 Git 告诉你

rebase 停下来时，Git 会显示当前在**处理哪个外来 commit**、在**哪个文件**冲突。

```bash
git log origin/main --oneline -10          # 看对方提交记录
git show <冲突commit_hash>                 # 看那个 commit 的完整改动 + message
git diff                                   # 看双方所有差异
```

**对方的 commit message 写清楚了意图，就能独立判断怎么取舍。**

### 第二层：看不懂，远端问对方

把冲突内容 + 对方 commit message 发群聊。双方都在自己房间操作，靠信息对齐决策。不是闭眼瞎修。

### 第三层：实在不行就 abort + merge

```bash
git rebase --abort          # 回到干净状态
git merge origin/main       # 换 merge 策略
```

rebase 逐 commit 解决心智负担重时，merge 把冲突集中在一个点解决更稳。

### 第四层：传文件对照修（终极手段）

冲突太大、涉及多处文件、远程沟通效率低时，有冲突的一方把相关文件内容发给对方，在自己工作区对照修。

### rebase 中断后的修复

会话超时或终端断开导致 rebase 挂起：

```bash
git rebase --abort      # 清理状态
git status              # 确认干净
# 然后从头走标准同步流程
```

---

## 四、GitHub 推送权限

- **hermes_colleague：严禁 push 到 GitHub**。只能 push 到公共区 origin。
- **hermes_me：仅限刚哥明确指令后执行。** 指令形式如「推到 GitHub」「同步 GitHub」。

hermes_me 执行 GitHub 推送时必须从 `~/yomi-dev/` 发起：

```bash
cd ~/yomi-dev/
git push github main        # github 为工作区配置的 GitHub remote
```

**如果刚哥没发指令，hermes_me 不得主动 push GitHub**，即使公共区已成功同步。

---

## 五、协作纪律

1. **小步提交、频繁同步** — 每次冲突面小到肉眼能判断。
2. **commit message 写清楚意图** — 不能只写 "fix" "update"。对方看到冲突时必须能从 commit message 独立决策。
3. **两个人尽量改不同文件** — 架构上减少碰撞面。
4. **冲突太大不硬搞 rebase** — 果断 abort 换 merge。
5. **禁止 `git pull`** — 用 `git fetch` + `git rebase origin/main`，每一步可检查。
6. **禁止 `git push --force`** — 任何情况下不得 force push。

---

## 六、快速决策表

| 情况 | 操作 |
|---|---|
| push 被 hook 拒绝 | `sudo -n systemctl start open-gate` → 重试 push |
| 本地领先、公共区无新增 | `sudo -n systemctl start open-gate` → `git push origin master` |
| 公共区有新增、无冲突 | `sudo -n systemctl start open-gate` → `git fetch` → `git rebase origin/master` → `git push` |
| 公共区有新增、有冲突、能判断 | 解决冲突 → `git rebase --continue` → `git push` |
| 公共区有新增、有冲突、看不懂 | 群聊问对方 → 按对方指示修 |
| 公共区有新增、冲突太大搞不定 | `git rebase --abort` → `git merge origin/master` |
| push 被 reject（fast-forward）| 重走 fetch → rebase → push |

---

## 七、禁止清单

- ⛔ 在 `~/yomi/` 执行任何 git 写操作
- ⛔ hermes_me 在 `~/yomi/` 直接 git reset / pull / rebase / merge（必须通过令牌门禁）
- ⛔ hermes_me 不在 `~/yomi-dev/` 时执行 git 操作
- ⛔ hermes_colleague 不在 `/home/hermes_colleague/yomi/` 时执行 git 操作
- ⛔ 工作区不干净时直接 rebase
- ⛔ `git pull`（不分步检查）
- ⛔ `git push --force`
- ⛔ fetch 完拖延不操作
- ⛔ rebase 中断后不清理状态继续操作
- ⛔ hermes_colleague push 到 GitHub
- ⛔ hermes_me 未经刚哥指令 push 到 GitHub
- ⛔ hermes_me 未经刚哥「开门」指令创建令牌

---

## 八、Mac 本地拉取（反向隧道 SSH）

### 连接参数

| 参数 | 值 |
|------|-----|
| 端口 | `19922`（反向隧道） |
| 用户 | **`lulu`**（不是 `hermes_me`） |
| 密钥 | `~/.ssh/mac_gate` |
| 项目路径 | `~/AIProjects/yomi` |

### 命令

```bash
# Mac 本地拉取最新代码
ssh -p 19922 -i ~/.ssh/mac_gate -o StrictHostKeyChecking=no lulu@127.0.0.1 \
  "cd ~/AIProjects/yomi && git pull origin master"
```

### ⚠️ 注意
- 用户是 **`lulu`**，不是 `hermes_me`。用错用户名会 `Permission denied`。
- 如果提示 `Permission denied (publickey)`，检查反向隧道是否存活：`ss -tlnp | grep 19922`
- Mac 端 `.git` config 中 GitHub remote 名为 `origin`（ECS 上用 `github`）
