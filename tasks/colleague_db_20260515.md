# 小张 — 数据库任务清单（2026-05-15）

> 基准文档：`Hermes工作流与成本账本规划-GPT终审版.md` §15 落地实施顺序
> 当前 git HEAD: `148f89f`（已包含你的 b6b6b7a cost 分账提交）

## 🔴 P0 优先

### 1. 跑通 daily_report_v3.py，验证成本日报输出

- 文件：`scripts/daily_report_v3.py`
- 目标：输出 HTML/PNG 成本日报，数据来源 model_calls 表
- 验证：产出可读的成本日报，含 Qwen/DeepSeek 分模型统计

### 2. model_calls cost_cny 落账修复

- 问题：历史 Qwen-VL 和 DeepSeek 调用 `cost_cny=0`
- 目标：每次成功调用必须写入真实 `cost_cny`
- 关键：`backend/model_logger.py` 写入 `cost_cny` 时从 pricing_snapshot 取实时单价

### 3. feature_code 标准化

- 问题：旧口径混用 `fullpage_extract` / `vision_cutting` / 空值
- 目标：统一为标准枚举（如 `qwen_vl_parse_homework`, `deepseek_tutor`, `deepseek_check`）
- 参考：成本账本规划 §5.1 model_calls 字段规范

## 🟡 P1

### 4. credit_ledger 用户侧扣费接入

- 目标：`credit_cost` 不再为 0，真实记录学豆/权益扣减
- 关键：`actual_cost_cny`（供应商成本）与 `credit_delta`（用户扣费）分开记录，不得混用

---

**规则提醒：** 完成后 push 到公共区前，先群里说一声，等刚哥「开门」令牌。
路径：读完开工 → 文件在 `/home/hermes_me/yomi/tasks/colleague_db_20260515.md`
