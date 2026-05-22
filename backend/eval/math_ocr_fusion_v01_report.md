# 数学 OCR Fusion 探针 v01 报告

> 生成时间：2026-05-21 13:35:11
> 探针版本：v01（分层置信度 + 多策略 answer_bbox + 安全门）

## 一、样本概况

| 指标 | 数值 |
|------|------|
| unique_image_count | 4 |
| sample_job_count | 4 |
| math_question_count | 63 |

### 图像清单

| # | ahash | file_name | math_q | total_q | image_size |
|---|-------|-----------|--------|---------|------------|
| 1 | 7f2397fef7ef | 悠米伴学前端ui视觉基准图.jpg | 7 | 13 | 1536×1024 |
| 2 | 087c7c7c7c7c | 83a071178d0f.jpg | 20 | 20 | 1200×1600 |
| 3 | 0000ffffffff | mmexport1778637138719.jpg | 27 | 27 | 719×1600 |
| 4 | 00fffffdf8fc | wx_camera_1778591660078.jpg | 9 | 10 | 719×1600 |

## 二、核心指标（balanced 阈值: strong≥0.50, medium≥0.35）

| 指标 | 数值 |
|------|------|
| ocr_block_count | 472 |
| strong_match_count | 30 |
| medium_match_count | 8 |
| weak_skipped_count | 25 |
| question_bbox_match_rate_strong_only | 47.6% |
| question_bbox_match_rate_strong_plus_medium | 60.3% |
| answer_bbox_candidate_count | 147 |
| answer_bbox_candidate_rate | 76.3% |
| answer_bbox_rejected_count | 2045 |
| suspected_false_match_count | 6 |
| meta_filtered_count | 124 |

## 三、三阈值对比

| 阈值档 | strong | medium | weak | S+M 率 |
|--------|--------|--------|------|--------|
| strict | 0 | 30 | 33 | 47.6% |
| balanced | 30 | 8 | 25 | 60.3% |
| loose | 38 | 10 | 15 | 76.2% |

## 四、按图像类型结果

### Image 1: 悠米伴学前端ui视觉基准图.jpg (c38d4af26160)

- questions=7, OCR blocks=259
- strong=4 medium=2 weak=1
- answer_accepted=65 rejected=795
- meta_filtered=26 suspected_false=0

| # | tier | score | question_text | bbox | answers |
|---|------|-------|---------------|------|---------|
| 6160-3 | strong | 0.5789 | 4.5×3=13.5(元) | (565,634) | (, 5.12-5.18 |
| 6160-4 | strong | 0.6484 | 5.18/数学S习册期S页 | (1016,601) |  |
| 6160-6 | strong | 0.55 | 4.5×3=13.5 | (862,667) | (, 5.12-5.18, 57% |
| 160-12 | strong | 0.571 | 13.5+9.5=23(元) | (567,669) | (, 5.12-5.18 |

### Image 2: 83a071178d0f.jpg (95d1b03f7ca8)

- questions=20, OCR blocks=40
- strong=12 medium=6 weak=2
- answer_accepted=29 rejected=425
- meta_filtered=13 suspected_false=0

| # | tier | score | question_text | bbox | answers |
|---|------|-------|---------------|------|---------|
| 7ca8-1 | strong | 0.5 | 8+8= | (671,219) | 9+ |
| 7ca8-5 | strong | 0.5 | 8+4= | (692,417) | 9+, 回/ |
| 7ca8-8 | strong | 0.5 | 9+4= | (318,721) | /3 |
| ca8-10 | strong | 0.5 | 7+8= | (367,787) | /3, 3+9=2, 7+6= |
| ca8-11 | strong | 0.5 | 7+6= | (671,788) |  |

### Image 3: mmexport1778637138719.jpg (264c2bbebf2f)

- questions=27, OCR blocks=76
- strong=12 medium=0 weak=15
- answer_accepted=53 rejected=643
- meta_filtered=36 suspected_false=6

| # | tier | score | question_text | bbox | answers |
|---|------|-------|---------------|------|---------|
| bf2f-0 | strong | 0.55 | 48-5= | (38,513) | 97-6=, =91, 32-20= |
| bf2f-1 | strong | 0.55 | 97-6= | (293,546) | =91, 32-20=, A2 |
| bf2f-2 | strong | 0.55 | 32-20= | (545,549) | A2 |
| bf2f-3 | strong | 0.55 | 65-4= | (40,582) | 97-6=, =91, 32-20= |
| bf2f-4 | strong | 0.55 | 29-8= | (295,612) | 36-3= |

### Image 4: wx_camera_1778591660078.jpg (8eabfd355fe7)

- questions=9, OCR blocks=97
- strong=2 medium=0 weak=7
- answer_accepted=0 rejected=182
- meta_filtered=49 suspected_false=0

| # | tier | score | question_text | bbox | answers |
|---|------|-------|---------------|------|---------|
| 5fe7-1 | strong | 0.5 | 2、 | (360,1245) |  |
| 5fe7-5 | strong | 0.5 | 6.1g | (697,171) |  |


## 五、Pipeline 灰度门槛判断

| 条件 | 要求 | 实际 | 达标 |
|------|------|------|------|
| unique_image_count ≥ 5 | 4 | ❌ |
| S+M 匹配率 ≥ 70% | 60.3% | ❌ |
| strong 占比不过低 | 30/63 | ✅ |
| answer_bbox_candidate_rate ≥ 60% | 76.3% | ✅ |
| suspected_false 可控 | 6 | ✅ |
| weak 被正确跳过 | 25 weak | ✅ |
| meta 误匹配接近 0 | 124 | ❌ |

### 结论：❌ 未达到 pipeline 灰度门槛

### 下一步优先修复项：

1. **样本不足**：当前仅有 4 种不同图像，需补充竖式/填空/比大小/手写/涂改等题型样本。
2. **匹配率不足**：S+M 匹配率 60.3%，需增强 OCR block 文本与 Qwen 题目文本的对齐策略。

---
*探针 v01 自动生成*