# 悠米伴学 — Hermes 工作流与成本账本规划（GPT 终审版）

> 终审定稿：数据库成本账本方案 + Hermes 工作流方案 + 安全边界 + 落地实施顺序
>
> 复制自 `悠米伴学-Hermes工作流与成本账本规划GPT终审版.docx`

---

## 一、GPT 终审意见与最终裁决

**终审裁决**：本版可以作为悠米伴学 Hermes 工作流与成本账本模块的开工依据。当前优先级不是先做优惠抓取或活动策划，而是先完成模型调用落账、DeepSeek 判对错/问答落库、用户侧 credit_ledger 记账、平台账单对账四件事。只有成本账本闭环后，活动 ROI、套餐定价和运营策略才有真实依据。

**终审意见 5**：实施顺序必须先 P0 成本落账与链路审计，再 P1 成本日报和高成本用户识别，再 P2 套餐/活动 ROI，最后才是优惠抓取和活动策划。

**终审意见 4**：账实核对必须使用账单 CSV、只读 RAM/API 或人工导入，不得保存主账号 AK 或高权限云账号密钥。

**终审意见 3**：Hermes 只读生产库、日志和账单来源，只写自己的分析库；不得自动写生产 model_calls、credit_ledger、model_price_snapshots 或业务表。

**终审意见 2**：Qwen-VL-Max 识图切题允许一次模型调用完成，不强制拆成 fullpage_extract 与 question_cutting 两次调用，避免为凑字段造成重复计费。

**终审意见 1**：数据库层面以 model_calls 为供应商真实成本原始账本，以 credit_ledger 为用户侧权益/学豆账本，二者不得混用。

---

## 二、本版修正摘要

| 修正点 | 上一版潜在风险 | 本版定稿规则 |
|--------|--------------|------------|
| Qwen-VL 调用口径 | 把整页识图和切题强制拆成两个 call_id，可能导致重复调用和成本上升 | 允许一次 Qwen-VL-Max 调用完成识图+切题+结构化；统一用 qwen_parse_call_id 和 feature_code=qwen_vl_parse_homework |
| 价格快照权限 | Hermes 被误解为可直接写生产 model_price_snapshots | Hermes 只写 hermes_price_watch；生产价格表由人工确认或后端维护脚本更新 |
| 缺口回填权限 | 缺口清单可能被误解为 Hermes 可自动 backfill 生产表 | Hermes 只生成 backfill 候选清单和 SQL 建议；生产回填必须人工确认后执行 |
| 平台账单权限 | 账实核对可能诱导保存高权限云账号密钥 | 优先导入账单 CSV；其次只读 RAM/API；禁止主账号 AK 和高权限密钥 |

---

## 三、当前数据库断点复盘

| 链路环节 | 当前数据库表现 | 问题判断 | 优先级 |
|---------|-------------|---------|-------|
| 拍照/上传 | parsejobs 有记录；image_registry 有记录 | 上传链路基本有记录 | 正常 |
| 压缩 | 无追踪；纯前端操作 | Phase 0 可暂不计成本 | P2 |
| Qwen-VL 识图切题 | model_calls 有记录，但 cost=0；feature_code 为 fullpage_extract / vision_cutting | 有调用但不可用于成本测算；需统一 feature_code | **P0** |
| DeepSeek 判对错 | question_attempt 空；model_calls 无 deepseek_check 类记录 | 判对错链路未落库 | **P0** |
| DeepSeek 问答 | ai_tutoring_chat / tutor_chats 空；model_calls 无 deepseek_tutor 类记录 | 问答链路未落库 | **P0** |
| 用户侧成本 | credit_cost 全是 0.0 | 无法判断运营成本、免费额度和套餐盈亏 | **P0** |

---

## 四、新业务链路定版

```
上传作业
  ↓
客户端压缩与图片入库
  ↓
Qwen-VL-Max：一次调用可完成整页识图 + 切题 + bbox + 题目结构化
  ↓
parse_jobs → question_items → bbox + 题目文字
  ↓
孩子做答 → DeepSeek 判对错 → question_attempt 写入
  ↓
DeepSeek 问答：首次讲解 / 追问 → ai_tutoring_messages 写入
  ↓
所有模型调用 → model_calls 写入（供应商真实成本）
  ↓
用户侧扣费 → credit_ledger 写入（学豆/权益扣减）
```

**核心约束**：前端不直接接触 Qwen 或 DeepSeek Key；所有真实模型调用继续走后端 AI Gateway；Hermes 不直接写生产业务表。

---

## 五、数据库规划：成本账本字段

### 5.1 model_calls：模型调用原始账本

model_calls 是成本测算第一数据源。每一次 Qwen-VL 或 DeepSeek 调用，无论成功、失败、超时、重试，都应该写入。

| 字段 | 用途 | 是否关键 |
|------|------|---------|
| call_id | 模型调用唯一 ID | 是 |
| trace_id / parent_trace_id | 串起一次作业完整链路及上下游调用 | 是 |
| job_id / question_id / child_id | 把调用归属到作业、题目、孩子 | 是 |
| provider / model_name | 区分 DashScope、DeepSeek 及具体模型 | 是 |
| feature_code | 区分识题、判对错、首次讲解、追问等功能 | 是 |
| sub_stage | 可选字段，用于一次 Qwen 调用内部标记 | 建议 |
| input_tokens / output_tokens | 计算模型费用的基础 | 是 |
| cache_hit_tokens / cache_miss_tokens | DeepSeek 缓存计费差异需要 | 建议 |
| image_count / image_total_bytes | 定位图片过大导致成本异常 | 建议 |
| unit_price_input / unit_price_output | 调用时使用的价格快照 | 是 |
| currency / raw_cost / cost_cny | 原币种成本与人民币归一化成本 | 是 |
| pricing_snapshot_id | 关联后端确认后的价格快照 | 是 |
| latency_ms / status / error_code / request_id | 排查超时、失败、供应商问题 | 是 |

### 5.2 feature_code 定稿枚举

| 标准 feature_code | 业务含义 | 对应模型 |
|------------------|---------|---------|
| qwen_vl_parse_homework | 识图、切题、bbox、题目结构化 | Qwen-VL-Max |
| deepseek_check_attempt | 孩子答案判对错 | DeepSeek |
| deepseek_tutor_initial | 首次单题讲解 | DeepSeek |
| deepseek_tutor_followup | 孩子继续追问 | DeepSeek |
| deepseek_wrong_summary | 错因总结 | DeepSeek |
| deepseek_knowledge_summary | 知识点归纳 | DeepSeek |

> 旧 feature_code fullpage_extract / vision_cutting 可保留为历史兼容，但新写入必须使用 qwen_vl_parse_homework。

### 5.3 model_price_snapshots 与 hermes_price_watch 分工

| 表 | 谁可以写 | 用途 | 安全规则 |
|----|---------|------|---------|
| model_price_snapshots | 后端配置、人工确认、运维维护脚本 | 生产成本计算使用的有效价格快照 | Hermes 不直接写生产价格表 |
| hermes_price_watch | Hermes | 记录抓到的价格变化、来源、建议更新内容 | 只做观察和建议，不改变生产成本计算口径 |

### 5.4 业务表字段补强

| 表 | 必须补充/确认字段 | 核心目的 |
|----|-----------------|---------|
| parse_jobs | parse_mode、parser_provider、parser_model、qwen_parse_call_id、question_count、total_parse_cost_cny | 把 Qwen-VL 解析成本归属到一次作业 |
| question_items | source_call_id、parse_source、bbox_json、confidence、parse_cost_allocated_cny | 把作业解析成本分摊到每道题 |
| question_attempt | user_answer、is_correct、score、confidence、check_call_id、check_cost_cny | 把 DeepSeek 判对错落库并关联成本 |
| ai_tutoring_sessions | question_id、message_count、total_input_tokens、total_output_tokens、total_cost_cny | 汇总单题辅导会话成本 |
| ai_tutoring_messages | session_id、role、content、call_id、feature_code、input_tokens、output_tokens、cost_cny | 每轮问答与 DeepSeek 调用一一对应 |
| credit_ledger | feature_code、job_id、question_id、call_id、actual_cost_cny、credit_delta、billing_status | 区分供应商真实成本与用户侧学豆/权益扣减 |

---

## 六、Hermes 工作流总设计

### 6.1 工作流优先级

```
定时任务 / 企业微信指令
        ↓
Hermes 调度器
        ↓
只读生产数据库 + 平台账单只读来源 + 模型价格观察 + 日志分析
        ↓
写入 Hermes 分析库（hermes_youmi.sqlite）
        ↓
推送到企业微信
```

| 优先级 | 工作流 | 目的 |
|--------|--------|------|
| **P0** | 模型落账巡检 | 发现 cost=0、feature_code 缺失、DeepSeek 未落库等硬问题 |
| **P0** | 链路完整性审计 | 判断上传到扣费是否形成闭环 |
| **P0** | 账实核对 | 对比数据库成本与平台账单，防止账本失真 |
| P1 | 缺口回填候选清单 | 输出历史数据缺失明细，供人工确认后修复 |
| P1 | 每日 AI 成本日报 | 按模型、功能、作业、题目、孩子统计成本 |
| P1 | 高成本用户识别 | 提前发现被刷、重度使用、异常追问用户 |
| P2 | 套餐盈亏测算 | 判断体验包、会员、家庭套餐是否亏损 |
| P2 | 活动 ROI 测算 | 活动前测算最坏成本和预算封顶 |
| P2 | 模型降级建议 | 判断哪些场景可从 Max 降到 Plus 或低成本模型 |
| P3 | 竞品/优惠抓取 | 服务于降本和活动策划，不应早于账本闭环 |

---

## 七、核心工作流细化

### 7.1 模型落账巡检（每 30 分钟）

- 检查 model_calls 是否新增
- 检查 success 调用是否 cost_cny=0
- 检查 feature_code 是否为空、旧口径或不在白名单
- 检查 Qwen-VL 是否写入 input_tokens / output_tokens / image_count
- 检查 DeepSeek 判对错、首次讲解、追问是否有对应 model_calls
- 检查 model_calls 是否能关联 job_id / question_id / child_id

> cost=0 且 success、feature_code 为空、DeepSeek 前端动作存在但无 model_calls，均为红色告警。

### 7.2 管线完整性审计（每小时）

- parsejobs 与 imageregistry 是否匹配
- parsejobs completed 后是否生成 question_items
- Qwen-VL model_calls 是否与 parsejobs 关联
- 孩子提交答案后 question_attempt 是否写入
- AI 问答后 ai_tutoring_messages 是否写入
- 每次模型调用是否有 credit_ledger 或免费额度记录

> 输出断点：上传、解析、切题、判对错、问答、扣费哪个环节断。

### 7.3 账实核对（每天 23:30）

- 汇总数据库 model_calls 的 cost_cny
- 导入阿里云 DashScope 账单 CSV 或使用只读 RAM/API
- 导入 DeepSeek 控制台账单 CSV 或使用只读 API
- 按 provider / model / date 对比差异
- 差异超过阈值自动推送

> 差异 >5% 黄色告警，>10% 红色告警。禁止主账号 AK 和高权限密钥。

### 7.4 缺口回填候选清单（每天一次）

- 找出 parsejob 有记录但 Qwen call 缺失的数据
- 找出 Qwen call 有记录但 cost=0 的数据
- 找出 question_item 存在但无 question_attempt 的判对错缺口
- 找出前端 AI 问答行为存在但 ai_tutoring_messages 空的缺口
- 按 job_id / question_id / call_id 输出回填候选清单

> Hermes 只生成清单和 SQL 建议，不自动写生产表。

### 7.5 每日 AI 成本日报（每天 22:30）

- 总模型成本、Qwen-VL 成本、DeepSeek 成本
- 按 feature_code 分组统计成本
- 单次作业成本、单题成本、单孩子成本
- 失败但产生 token 的调用成本
- 免费额度消耗和 credit_ledger 账本变化

### 7.6 高成本用户识别（每天一次）

- 高成本 child TOP 10
- 高频上传用户 TOP 10
- 高频追问用户 TOP 10
- 失败调用最多用户 TOP 10
- 单题平均成本异常用户

### 7.7 套餐盈亏测算（每天 23:00 + 每周一完整周报）

- 测算 9.9 体验包、19.9 月体验包、39.9 月会员、69.9 家庭会员
- 按平均作业次数、题数、追问次数推算月成本
- 加入云资源、失败重试、免费额度折损
- 输出毛利、安全额度、风险阈值

> 没有 model_calls 成本闭环前，不允许输出"可投放"结论，只能输出"数据不足"。

### 7.8 活动 ROI 测算（每周一次 / 微信指令触发）

- 测算活动人数、天数、每日作业数、每作业题数、每题追问数
- 计算 Qwen-VL、DeepSeek、云资源、失败重试总成本
- 估算收入、毛利、最坏亏损
- 给出总预算封顶、单用户限额、活动停止条件

### 7.9 模型价格观察（每天 10:00）

- 观察 Qwen-VL-Max、Qwen-VL-Plus、DeepSeek 等价格
- 写入 hermes_price_watch
- 价格变化时估算昨日用量下的成本变化
- 生成建议更新 model_price_snapshots 的人工确认单

### 7.10 Debug 日志摘要（每天 09:00 和 22:00）

- 读取后端日志、模型调用错误、数据库异常、API 500/404、任务超时
- 输出 TOP 错误、影响范围、建议修复顺序
- 与链路完整性审计结果合并

---

## 八、Hermes 分析库

Hermes 建议单独使用 hermes_youmi.sqlite 或独立 PostgreSQL schema。生产数据库只读，Hermes 分析结果写入自己的表。

| Hermes 表 | 核心字段 | 用途 |
|-----------|---------|------|
| hermes_alerts | alert_type, severity, title, detail, related_table, related_id, status, resolved_at | 保存告警和处理状态 |
| hermes_daily_cost_reports | report_date, total_cost_cny, qwen_cost_cny, deepseek_cost_cny, avg_job_cost_cny | 每日成本日报沉淀 |
| hermes_chain_audit_reports | parsejob_count, qwen_call_count, question_item_count, question_attempt_count, tutor_msg_count | 链路健康审计 |
| hermes_campaign_forecasts | campaign_name, user_count, days, estimated_total_cost, expected_revenue, roi_result | 活动 ROI 预测留档 |
| hermes_price_watch | provider, model_name, old_price, new_price, change_rate, source_url, suggested_action | 价格变化监控与人工确认建议 |
| hermes_backfill_candidates | source_table, source_id, missing_type, suggested_action, status | 缺口回填候选清单，不直接执行 |

---

## 九、企业微信指令设计

微信回复格式必须短：结论、关键数字、异常点、建议动作、是否需要人工确认。

| 微信指令 | Hermes 返回内容 |
|---------|---------------|
| 今日成本 | 总成本、Qwen 成本、DeepSeek 成本、单作业/单题成本、异常点 |
| 今日链路 | 上传、解析、切题、判对错、问答、扣费各环节数量和断点 |
| 今日异常 | 红色/黄色告警列表，按影响程度排序 |
| 查 DeepSeek 是否落库 | DeepSeek model_calls、question_attempt、ai_tutoring_messages 写入情况 |
| 查 Qwen 成本为什么是 0 | cost=0 调用明细、缺失字段、建议修复点 |
| 测算 500 用户活动成本 | 按默认或指定参数生成活动 ROI 测算 |
| 生成 9.9 体验包方案 | 先测算成本与限额，再输出活动建议 |
| 查最近 20 条失败调用 | 返回失败调用、错误码、耗时、是否产生 token 成本 |

---

## 十、链路健康评分

| 评分项 | 权重建议 | 扣分逻辑 |
|--------|---------|---------|
| model_calls 成本完整度 | 20 | success 调用 cost=0 越多，扣分越重 |
| feature_code 完整度 | 10 | 缺失或旧口径 feature_code 扣分 |
| Qwen-VL 解析闭环率 | 10 | parsejobs 完成但无 question_items 或无 Qwen call 扣分 |
| DeepSeek 落库率 | 20 | 判对错/问答无 model_calls、无消息表记录扣分 |
| credit_ledger 写入率 | 15 | 模型调用有成本但无用户侧账本扣分 |
| 账实核对差异 | 15 | 数据库成本与平台账单差异越大，扣分越重 |
| 失败率与超时率 | 10 | 失败/超时集中增长扣分 |

评分解释：
- **90-100**：可小规模放量，可做低风险活动
- **80-89**：可继续内测，但需观察异常
- **60-79**：不建议做活动投放，先修链路问题
- **40-59**：停止放量，集中修复
- **<40**：紧急状态

---

## 十一、成本测算公式

单次作业成本 =
- Qwen-VL 识图切题结构化成本
- \+ DeepSeek 判对错成本 × 实际判题次数
- \+ DeepSeek 首次辅导成本 × 实际打开辅导次数
- \+ DeepSeek 追问成本 × 实际追问次数
- \+ 云资源、失败重试、免费额度折损

> 用户侧学豆扣费不应等于供应商真实成本。credit_ledger 里必须同时保留 actual_cost_cny 和 credit_delta。

---

## 十二、活动前风控规则

| 问题 | Hermes 必须给出答案 |
|------|-------------------|
| 免费额度会不会被刷爆？ | 给出单用户每日最大成本、总预算上限、异常用户阈值 |
| 单用户最坏成本是多少？ | 按作业解析上限、题目数上限、追问上限计算 |
| 活动最坏亏损是多少？ | 按最低付费率与最高使用量测算 |
| 是否需要限流/限次？ | 给出每日作业次数、追问次数、视觉重读次数建议 |
| 什么时候停止活动？ | 设置总成本、失败率、账本异常、平台账单差异等停止条件 |

---

## 十三、安全边界与禁止事项

**原则**：采集自动化、分析自动化、建议自动化；生产变更、花钱动作、数据库写入、投放动作必须人工确认。

| 允许 Hermes 自动做 | 必须人工确认或禁止 |
|-------------------|------------------|
| 生成日报、周报、异常报告 | 自动改套餐价格 |
| 生成 SQL 检查语句 | 自动购买云资源 |
| 生成 backfill 候选清单 | 自动修改生产数据库 |
| 生成活动成本测算 | 自动开启活动投放 |
| 生成模型降级建议 | 自动切换生产模型 |
| 读取只读数据库和日志 | 自动清空数据、reset、force push、删除日志 |
| 写入 Hermes 自己的分析库 | 写入 model_calls、credit_ledger、question_attempt、ai_tutoring_messages 等生产表 |

---

## 十四、平台账单与密钥权限

| 优先级 | 方式 | 说明 |
|--------|------|------|
| 1 | 账单 CSV / 明细导出导入 Hermes 分析库 | 最安全，适合早期阶段；不需要云账号权限 |
| 2 | 只读 RAM 子账号 / 只读 API | 只允许读取账单和用量 |
| 3 | 人工手动录入价格与账单汇总 | 临时可用，但精度较低 |
| **禁止** | 主账号 AK / 高权限密钥 / 可购买资源权限 | 不得保存到 Hermes，不得交给自动化 agent |

---

## 十五、落地实施顺序

| 阶段 | 目标 | 完成标准 |
|------|------|---------|
| 第 1 阶段 | 修 model_calls 成本落账与 feature_code 标准化 | Qwen-VL 和 DeepSeek 每次调用都能写入 token、cost_cny、feature_code、job_id/question_id |
| 第 2 阶段 | 修 DeepSeek 判对错和问答落库 | question_attempt、ai_tutoring_sessions、ai_tutoring_messages 不再为空 |
| 第 3 阶段 | 接 credit_ledger 用户侧账本 | 真实成本 actual_cost_cny 与用户扣费 credit_delta 分开记录 |
| 第 4 阶段 | 上线 Hermes P0 巡检 | 每天能推送模型落账巡检、链路审计、账实核对 |
| 第 5 阶段 | 上线成本日报和高成本用户识别 | 可按作业、题目、孩子、模型、功能统计成本 |
| 第 6 阶段 | 上线套餐与活动 ROI 测算 | 活动前能输出最坏成本、预算封顶、限额建议、停止条件 |
| 第 7 阶段 | 再做优惠抓取和竞品活动观察 | 基于真实成本和业务数据生成活动策划 |

---

## 十六、验收标准

| 验收项 | 通过标准 |
|--------|---------|
| 模型落账 | Qwen-VL 与 DeepSeek success 调用 cost_cny 不再为 0，失败调用也记录 status/error_code |
| 功能归因 | model_calls feature_code 全部命中标准枚举，无空值和旧口径混用 |
| Qwen 调用口径 | 一次 Qwen-VL 调用可完整覆盖识图切题，不强制拆两个 call_id |
| 链路闭环 | parsejobs → question_items → question_attempt / ai_tutoring_messages → credit_ledger 全链路串联 |
| 成本可算 | 能输出单次作业、单题、单孩子、单模型、单功能成本 |
| 账实可核对 | 数据库成本与平台账单日差异可解释，超过阈值有告警 |
| 活动可测算 | 能输入用户数、天数、使用上限后输出 ROI 和最坏亏损 |
| 安全边界 | Hermes 无生产写权限，不自动调价、不自动投放、不自动购买资源、不保存高权限密钥 |

---

## 十七、GPT 终审版最终确认

- ✅ 逻辑一致：取消 OCR 后，Qwen-VL-Max 作为识图切题入口，DeepSeek 负责判对错、解答、追问和总结
- ✅ 成本一致：model_calls 记录供应商真实成本，credit_ledger 记录用户侧扣费，两者不混用
- ✅ 调用一致：不强制把 Qwen-VL 识图和切题拆成两次模型调用
- ✅ 权限一致：Hermes 只读生产库和日志，只写自己的分析库
- ✅ 对账一致：账实核对使用账单 CSV 或只读账号/API
- ✅ 执行一致：先修成本落账和 DeepSeek 落库，再做日报、套餐测算、活动 ROI，最后做优惠抓取

> **最终建议**：当前立即进入"数据库成本落账修复 + DeepSeek 链路落库 + Hermes P0 巡检工作流搭建"。在 model_calls、question_attempt、ai_tutoring_messages、credit_ledger、账单对账未闭环前，不建议启动营销活动自动化或规模化投放。
