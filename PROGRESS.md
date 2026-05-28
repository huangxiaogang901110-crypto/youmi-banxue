# 悠米伴学 — 工程进度

> 由 Hermes me 维护。洁癖式存档，仅保留当前基准。

## 当前基准（2026-05-27）

| 维度 | 数值 |
|------|------|
| 仓库 | `huangxiaogang901110-crypto/youmi-banxue` |
| origin/master | `0eb36d5` — merge: repair3d math OCR-first grayscale path |
| 本地 master | `0eb36d5` — 与 origin/master 完全同步 |
| Codex 专项领先公共区 | 41 提交（公共区 `~/yomi/` 停在 `33e9293`，05-22） |
| 后端代码 | 5,310 行 / 17 文件（FastAPI） |
| 前端代码 | 7,052 行 / 50 文件（Next.js 14 + React 19） |
| 数据库 | SQLite 33 表，`question_item` 1,747 条 |
| 用户 | 3 位家长 + 3 个孩子 |
| 测试 | v4 后端基础 8 + v5 主链路 1 + v6 前端 E2E 8 = **9/9 全绿** |
| 运行时 | 后端 :8000 ✅ / :8001 ❌ 未运行 |
| DeepSeek | 悠米 Key ¥99.61 / Hermes 开发 Key ¥9.67 ⚠️ 低于 ¥10 |
| 折扣到期 | 2026-05-31（75% 折扣结束 → 4 倍价） |

---

## 已完成功能（全量）

### 核心链路 ✅
- 上传（拍照/贴文本）→ Qwen-VL 全图识题（优先）→ OCR+切题（回落）→ AI 辅导（DeepSeek）→ 错题记录
- JWT 鉴权 + 种子用户（13800138000/123456）
- 权益系统 + 激活码 + 学豆扣减
- 孩子答案识别 + 数学合规校验

### 作业系统 ✅
- 微信群作业清单（文本粘贴 → DeepSeek 解析）
- 日期化管理（同天合并、跨天隔离、历史只读）
- 作业清单后端持久化（抗清缓存）

### 错题库 ✅
- 多级分类（数学/语文/英语 → 子类）
- 加减法数值范围自动分流
- 加入/移出 + 前端页面

### 其他 ✅
- 识图切题历史持久化（三层兜底）
- 用户侧成本汇总表
- 上传白屏修复 + 处理进度防回退
- 密集无题号算式切题

---

## Codex 专项 — Repair 系列（2026-05-27）

### Repair 进展：3A → 3J 已全入 master

| 阶段 | 内容 | 状态 |
|------|------|------|
| 3A | JSON fixture 基础 + loader | ✅ merged |
| 3B | 文档分类器质量改善 | ✅ merged |
| 3C | Fixture 覆盖扩展 + 统计卫生修复 | ✅ merged |
| 3D | Real eval runner + math OCR-first grayscale | ✅ merged |
| 3G/3H | 真实样本计划 + preprod review | ✅ merged |
| 3I | Qwen-VL timeout 修复 | ✅ merged |
| 3J | Evaluator 契约 + 安全门指标 | ✅ merged |

### Math OCR-first B++ 灰度路径

- 数学竖式计算优先走 OCR 规则判题（零 API 成本）
- 通用 OCR blocks → 数学 block 过滤 → 答案区定位 → 质量门验证
- 当前处于候选状态，待公共区部署后实装

### 3D Math OCR Probe 质量门

| 指标 | 数值 | 阈值 | 通过 |
|------|------|------|------|
| Candidate questions | 29 | ≥28 | ✅ |
| Answer bbox | 25 / 29 (86.2%) | ≥80% | ✅ |
| Meta 误判 | 0 | ≤2 | ✅ |
| Header 当题 | False | False | ✅ |
| **质量门** | — | — | **✅ 全部通过** |

### 边界说明

- 公共区 `~/yomi/` 仍落后 Codex master 41 提交，不等于 GitHub 未 push
- GitHub master = `0eb36d5`，与本地完全同步
- 未部署生产、未重启服务、本轮未触发真实模型调用
- 本轮仅做文档/评估报告收口

---

## 测试体系（三层）

| 层级 | 文件 | 覆盖范围 | 状态 |
|------|------|----------|------|
| v4 后端基础 | `tests/test_api.py` | health/login/register/鉴权/错题 8 个 | ✅ |
| v5 主链路 | `tests/test_homework_flow.py` | 上传→解析→切题 1 个 | ✅ |
| v6 前端 E2E | `e2e/yomi-front-flow.spec.ts` | 工作台/上传/识别/清单/切题/解析/缓存 8 场景 | ✅ |
| 运行命令 | `backend/run-tests.sh` | Python + Playwright 一键 | ✅ |
| 触发规则 | 修改 `backend/*.py` 或前端源码后自动跑全量 | | ✅ |

---

## 数据库现状

| 表 | 行数 | 说明 |
|----|------|------|
| question_item | 1,747 | 核心题库（全部 web_upload） |
| model_calls | 341 | AI 调用追踪 |
| ai_tutoring_chat | 196 | 辅导对话 |
| ai_tutoring_messages | 196 | 辅导消息 |
| parse_jobs | 110 | 解析任务 |
| image_registry | 108 | 图片注册 |
| assignment | 108 | 作业记录 |
| credit_ledger | 98 | 学豆流水 |
| ai_tutoring_sessions | 44 | 辅导会话 |
| tutor_chats | 44 | 辅导历史 |
| question_attempt | 12 | 孩子答题记录 |
| mistake_book_item | 1 | ⚠️ 错题（仅测试数据） |

---

## 代码健康（2026-05-15 评价）⚠️

### 🚩 最大隐患：main.py 单体巨石

```
main.py  2,192 行  31 个路由处理器  0 个 APIRouter  仅 8 个 def 函数
```

所有业务逻辑（上传/切题/错题/支付/AI辅导/用户/报告）塞在一个文件，零模块拆分。

### 前端大文件

| 文件 | 行数 |
|------|------|
| `workspace/page.tsx` | 841 |
| `lib/api.ts` | 473 |
| `upload/page.tsx` | 467 |

TypeScript 类型安全 ⭐⭐⭐⭐⭐（仅 1 个 `:any`），但模块化 ⭐⭐。

---

## 下一步（按优先级）

| 优先级 | 任务 | 类型 |
|--------|------|------|
| 🔴 P0 | **拆分 main.py** — 按功能域拆 router 模块 | 工程债 |
| 🔴 P0 | **错题自动入库** — 辅导完成后自动写入 mistake_book_item | 功能 |
| 🟡 P1 | 错题间隔复习 — next_review_at + 今日待复习入口 | 功能 |
| 🟡 P1 | 前端 E2E CI/CD 自动触发 | 工程 |
| 🟢 P2 | 错题统计面板 — 知识点分类可视化 | 功能 |
| 🟢 P2 | 结构化日志 — print → Python logging | 工程 |
| ⏸️ 后置 | 激活码核销（用户要求暂缓） | 功能 |
| ⏸️ 暂停 | 登录/注册页 UI 对齐（Mac 隧道断开中） | UI |

---

## 禁则

- ❌ 不在公区 `~/yomi/` 直接改文件
- ❌ 不自动 push 到 GitHub
- ❌ 不自动同步公区（必须等「同步」指令）
- ❌ 不连接生产库 `/srv/yomi/yomi.db` 跑测试
- ❌ 不测 :8000 / :3000 生产端口
- ❌ 不在前端用 emoji（用 SVG/PNG）
- ❌ 代码不含密钥/凭证

---

## 关键边界

- 开发区：`~/yomi-dev/`（可读可写，唯一工作区）
- 公共区：`~/yomi/`（只读，root:root 归属）
- 生产 DB：`/srv/yomi/yomi.db`
- 测试目标：8001（测试后端）+ 3001（开发前端）
- GitHub：huangxiaogang901110-crypto/youmi-banxue
- 同事：hermes_colleague，开发区 `/home/hermes_colleague/yomi/`（700 隔离）
- 公共区同步：走 GitHub 核验，开门→办事→关门→巡检

---

*最后更新：2026-05-27 — Hermes me 文档收口*
