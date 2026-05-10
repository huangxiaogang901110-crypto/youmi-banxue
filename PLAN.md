# 悠米伴学 Phase 0 施工任务计划

> **基准文件**（开工必读）：
> 1. `悠米伴学-施工基准文档御三家GPT终审版.docx` — 总基准，最高优先级
> 2. `悠米伴学-前端基准御三家GPT终审版.docx` — 前端专项基准
> 
> **产出**: `PROGRESS.md`

---

## 核心边界（不可越）

| 边界 | 裁定 |
|------|------|
| Phase 0 目标 | 打通主链路可录屏演示，不是完整商业 App |
| 用户可见层 | Next.js App Router + shadcn/ui + Tailwind，**不接受** Gradio/Streamlit 作为第一眼 |
| 支付 | **不接**真实微信/支付宝/IAP SDK |
| 激活码 | **只做** Mock 弹层，不做真实核销 |
| 错题本 | **只做** 题目状态标记 + 错因记录，不做完整检索/知识点体系 |
| bbox | `<img>` + div 绝对定位，**不用** Canvas 编辑器 |
| 流式 | Phase 0 **假打字机**，不做真 SSE |
| API Key | **禁止**进入前端/PWA/客户端/缓存/日志 |
| 文案 | **禁止** "永久免费""无限识别""无限追问""秒出结果" |

---

## 全局技术约束（所有任务适用）

| # | 约束 | 来源 |
|---|------|------|
| G1 | 所有 `localStorage`/`IndexedDB`/`window` 读取必须在 `useEffect` 内 | 前端基准 §5 hydration |
| G2 | 移动端 375-430px 优先，桌面端 1024px+ 扩展 | 前端基准 §12 |
| G3 | API 统一走 `typedFetch`，组件不得直拼 URL | 前端基准 §6 |
| G4 | 统一错误格式 `{ok, code, message, request_id, details}` | 施工基准 §6.3 |
| G5 | KaTeX + react-katex 唯一公式渲染库 | 前端基准 §14 |
| G6 | 主链路页面默认 `"use client"` | 前端基准 §5 |
| G7 | 首次渲染用 skeleton/loading 占位，不裸奔 | 施工基准 §21 |
| G8 | TanStack Query 是远程数据唯一源，Zustand 只做 UI 展示态 | 前端基准 §15.1 |
| G9 | multipart/form-data 上传，禁止 Base64 JSON | 施工基准 §24 |
| G10 | `X-Child-Id: demo_child_001` 统一 Header | 施工基准 §6.0 |

---

## 已完成任务

| # | 任务 | 状态 |
|---|------|------|
| 1 | 项目骨架 (Next.js + Tailwind + shadcn/ui) | ✅ |
| 2 | 前端 types.ts + api.ts (typedFetch + 9 API 函数) | ✅ |
| 3 | FastAPI 后端 (骨架 + 7 个 Mock 端点，8/8 验证通过) | ✅ |

---

## 待施工任务

### 任务 4：前端页面布局与导航

**目标**: 搭好全局框架，5 个页面路由全部就位。

| 子任务 | 内容 | 关键约束 |
|--------|------|----------|
| **4a** | Tailwind Design Tokens | 按前端基准 §13 Token 表：`#FAFAF7` 底、`#4DBBAA` 主色、20px 卡片圆角、14px 按钮圆角 |
| **4b** | 全局 Layout 组件 | TabBar 底部导航 + 顶部状态栏 + `safe-area-inset-bottom` + `100dvh` |
| **4c** | 5 个页面路由骨架 | `/` 入口、`/workspace` 工作台、`/upload` 上传、`/question/[id]` 辅导、`/mistakes` 错题标记 |
| **4d** | 通用组件 | StatusBar、PageContainer、EmptyState、Skeleton、ErrorDisplay（统一错误格式） |

**验收**: `npm run build` 零错误，5 个路由均可访问，移动端 375px 视口下 TabBar 不重叠

---

### 任务 5：作业上传页

**目标**: 实现图片/PDF 上传，包含压缩、幂等、进度 UI。

| 子任务 | 内容 | 关键约束 |
|--------|------|----------|
| **5a** | 拖拽上传 + 相机/相册按钮 | 手机端优先验证 |
| **5b** | 客户端图片压缩 | Canvas API，长边 ≤2200px，JPEG 质量 0.85，目标 ≤3MB |
| **5c** | multipart/form-data 上传 | `client_task_id` UUIDv4 幂等键（前端生成），`page_range=1`（PDF 第 1 页），禁止 Base64 JSON |
| **5d** | 文件类型校验 + 上传进度 | 仅允许 image/jpeg, image/png, image/webp, application/pdf |

**验收**: Network 中 /api/parse-jobs 为 multipart/form-data，请求体含 `client_task_id` UUID，无 Base64 JSON

---

### 任务 6：解析状态 + 题目列表 + bbox

**目标**: 实现轮询、状态动效、题目列表、bbox 单向联动。

| 子任务 | 内容 | 关键约束 |
|--------|------|----------|
| **6a** | 解析状态页 | 6 阶段 Stepper（uploaded→enhancing→ocr→cutting→vision→validating→completed），顶部"通常需要 20-30 秒"，动效降级不与真实进度同步 |
| **6b** | `useParseJobPolling` hook | TanStack Query 1.5s 轮询，**job_id 必须写入 URL**（`/workspace?job_id=xxx`），刷新后可恢复 |
| **6c** | bbox 渲染层 | `<img>` + 绝对定位 div，**img.onLoad 后**用 naturalWidth/Height 计算坐标，**ResizeObserver** 监听容器变化，公式：`display_x = bbox_x × display_w / original_w` |
| **6d** | 单向联动 | 点击题目列表→高亮对应 bbox + 护眼青色边框 + 非选中区 20% 暗色蒙版，不做反向点击 bbox 定位列表 |

**验收**: 375px 下 bbox 位置无偏移，刷新 `/workspace?job_id=xxx` 可恢复轮询

---

### 任务 7：AI 辅导对话页

**目标**: 假打字机对话、KaTeX 公式、视觉二次路由、扣费恢复。

| 子任务 | 内容 | 关键约束 |
|--------|------|----------|
| **7a** | 假打字机 hook | `setInterval` 逐字输出 40-50 字/秒，**点击对话区或"跳过"按钮立即展示全文** |
| **7b** | 三层输出组件 | 提示 → 分步讲解 → 完整解析 + 易错点，大段文字卡片化，公式用 KaTeX |
| **7c** | KaTeX 集成 | `react-katex`，不引入 MathJax 或其他 |
| **7d** | 动作按钮组 + 缓存恢复 | "给我一点提示""分步讲给我听""查看完整解析""我会了""加入错题本"，**`/tutor` 成功返回后先写入 IndexedDB 再更新余额展示**（防刷新丢内容） |
| **7e** | 视觉二次路由 | 前端关键词检测（图/线段/表格/几何/箭头/阴影/方格/坐标/这条/这个图）→ 触发雷达扫描动画 → 调 `/vision`，**成功后同样 IndexedDB 缓存恢复** |

**验收**: 假打字机可跳过，`/tutor` 成功后刷新页面可恢复内容不重复扣费，视觉关键词命中后出现"AI 正在仔细观察原图..."

---

### 任务 8：状态标记 + 权益 + 激活码

**目标**: 题目状态标记、权益状态条、激活码 Mock、额度拦截。

| 子任务 | 内容 | 关键约束 |
|--------|------|----------|
| **8a** | 题目状态标记组件 | "我会了" / "加入错题本" + 错因记录（`error_type_code` + `reason_desc`），**不做列表检索/筛选/知识点归类** |
| **8b** | 权益状态条 | `useEntitlementQuery()` → onSuccess → `entitlementStore.setState()` → 组件只读 store，TanStack Query 唯一远程源，**单向数据流不可反向写** |
| **8c** | 激活码 Mock + 额度拦截 | 激活码弹层（Mock 校验 "YOMI-FREE-2024"），credit_low 轻提示，credit_empty 拦截深度调用并引导激活 |

**验收**: 额度为空时深度解析按钮不可点击且显示引导，激活码 Mock 校验可用

---

### 任务 9：端到端联调与验收

**目标**: 全链路走通 + 移动端验证 + 错误态覆盖。

| 子任务 | 内容 | 关键约束 |
|--------|------|----------|
| **9a** | 全链路走通 | 上传→解析状态→题目列表→选题→假打字机辅导→视觉追问→状态标记 |
| **9b** | 移动端验证 | 375px-430px 视口下各页可用，软键盘不遮挡输入框 |
| **9c** | 错误态覆盖 | 上传失败、OCR 失败、切题失败、DeepSeek 超时、额度不足，**每个错误有用户可理解提示 + 操作出口** |

**验收**: 手机端可录屏演示完整主链路，Phase 0 禁止事项零越界

---

## Phase 0 最终验收清单

| 类别 | 验收点 | 来源 |
|------|--------|------|
| UI | 用户第一眼能看懂这是"家庭学习工作台"，首页有明确上传入口 | 前端基准 §20 |
| 移动端 | 375-430px 下上传、解析、列表、辅导可用 | 前端基准 §20 |
| 解析状态 | 有阶段进度 + 时长预期 + 超时提示，不误以为卡死 | 前端基准 §20 |
| bbox | 点击题目高亮 bbox，位置映射正确，无偏移 | 前端基准 §20 |
| 辅导 | 假打字机效果 + 可跳过，KaTeX 公式渲染正常 | 前端基准 §20 |
| 视觉追问 | 命中关键词出现"AI 正在仔细观察原图..."状态 | 前端基准 §20 |
| 错误态 | 上传/OCR/切题/超时/额度不足均有可理解提示+出口 | 前端基准 §20 |
| multipart | Network 中 /api/parse-jobs 为 multipart/form-data | 施工基准 §24 |
| 刷新恢复 | 刷新 `/workspace?job_id=xxx` 可继续轮询 | 施工基准 §24 |
| hydration | 首次渲染无 `window is not defined` 报错 | 施工基准 §24 |
| 扣费恢复 | tutor 成功后刷新可恢复解析，不重复扣费 | 施工基准 §24 |
| 商业化预留 | entitlement 状态、activation Mock、credit 拦截 全部存在 | 施工基准 §20 |
| 文案边界 | 无"永久免费""无限识别""违反平台规则"等表述 | 施工基准 §20 |
| 禁止事项 | 未接真实支付/激活码/bbox编辑器/DOCX/完整错题本 | 施工基准 §18 |

---

## 开工顺序

```
任务 4 → 任务 5 → 任务 6 → 任务 7 → 任务 8 → 任务 9
```

每完成一个任务：更新 `PROGRESS.md`，跑 `npm run build` 验证不破。
