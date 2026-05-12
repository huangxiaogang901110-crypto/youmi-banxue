# 悠米伴学 — 工程进度

> 由 Hermes（阿石）维护。ECS 上项目文件，不提交 git。

## 当前状态

- 项目：悠米伴学 (Youmi Companion Learning)
- 阶段：**Phase 1 进行中**（Phase 0 ✅，P0-Repair-A ✅）
- 最后更新：2026-05-12 23:30 CST
- 部署：ECS 39.107.119.136，Nginx 静态 `/out` + FastAPI `:8000`
- 构建：Next.js 16.2.6 (Node v20.18.1)
- 数据库：SQLite + ECS 系统盘

---

## Phase 0 主链路

| # | 任务 | 状态 | 备注 |
|---|------|------|------|
| 1 | Next.js 骨架 + shadcn/ui + 设计 Token | ✅ 完成 | npm run dev → 200 OK |
| 2 | typed fetch client + 全部接口封装 + Mock | ✅ 完成 | types.ts + api.ts，含 homeworkApi |
| 3 | FastAPI 后端骨架 + Mock 端点 | ✅ 完成 | 8 个接口全部验证通过 |
| 4 | 页面布局：Design Tokens + TabBar + 6 路由 + 通用组件 | ✅ 完成 | build 零错误 |
| 5 | 上传页：图片压缩 + client_task_id + multipart + 状态机 | ✅ 完成 | build 零错误 |
| 6 | 解析状态页：轮询 + bbox 渲染 + workspace 集成 | ✅ 完成 | build 零错误 |
| 7 | AI 辅导页：假打字机 + KaTeX + IndexedDB + 视觉二次路由 | ✅ 完成 | build 零错误 |
| 8 | 权益系统 + 激活码弹层 + 额度拦截 + 错因记录 | ✅ 完成 | build 零错误 |
| 9 | 端到端联调：7 API + 6 页面 200 + 错误态 + 文案 + build | ✅ 完成 | 零错误 |

---

## P0-Repair-A：微信群作业清单

> 补齐 Phase 0 产品闭环缺口。2026-05-09 启动 → 2026-05-10 全部完成。

| # | 任务 | 状态 | 备注 |
|---|------|------|------|
| R1 | 作业入口 + 页面骨架 | ✅ 完成 | 合并到 `/upload` 页面，顶部「贴文本」「拍照片」双 Tab |
| R2 | 后端 POST /api/homework/parse Mock | ✅ 完成 | 支持「语文:背诵第12课」等微信格式 |
| R3 | 前端 types.ts + api.ts 补接口 | ✅ 完成 | HomeworkSubject + HomeworkParseData + homeworkApi |
| R4 | TextPaste 组件 | ✅ 完成 | 粘贴框 + 发送 + loading + 错误，集成在 upload 页 |
| R5 | HomeworkList + 勾选 + 进度条 | ✅ 完成 | 按科目分组，科目级进度小条、任务删除、只读模式 |
| R6 | 状态持久化 | ✅ 完成 | 日期化管理：同天合并去重、跨天隔离、历史只读、跨天复制 |
| R7 | 首页集成 | ✅ 完成 | 首页"识别作业"入口卡片 |
| R8 | 构建 + 联调 + 部署 | ✅ 完成 | TabBar 5 个标签，build 零错误 |
| R9 | DeepSeek 作业文本解析 | ✅ 完成 | 2026-05-11 接入 deepseek-v4-flash，替换 Mock 正则解析 |
| R10 | 日期化管理 + 进度 + 空态 | ✅ 完成 | 2026-05-11 useHomeworkDays hook，完整产品闭环 |

### 设计决策
- **TabBar 保持 5 个**：不新增"作业"标签，作业清单合并入 `/upload` 页面的双 Tab 切换
- **二次粘贴策略**：P0 精确文本匹配去重，P1 接 v4-flash 语义解析
- **主页入口**：首页卡片"识别作业"跳转 `/upload?tab=text`

---

## 技术栈

| 模块 | 版本 |
|------|------|
| Next.js | 16.2.6 |
| React | 19.2.4 |
| Tailwind CSS | v4 |
| shadcn/ui | latest |
| TanStack Query | ✅ |
| Zustand | ✅ |
| Node.js (ECS) | v20.18.1 (/home/hermes_me/local/bin/node) |
| npm registry | npmmirror.com |

---

## 日志

### 2026-05-11

- ✅ 识别管线升级：Qwen-VL 全图识题优先（qwen-vl-max），OCR 回落。成本降 5×，延迟降 50%
- ✅ DeepSeek v4-flash 作业文本解析接入 `/api/homework/parse`，Mock 回落
- ✅ 作业清单日期化管理：同天合并去重、跨天隔离、历史只读、跨天复制、任务删除、进度可视化、空态引导
- ⚠️ 踩坑：Qwen-VL 返回 `"VOCABULARY 1"` 非数字题号 → int() 崩溃 → 修复 try/except 容错
- ⚠️ 踩坑：`save_result` 替换 `_jobs[jid]` 丢失 `poll_count` → 前端轮询死循环 → 修复保底
- ⚠️ 踩坑：DeepSeek prompt 把编号列表拆成多科 → 修复「先判断单科/多科」规则
- ⚠️ 踩坑：`useParseJobPolling` 未识别 `ok:false` → 永远转圈 → 修复显式检查
- ⚠️ 踩坑：轮询/结果态无上传入口 → 加「新上传」按钮
- 📄 新建基准文档：`识别管线说明.md`、`作业清单日期化管理说明.md`

### 2026-05-10

- ✅ 任务 10 完成：JWT 鉴权 — 后端 auth.py + /auth/login|register|children 端点 + 前端 login 页
- ✅ typedFetch 自动注入 Authorization header，替换所有 demo_child_001
- ✅ 种子用户：13800138000 / 123456 → 家长「测试家长」+ 孩子「小明」
- ✅ 任务 9 完成：DB schema — raw sqlite3，4 表 JSON 存储，内存双写 DB
- ✅ 数据库策略修正：确认 SQLite+ESSD 为最终方案，取消 PostgreSQL 迁移。另写 `数据库策略修正说明.md` 覆盖施工基准
- ✅ 代码审查：修复 2 个 🔴 bug（`log_entry` 未定义 + `_persist_job` SQLAlchemy 残留死代码），删除冗余 `init_db()` 调用
- ✅ 任务 8 完成：DeepSeek 辅导客户端 + 对话上下文 + /tutor 端点
- ✅ 基准对表修复：schema_validating 状态、needs_review 触发、enqueue/worker/save 三边界拆分
- ✅ 全量自测通过：6 路由 200，404 正确，API health OK，前端构建零错误
- ✅ 接入真实 API key：QWEN_DASHSCOPE_API_KEY + DEEPSEEK_API_KEY 已写入 .env，替换占位值
- ✅ MOCK_MODE=false，前端已切换到真实 API 模式
- ✅ 🔴 修复 worker_process_job 巨型内嵌函数 → 模块级 + 显式 enqueue_parse_job 边界
- ⚠️ 踩坑：.next/、out/、__pycache__/ root 遗留权限，构建报 EACCES；用 root key SSH 本地修复
- ⚠️ 踩坑：Python 内嵌函数提升缩进混乱，逐行 `line[8:]` 修复

### 2026-05-09
- ✅ 任务 1 完成：项目骨架搭建、依赖安装、shadcn/ui 初始化、dev server 200 OK
- ⚠️ 踩坑：npm 走搬瓦工代理极慢，改用淘宝镜像 1 分钟装完
- ⚠️ 踩坑：shadcn v4 镜像版有 babel 依赖 bug，init 时切回官方源
- ⚠️ 踩坑：Next.js 16 + Google Fonts 有 Turbopack 兼容问题，暂用系统字体
- ✅ 任务 2-9 全部完成，端到端联调通过

---

## Phase 0 真实 AI 链路

> 替换 Mock，接入真实 OCR / Qwen-VL / DeepSeek。严格遵守基准 Table 11 模型分工。

| # | 任务 | 状态 | 备注 |
|---|------|------|------|
| 5 | 接入阿里云 OCR | ✅ 完成 | 教育 OCR 作为回落路径，非主力 |
| 6 | 智能切题（题号规则+版面规则） | ✅ 完成 | 仅 OCR 回落时使用，详见 `切题算法说明.md` |
| 7 | Qwen-VL 视觉理解 | ✅ 完成 | **2026-05-11 升级**：模型 `qwen-vl-max`，新增全图识题 `extract_questions()` 为主力管线 |
| 8 | DeepSeek 辅导讲解 | ✅ 完成 | `deepseek_client.py`：空 key 优雅降级 |
| 9 | DB schema | ✅ 完成 | SQLite 持久化 |

### 管线架构（2026-05-11 更新）

```
Qwen-VL 全图识题 (优先) ──→ 直接出题 ──→ 校验 → 保存
         │
         ▼ 失败
    OCR → 切题 → 逐题 Qwen-VL ──→ 校验 → 保存
```

| 指标 | Qwen-VL 优先 | OCR 回落 |
|------|-------------|----------|
| 20题延迟 | ~21s | ~30-50s |
| 20题成本 | **¥0.011** | ¥0.057 |
| 模型 | qwen-vl-max | 教育OCR + qwen-vl-max |

> 详见 `识别管线说明.md`

---

## Phase 1 进度

| # | 任务 | 状态 | 备注 |
|---|------|------|------|
| 1 | 账号/child 鉴权 | ✅ 完成 | JWT 后端鉴权 + 去硬编码 + 工作台显示孩子名 |
| 2 | Bug 修复（历史记录/摄像头/异步时序等） | ✅ 完成 | 多轮修复，Vitest 测试基础设施已建立 |
| 3 | Debug 子 agent 体系 | ✅ 完成 | debug-subagent 技能 + 规则焊入 AGENTS.md |
| 4 | vision_client extract_questions 漏 usage | ✅ 修复 | 今日修复，DB token 记录恢复正常 |
| 5 | API 限流 | 🟡 进行中 | slowapi 已装，4 端点已限流，待补全 |
| 6 | 结构化日志 | 🟡 待开始 | 当前 41 处 print()，待替换为 logging |
| 7 | OSS 对象存储 | 🟢 已有基础 | oss_client.py 已写，待验证 + 7 天生命周期 |
| 8 | 激活码核销 | ⏸️ 后置 | 用户要求暂缓 |
| 9 | 登录/注册页对齐基准图 | ⏸️ 暂停 | Mac 隧道断开，待恢复后继续 |

## 下一步

| 优先级 | 任务 |
|--------|------|
| 🔴 | API 限流补全（login/register/auth 端点 + 全局默认） |
| 🔴 | 结构化日志（print → Python logging + 关键链路 trace_id） |
| 🟢 | OSS 验证 + 7 天生命周期策略 |

> 激活码核销后置，登录/注册页 UI 对齐等 Mac 隧道恢复后继续。
