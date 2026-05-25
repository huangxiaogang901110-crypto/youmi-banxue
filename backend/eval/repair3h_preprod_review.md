# Repair-3H 生产接入前最终审查

## 结论

- 当前结论：`No-Go`，本轮不允许直接进入 3I。
- 原因不是 classifier/evaluator 失效，而是进入真实小样本前还有明确阻塞项未消掉：
  - 测试入口口径未对齐：仓库运行文档是 `3001/8001`，旧真实评估脚本默认 `8002`，仓库内未发现 `3002`
  - 日志层没有集中脱敏器，不能证明真实调用时绝不会落明文敏感信息
  - 真实真值缺口仍主要集中在 `dense_multi_column_page` 与 `landscape_or_tilted_photo`
- 可以给出的正面结论：当前 classifier/evaluator/quality gate 已经足够支撑“受控、人工盯盘、5-8 张”的 3I 小样本真实测准备，但不能自动放行。

## 审查结果

### 1. classifier

当前 `backend/document_classifier.py` 已具备进入小样本真实测前的最小必要能力：

- 能输出 `layout_regions`、`section_headers`、`meta_block_indices`、`question_block_indices`、`answer_block_indices`、`major_failure_reason`
- 对 `cover_or_instruction_page`、`non_homework`、`unknown` 采用保守收口
- 对 `mixed_homework` 保留题目，不再一刀切丢弃
- 数学与语文/英语结构化抽取路由已分离

当前单测覆盖也足够说明“规则没有明显倒退”：

- `backend/test_document_classifier.py`
  - 覆盖 meta/footer 过滤、cover/non-homework 收口、mixed 保留、section/options/blanks、结构化抽取、兼容旧字段
- `pipeline.py` 的收口兼容只通过测试引用验证，本轮未修改 `pipeline.py`

保留风险：

- 现有单测主要是规则样本与伪造 OCR block，仍缺真实横拍/斜拍、多栏密集题的在线回归证据
- 这也是 3G 首批真实样本必须优先补的缺口

### 2. evaluator

当前 `backend/eval_repair2_cached.py` 已具备进入小样本真实测前的预检价值：

- 固化了两档 no-paid 质量门槛
  - `only_json_fixtures`: `effective_sample_count=10`、`skipped_count=0`、`violation_total_count=0`
  - `no_paid`: `effective_sample_count=11`、`skipped_count=18`、`violation_total_count=0`
- 会统计：
  - `question_count`
  - `meta_filtered_count`
  - `pseudo_filtered_count`
  - `section_count`
  - `options_count`
  - `blanks_count`
  - `source_kind`
  - `skipped_reason`
- 会产出 `fixture_gap_report`，能明确告诉我们真实真值还缺哪些形态

当前测试 `backend/test_eval_repair2_cached.py` 也已经把基准锁住：

- `test_repo_only_json_quality_gate_matches_baseline`
- `test_repo_no_paid_quality_gate_and_gap_report_snapshot`

结论：

- evaluator/quality gate 适合作为 3I 前置闸门
- 但它只能证明“当前缓存与 fixture 没倒退”，不能替代真实拍照样本的人工验收

### 3. quality gate

当前 gate 足够严格，能挡住以下问题：

- `unknown` 样本混入 effective 集合
- `fixture_only` 样本误算为有效样本
- conservative 页型仍产出题目
- meta / pseudo / legacy field break 污染结果

结论：

- 作为 3I 前 gate：够用
- 作为 3I 后自动放量依据：不够

### 4. 模型链路分离

当前链路边界基本清楚：

- 识别链路：Qwen-VL + OCR
- 辅导链路：DeepSeek
- 作业文本解析链路：DeepSeek

仓库证据：

- `backend/vision_client.py`: `qwen-vl-max`
- `backend/ocr_client.py`: 阿里云 OCR
- `backend/routes/parse_routes.py`: `/api/questions/{question_id}/tutor` 才进入 DeepSeek
- `backend/routes/homework_routes.py`: `/api/homework/parse` 才进入 DeepSeek 作业文本解析

因此 3I 若严格限定为“识别小样本真实测”，DeepSeek 预算应为 `0`。

### 5. 日志与脱敏

这部分是当前最明显的预发布风险之一。

- `backend/logger.py` 只有通用 logging 封装，没有集中脱敏逻辑
- `backend/model_logger.py` 主要记录模型调用元数据、成本、trace，风险比原始日志低
- 但仓库内没有证据证明所有 route 日志、异常日志、shell 输出都做了统一脱敏

结论：

- 3I 前必须额外确认日志查看范围、grep 策略和敏感字段处理
- 在未确认前，不应把“日志脱敏已完成”当作既定事实

## 部署前必须确认项

| 项目 | 当前证据 | 进入 3I 前必须确认 |
|---|---|---|
| 分支 / HEAD | 目标应为 `codex/repair3g3h-real-sample-plan` from `39e633b`；当前沙箱 `.git` 只读，未能在本轮创建分支 | 手工确认分支已创建，且 HEAD 仍锚定 `39e633b` 或其 docs-only 后继提交 |
| env 变量 | `local_check_env.sh` 要求 `QWEN_DASHSCOPE_API_KEY`、`DEEPSEEK_API_KEY`、`ALIBABA_CLOUD_ACCESS_KEY_ID`、`ALIBABA_CLOUD_ACCESS_KEY_SECRET`、`TEST_PHONE`、`TEST_PWD` | 3I 只需确认 Qwen/OCR 与测试账号；DeepSeek 不应参与识别批次 |
| 模型开关 | 识别链路可走 Qwen/OCR；DeepSeek 在 tutor/homework parse 路由 | 3I 明确限定只打 `/api/parse-jobs*`，不打 tutor、不打 homework parse |
| 日志脱敏 | 未发现集中 redaction 层 | 执行前先确认日志查看与保存方式，避免落明文手机号、密钥、Bearer |
| 成本上限 | 方案中已给出 `8` 图 / `8` 次 parse / `4` 次 OCR / `0` 次 DeepSeek | 需要明确批准该上限，超限自动停止 |
| 回滚点 | 本轮只做文档，业务代码未改 | 3I 前确认回滚点为 `39e633b` 或 docs-only commit；真实样本失败可直接废弃批次，不碰生产 |
| `3002/8002` 测试入口 | 仅在 `backend/evaluate_repair1_step1.py` 看到默认 `8002`；仓库运行文档写 `3001/8001`；未发现 `3002` | 必须口头或书面确认真实非生产入口；不能默认猜 `8002`、更不能打 `8000` |
| `3000/8000` 不得误动 | `PROGRESS.md` 已写明不测生产端口 | 3I 必须继续保持；任何健康检查、curl、前端联调都不能碰 `3000/8000` |

## Go / No-Go

### 当前结论

- `No-Go`

### 允许进入 3I 的前提

必须同时满足以下授权项：

1. 明确授权执行真实识别调用
2. 明确授权测试入口，确认是 `8002/3002` 还是修正为 `8001/3001`
3. 明确授权使用测试账号 `TEST_PHONE` / `TEST_PWD`
4. 明确授权成本上限：最多 `8` 张图、最多 `8` 次 parse、最多 `4` 次 OCR、`0` 次 DeepSeek
5. 明确授权把通过人工验收的样本转为后续 cache/fixture 候选

### 3I 不允许做什么

- 不允许自动扩大样本量
- 不允许触发 DeepSeek tutor 或 homework parse
- 不允许部署、重启、改生产目录
- 不允许触碰 `3000/8000`
- 不允许顺手改 `pipeline.py`
- 不允许在未确认分支/HEAD 的情况下开始真实跑批

## 本轮限制声明

- 本轮只完成方案、审查、文档与 no-paid 验证
- `pipeline.py` 未改，也不需要为 3G/3H 修改
- 3I 必须另行授权，不能自动执行
