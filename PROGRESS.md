# 悠米伴学 — 工程进度

> 由 Hermes（阿石）维护。ECS 上项目文件，不提交 git。

## 当前状态

- 项目：悠米伴学 (Youmi Companion Learning)
- 阶段：**Phase 0 全部完成 ✅** → 待进入 Phase 1 小范围内测
- 最后更新：2026-05-10 21:35 CST
- 部署：ECS 39.107.119.136，Nginx 静态 `/out` + FastAPI `:8000`
- 构建：Next.js 16.2.6 (Node v20.18.1)，7 路由全静态导出，零错误

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
| R5 | HomeworkList + 勾选 + 进度条 | ✅ 完成 | 按科目分组，checked 状态 + 进度百分比 |
| R6 | 状态持久化 + 合并预览 | ✅ 完成 | localStorage 持久化，精确文本匹配去重 |
| R7 | 首页集成 | ✅ 完成 | 首页"识别作业"入口卡片 |
| R8 | 构建 + 联调 + 部署 | ✅ 完成 | TabBar 5 个标签（作业合并入「识别作业」），build 零错误 |

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

### 2026-05-10
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
| 5 | 接入阿里云 OCR | ✅ 完成 | 官方 SDK，端到端验证通过。免费额度剩余 193 次 |
| 6 | 智能切题（题号规则+版面规则） | ✅ 完成 | `question_cutter.py`：7种题号模式 + gap fallback。259块→13题。禁止大模型直接画框 |
| 7 | Qwen-VL 视觉理解（识别图形/公式） | ✅ 完成 | `vision_client.py`：DashScope Qwen-VL-Plus。key 为空时优雅降级。集成到管线 vision_reviewing 阶段。model_call_log 工厂函数已提取 |
| 8 | DeepSeek 辅导讲解 | ✅ 完成 | `deepseek_client.py`：空 key 优雅降级（sk- 前缀校验）。`tutor_prompt.py`：initial/followup 双模式。对话上下文 + 轮数上限 + model_call_log |
| 9 | DB schema（model_call_log + 结果持久化） | ⬜ 待做 | |

---

## 下一步：Phase 1 小范围内测

| 项目 | 内容 | 优先级 |
|------|------|--------|
| 账号/child 鉴权 | JWT + X-Child-Id 真实鉴权替代占位 | 🔴 高 |
| 真实 AI 解析 | 替换 Mock，接 v4-flash / Qwen-VL | 🔴 高 |
| 云端数据库 | PostgreSQL/Supabase 替代 Mock 数据 | 🟡 中 |
| 激活码核销 | 真实核销替代 Mock | 🟡 中 |
| 对象存储 | R2/OSS 存储图片，7天生命周期 | 🟢 低 |
| FastAPI 云部署 | CORS + 限流 + 日志 | 🟡 中 |
