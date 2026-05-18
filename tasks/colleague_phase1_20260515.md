# 小张 — Phase 1 后端任务清单（2026-05-15）

> 基准文档：`~/yomi/` 公共区施工基准
> 当前状态：Phase 0 全部完成 ✅，进入 Phase 1 小范围内测

---

## 🔴 P0 — 真实 AI 解析（替换 Mock）

当前主链路 7 个 API 全部是 Mock 返回。目标：接入真实大模型。

### 1. DeepSeek 辅导调度

- **端点**：`POST /api/tutor`（当前 Mock）
- **目标**：调用 DeepSeek API（悠米项目 key），返回真实辅导内容
- **关键**：
  - 价格快照写入 `pricing_snapshot` 字段（JSON: model/input_price/output_price/total）
  - 每次调用写入 `model_calls` 表（已标准化 `feature_code`）
  - 超时 30s，失败优雅降级（fallback 提示，不崩服务）
  - prompt 用施工基准里已定好的 system prompt

### 2. Qwen-VL 视觉解析

- **端点**：`POST /api/parse-jobs`（当前 Mock）→ 异步任务：enhance → OCR → cutting → vision
- **目标**：接 Qwen-VL（DashScope），实现真实 OCR + 切题 + 识别
- **关键**：
  - OSS 上传图片后用签名 URL 喂 Qwen-VL（不传 base64）
  - 切题坐标写入 `bbox` 字段
  - 异步任务状态机完整：`uploaded → enhancing → ocr → cutting → vision → validating → completed`

### 3. 作业文本解析（v4-flash）

- **端点**：`POST /api/homework/parse`（当前 Mock）
- **目标**：接 DeepSeek 语义解析微信群作业文本
- **关键**：输出结构化科目+条目列表，替代当前正则规则

---

## 🔴 P0 — 账号/Child 鉴权

### 4. child 表设计 + JWT

- **目标**：真实账号体系替代 `demo_child_001` 占位
- **关键**：
  - 表结构：`children(id, parent_id, name, grade, jwt_secret, created_at)`
  - JWT 生成/验证中间件（FastAPI dependency）
  - `X-Child-Id` Header → JWT 校验 → 注入 request.state
  - 不存明文密码，不做注册/登录（Phase 1 仅鉴权链路）

---

## 🟡 P1 — 云端数据库

### 5. PostgreSQL/Supabase 迁移

- **目标**：从 SQLite 单文件迁移到 PostgreSQL
- **关键**：
  - 表结构不变，SQLite → PG DDL 适配
  - Supabase 免费层 500MB，够内测用
  - 保留 SQLite 作为 local fallback

---

## 开工顺序

```
1 → 2 → 4 → 3 → 5
（AI辅导 → 视觉解析 → 鉴权 → 作业解析 → 云库）
```

先做 1，因为依赖最少、能最快看到真实效果。

---

## 规则提醒

- 全部在自己的房间（`/home/hermes_colleague/yomi/`）操作
- push 到公共区前群里喊刚哥「开门」
- 公共区 Git 规则见：`cat ~/yomi/GIT_SYNC_RULES.md`
