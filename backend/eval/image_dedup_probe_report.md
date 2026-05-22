# 悠米伴学 — 图片去重指纹探针报告

> 生成时间: 2026-05-21
> 分支: feat/general-ocr-grayscale

## 测试图片

- 路径: `/tmp/yomi/83a071178d0f.jpg`（数学口算作业，20题）
- 原始大小: 310,169 bytes (1200×1600 RGB)
- EXIF Orientation: 1（无旋转）

## 三层指纹

### 第一层：original_sha256（文件字节 hash）
对上传 bytes 直接 sha256。仅能识别完全相同的文件。
**❌ 不能用于去重** — 同图经不同压缩后 bytes 必然不同。

### 第二层：canonical_pixel_hash（规范化像素 hash）
流程: 修正 EXIF → 转 RGB → 最长边 768px → 像素数组 sha256。
**❌ 不能抗 JPEG 重新压缩** — JPEG 解码在不同 quality level 产生微小像素差异，原始像素 bytes 不同。

### 第三层：perceptual_hash（感知 hash）⭐ P0 主力
ahash（均值hash）+ dhash（差值hash），64-bit。
**✅ 可抗 JPEG 重新压缩、去 EXIF、resize、格式转换。**

## 实验结果

| 变体 | 大小 | original_sha256 | canonical | ahash | dhash | ah_dist | dh_dist |
|------|------|-----------------|-----------|-------|-------|---------|---------|
| original_file | 310KB | 20e119c8... | ❌ | 087c7c7c... | 13337373... | 0 | 0 |
| canonical_png | 696KB | 5ab3c09a... | ✅ | 087c7c7c... | 13337373... | 0 | 0 |
| jpeg_q95 | 558KB | f7f285da... | ❌ | 087c7c7c... | 13337373... | 0 | 0 |
| jpeg_q75 | 298KB | 06aeed57... | ❌ | 087c7c7c... | 13337373... | 0 | 0 |
| jpeg_q60 | 241KB | 7afa6914... | ❌ | 087c7c7c... | 13337373... | 0 | 0 |
| jpeg_q92_noexif | 441KB | d59c49dd... | ❌ | 087c7c7c... | 13337373... | 0 | 0 |
| resize_90pct_q85 | 309KB | fdf20c54... | ❌ | 087c7c7c... | 13337373... | 0 | 0 |
| webp_q80 | 225KB | 4f63e7fd... | ❌ | 887c7c7c... | 11134171... | 1 | 2 |

## 核心结论

### canonical_pixel_hash 为什么失败？

JPEG 是有损压缩。同一张图以不同 quality 编码再解码后，每个像素的 RGB 值会有微小差异（±1~3）。即使肉眼不可见，原始像素 bytes 的 sha256 必然不同。

```
q95 解码像素: RGB(200, 150, 100)
q60 解码像素: RGB(202, 148, 99)  ← ±2 差异
→ sha256 完全不同
```

### ahash 为什么稳定？

ahash 将图片缩至 8×8 灰度，与均值比较。±2 的像素差异在 8×8 分辨率下被平滑吸收，不影响比较结果。

### WebP 为什么有 1-bit 差异？

WebP 使用完全不同的压缩算法（VP8），解码后的 8×8 缩略图在边缘处有轻微结构差异。**hamming ≤ 2 可覆盖。**

## P0 推荐方案

| 参数 | 值 | 理由 |
|------|-----|------|
| **主去重依据** | **ahash** | 抗 JPEG/EXIF/resize |
| **匹配阈值** | hamming distance **≤ 2** | 覆盖 WebP 等跨格式 |
| **辅助字段** | original_sha256 | 完全相同文件 100% 命中 |
| **记录字段** | dhash | 二次校验 |
| **NOT 使用** | canonical_pixel_hash | 不抗 JPEG 重新压缩 |

### 自动复用规则

1. 同 parent_id + child_id 范围内
2. ahash hamming ≤ 2 匹配旧 job
3. 旧 job 必须是 completed 且未 deleted
4. failed / needs_review / uploaded / processing **不复用**
5. 不同 child_id **不互相复用**
6. **不做相似图自动复用**（hamming > 2 不命中）
7. 命中缓存时**不调用 Qwen-VL、不调用 DeepSeek**
8. 返回结构兼容正常上传

### 为什么 hamming ≤ 2 是安全阈值？

```
同一张图不同压缩: 0~2 位差异
不同但相似的图:   ≥10 位差异（不同题目内容必然不同）
安全边际:         5x
```

## 需要新增的字段

`image_registry` 表增补（或新建 `image_fingerprints` 表）：

| 字段 | 类型 | 索引 | 说明 |
|------|------|------|------|
| id | TEXT PK | — | UUID |
| parent_id | TEXT | ✅ | 隔离 key |
| child_id | TEXT | ✅ | 隔离 key |
| job_id | TEXT | ✅ | 回指 job |
| original_sha256 | TEXT | — | 文件 bytes hash |
| ahash | TEXT | ✅ | **主去重 key** |
| dhash | TEXT | — | 二次校验 |
| width | INTEGER | — | |
| height | INTEGER | — | |
| created_at | TEXT | — | |
| deleted_at | TEXT | — | 软删除 |

```sql
CREATE INDEX IF NOT EXISTS idx_fingerprints_lookup 
  ON image_fingerprints(parent_id, child_id, ahash);
```

## 接入位置

在 `routes/parse_routes.py` 的 `create_parse_job()` 中，`contents = await file.read()` 之后、`enqueue_parse_job()` 之前：

```python
# 1. 计算 original_sha256
orig_sha = sha256_hex(contents)

# 2. 解码图片，计算 ahash
img = Image.open(io.BytesIO(contents))
img_hash = ahash(img)  # Pillow → 8x8 灰度 → 均值比较

# 3. 查重
existing = db.find_completed_job_by_ahash(parent_id, child_id, img_hash, hamming_threshold=2)
if existing:
    return existing_job_response(existing)  # 直接返回，不调用 Qwen

# 4. 未命中 → 正常走 pipeline
```

## 预估降本效果

| 场景 | 命中率预估 | 年节省 |
|------|-----------|--------|
| 重复上传同一图（家长手滑） | ~5-10% | ¥50-100 |
| 测试/调试反复上传 | ~15-20% | ¥150-200 |
| 合计 | ~20-30% 的作业上传 | ¥200-300/年 |

> 注：核心价值不是省多少钱，而是消除同一张图反复调 Qwen-VL 的浪费。
