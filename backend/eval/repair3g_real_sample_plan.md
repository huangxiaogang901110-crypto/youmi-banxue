# Repair-3G 少量真实识别采集方案

## 边界

- 本文件只定义 3G 采集方案，不执行真实模型调用。
- 本轮禁止进入 3I，禁止部署、重启、同步公共区、触碰 `3000/8000`。
- 3I 必须另行授权，且只能在确认测试入口后执行。

## 现有依据

- `backend/eval/repair3d_fixture_gap_report.json`
  - 已覆盖：`mixed_pinyin_english`、`complex_chinese_page`、`standalone_multiple_choice_page`、`non_homework_image`、`cover_or_instruction_page`、`teacher_markup`
  - 未覆盖：`dense_multi_column_page`、`landscape_or_tilted_photo`
- `backend/eval/repair3d_fixture_review_tags.json`
  - 已给出可参考的真实形态标签，优先对照 `04fb... / 1102... / 8ca1... / e8bc...`
- `backend/eval_repair2_cached.py`
  - `--only-json-fixtures` 与 `--no-paid` 的门槛已固化到 `QUALITY_GATE_EXPECTATIONS`
- `backend/evaluate_repair1_step1.py`
  - 现成真实识别脚本原型，支持 `--allow-paid-ocr` 与 `--max-paid-calls`

## 样本清单

| 编号 | 目标样本 | 用途 | 预期补缺口 | 预计触发 | 参考形态 |
|---|---|---|---|---|---|
| RS-01 | 横拍多栏语文阅读/填空页，带批改或涂改 | 首优先样本，一张同时覆盖横拍、密集、多栏、阅读、批改痕迹 | `landscape_or_tilted_photo` + `dense_multi_column_page` + `teacher_markup` | OCR=大概率 / Qwen=是 / DeepSeek=否 | `04fb8059...` |
| RS-02 | 斜拍数学练习页，已有孩子书写答案 | 验证斜拍页的题号、答案、切题边界是否稳定 | `landscape_or_tilted_photo` + `teacher_markup` | OCR=大概率 / Qwen=是 / DeepSeek=否 | `110203d3...`、`e8bc9c5f...` |
| RS-03 | 横拍多栏数学题页，含比较题/选择题混排 | 补多栏密集题，观察多栏排序与题组拆分 | `dense_multi_column_page` + `landscape_or_tilted_photo` | OCR=大概率 / Qwen=是 / DeepSeek=否 | `8ca18021...` |
| RS-04 | 语文阅读 + 填空页，竖拍清晰页 | 复核复杂语文页在真实拍照下的 section / blanks 保真 | `complex_chinese_page` | OCR=可能 / Qwen=是 / DeepSeek=否 | `58512088...`、`498bf6ab...` |
| RS-05 | 拼音 + 英文混合页，带少量书写 | 复核 mixed 路由不误掉题，且不把拼音当噪声 | `mixed_pinyin_english` | OCR=可能 / Qwen=是 / DeepSeek=否 | `a95fa987...` |
| RS-06 | 选择题独立页，一页只有选择题 | 复核 options 保留、题组边界、无无关 meta 误收 | `standalone_multiple_choice_page` | OCR=可能 / Qwen=是 / DeepSeek=否 | `1853b2e7...`、`7aac0fcd...` |
| RS-07 | 封面/说明页，可带老师勾改 | 验证 conservative routing，不应产出题目 | `cover_or_instruction_page` + `teacher_markup` | OCR=可能 / Qwen=是 / DeepSeek=否 | `0a511e8d...`、`db5fc846...` |
| RS-08 | 非作业图，如界面截图/包装/说明页 | 验证 non-homework 拒收路径，不应进题库 | `non_homework_image` | OCR=可能 / Qwen=是 / DeepSeek=否 | `db2d0e0a...` |

## 采集与命名规则

- 首批只收 `8` 张；如现场只能拿到部分，最低 `5` 张。
- 一页一图，不混拼图，不收 PDF，不做额外裁剪。
- 命名固定：`repair3g_rs01.jpg` 至 `repair3g_rs08.jpg`
- 每张图同步记录：
  - `sample_id`
  - `拍摄方向`
  - `是否多栏`
  - `是否有老师批改`
  - `预计题目数`
  - `是否允许后续转 fixture`
- 若同一张图同时覆盖多个目标，只算 `1` 张，不再补同质重复样本。

## 调用前命令草案

以下只可作为 3I 执行草案，不得在本轮执行。

### 1. 安全预检

```bash
bash local_check_env.sh
python backend/evalrepair2cached.py --no-paid --only-json-fixtures
python backend/evalrepair2cached.py --no-paid
```

### 2. 批量真实识别草案

`backend/evaluate_repair1_step1.py` 现有默认测试基址是 `http://127.0.0.1:8002`。如果实际测试入口仍是 `8001`，只能在 3I 获得明确确认后替换 `--base`，不得自行猜测。

```bash
python backend/evaluate_repair1_step1.py \
  --base http://127.0.0.1:8002 \
  --samples-dir /ABS/PATH/repair3g_batch1 \
  --phone "$TEST_PHONE" \
  --password "$TEST_PWD" \
  --allow-paid-ocr \
  --max-paid-calls 4 \
  --poll-interval 3 \
  --timeout-sec 90 \
  --upload-gap-sec 13 \
  --json-out /tmp/repair3g_batch1.json
```

### 3. 结果抽样核对草案

```bash
python - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path('/tmp/repair3g_batch1.json').read_text(encoding='utf-8'))
for item in payload.get('results', []):
    print(item['file_name'], item['page_type'], item['question_count'], item['final_status'])
PY
```

## 成本控制策略

- 图片上限：`8` 张
- 批次上限：`1` 批
- `/api/parse-jobs` 提交上限：`8` 次
- 付费 OCR 上限：`4` 次
- DeepSeek 调用上限：`0` 次
- 单图超时：`90` 秒
- 图与图间隔：`13` 秒，避免短时间连续提交造成误判或重复写入

### 停止条件

- 任何请求若指向 `3000/8000` 或生产目录，立即停止
- 任一命令若需要部署、重启、改 `pipeline.py`，立即停止
- 达到 `4` 次 paid OCR 后立即停止，不再补跑
- 连续 `2` 张出现 `failed`、`completed_empty` 或 `max_paid_calls_reached`，立即停止
- 任意样本若意外触发 DeepSeek，立即停止
- 任意样本若出现明显明文密钥/手机号日志泄露，立即停止

## 人工验收表

填表时优先对照 `backend/evaluate_repair1_step1.py` 输出中的 `question_count`、`page_type`、`qwen_recorded`、`fingerprint_written`、`major_failure_reason`。

| 编号 | 目标题目数 | 实际题目数 | 是否误收 meta | 是否漏题 | 是否切题/分组正确 | 是否进入 cache | 是否可加入 fixture | 备注 |
|---|---:|---:|---|---|---|---|---|---|
| RS-01 | 8-15 |  |  |  |  |  |  |  |
| RS-02 | 6-12 |  |  |  |  |  |  |  |
| RS-03 | 8-15 |  |  |  |  |  |  |  |
| RS-04 | 4-8 |  |  |  |  |  |  |  |
| RS-05 | 4-8 |  |  |  |  |  |  |  |
| RS-06 | 4-10 |  |  |  |  |  |  |  |
| RS-07 | 0 |  |  |  |  |  |  |  |
| RS-08 | 0 |  |  |  |  |  |  |  |

### 验收判定口径

- `是否误收 meta`
  - 题目列表中不得出现班级、姓名、页码、说明、版权、封面标题
- `是否漏题`
  - 与人工目视题数相比，允许 `0` 漏题；首批不接受系统性漏整栏
- `是否切题/分组正确`
  - 选择题页需保留选项；语文页需保留 `section_title` / blanks；封面页与非作业图应为 `0` 题
- `是否进入 cache`
  - 以 `fingerprint_written=true` 为主；允许再跑同图时命中缓存
- `是否可加入 fixture`
  - 仅当题目边界、页型、meta 过滤都可人工确认时才允许进入后续真值或 fixture 流程

## 进入 3I 的前置声明

- 3I 必须另行授权，不能自动执行。
- 3I 只允许识别链路小样本真实测，不允许扩展到辅导、部署、重启或生产端口。
