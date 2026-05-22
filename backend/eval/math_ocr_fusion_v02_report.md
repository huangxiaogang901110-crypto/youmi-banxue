# 数学 OCR Fusion 探针 v02 报告

> 生成时间：2026-05-21 13:53:31
> 探针版本：v02（图像预筛 + 题号权重0.7 + meta收紧）

## 一、图像预筛

| 指标 | 数值 |
|------|------|
| total_image_count | 8 |
| math_homework_image_count | 2 |
| non_math_rejected_count | 5 |
| uncertain_image_count | 1 |
| unique_math_image_count | 2 |

### 图像分类清单

| # | ahash | type | file_name | confidence | reason |
|---|-------|------|-----------|------------|--------|
| 1 | 0000ffffffff | math_homework | mmexport1778637138719.jpg | 0.85 | kw=3 expr=12 eq=29 |
| 2 | 087c7c7c7c7c | math_homework | 83a071178d0f.jpg | 0.75 | math_ops=41 non_math_kw=0 |
| 3 | 80248fbf9fb7 | uncertain | comment_af42804151dbb5d5d | 0.50 | unclear: math_ops=2 seq=4 kw=1 |
| 4 | 7f2397fef7ef | non_math | 悠米伴学前端ui视觉基准图.jpg | 0.90 | ui_elements: ui_hits=4 |
| 5 | fcfcfcfcfcfc | non_math | 1778899310720_f7216604.jp | 0.60 | no_signal: math_ops=1 seq=0 |
| 6 | 00fffffdf8fc | non_math | wx_camera_1778591660078.j | 0.95 | food_label: nutrition=6 food=5 |
| 7 | fcfefefefefc | non_math | 1777176608303.jpeg | 0.60 | no_signal: math_ops=0 seq=0 |
| 8 | f8fcfcfcfcfc | non_math | wx_camera_1778665415404.j | 0.60 | no_signal: math_ops=3 seq=0 |

## 二、Fusion 核心指标（仅 math_homework）

> balanced 阈值: strong≥0.50, medium≥0.35

| 指标 | 数值 |
|------|------|
| math_question_count | 47 |
| ocr_block_count | 116 |
| strong_match_count | 24 |
| medium_match_count | 10 |
| weak_skipped_count | 13 |
| question_bbox_match_rate_strong_only | 51.1% |
| question_bbox_match_rate_strong_plus_medium | 72.3% |
| answer_bbox_candidate_count | 95 |
| answer_bbox_candidate_rate | 79.4% |
| answer_bbox_rejected_count | 1125 |
| suspected_false_match_count | 6 |
| meta_filtered_count | 49 |
| non_math_false_accept_count | 17 |

## 三、三阈值对比

| 阈值档 | strong | medium | weak | S+M 率 |
|--------|--------|--------|------|--------|
| strict | 0 | 24 | 23 | 51.1% |
| balanced | 24 | 10 | 13 | 72.3% |
| loose | 34 | 5 | 8 | 83.0% |

## 四、按数学作业图结果

### Math 1: mmexport1778637138719.jpg (264c2bbebf2f)

- questions=27 OCR blocks=76
- S=12 M=3 W=12
- ans_acc=57 rej=788
- meta_filt=36 suspect=6

| # | score | text | bbox | answers |
|---|-------|------|------|---------|
| bf2f-0 | 0.55 | 48-5= | (38,513) | 97-6=, =91 |
| bf2f-1 | 0.55 | 97-6= | (293,546) | =91, 32-20= |
| bf2f-2 | 0.55 | 32-20= | (545,549) | A2 |
| bf2f-3 | 0.55 | 65-4= | (40,582) | 97-6=, =91 |
| bf2f-4 | 0.55 | 29-8= | (295,612) | 36-3= |

### Math 2: 83a071178d0f.jpg (95d1b03f7ca8)

- questions=20 OCR blocks=40
- S=12 M=7 W=1
- ans_acc=38 rej=337
- meta_filt=13 suspect=0

| # | score | text | bbox | answers |
|---|-------|------|------|---------|
| 7ca8-1 | 0.55 | 8+8= | (671,219) | 9+ |
| 7ca8-5 | 0.55 | 8+4= | (692,417) | 9+, 回/ |
| 7ca8-8 | 0.55 | 9+4= | (318,721) | /3 |
| ca8-10 | 0.55 | 7+8= | (367,787) | /3, 3+9=2 |
| ca8-11 | 0.55 | 7+6= | (671,788) |  |


## 五、Pipeline 灰度门槛判断

| 条件 | 要求 | 实际 | 达标 |
|------|------|------|------|
| unique_math_image_count ≥ 5 | 2 | ❌ |
| non_math_false_accept = 0 | 17 | ❌ |
| balanced S+M ≥ 70% | 72.3% | ✅ |
| answer_bbox_candidate_rate ≥ 60% | 79.4% | ✅ |
| suspected_false 可控 | 6 | ❌ |
| weak 不生成 bbox | 13 weak | ✅ |
| meta 明显下降 (vs v01: 124) | 49 | ✅ |

### 结论：❌ 未达到 pipeline 灰度门槛

### 下一步优先修复项：

1. **样本不足**：仅 2 种数学作业图，需补充竖式/填空/比大小等题型。
2. **非数学图误接受**：17 题被误认为数学题，需收紧数学题检测 regex。

---
*探针 v02 自动生成*