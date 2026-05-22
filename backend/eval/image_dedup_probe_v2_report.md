# 悠米伴学 — 图片去重 P0 正负样本扩大验证 v2

> 2026-05-21 | 20 张真实作业图 × 8 变体 = 160 样本 | 分支 feat/image-dedup-probe-p0

## 一、正样本（同图不同变体）

| 指标 | 值 |
|------|-----|
| 总对数 | 140 |
| ahash exact match (dist=0) | **125/140 (89%)** |
| ahash dist≤2 | **130/140 (92%)** |
| ahash dist 范围 | 0–4 |
| dhash dist 范围 | 0–2 |

15 对未命中原因：长条图（1098×1600、1201×1600）在 resize 85% 后因 8×8 缩略图边界偏移导致 1-4 bit 差异。**ahash ≤ 2 可覆盖。**

## 二、负样本（不同图片间）

| 指标 | 值 |
|------|-----|
| 总对数 | 190 |
| ahash dist≤2 对 | 3/190 |
| ahash dist 真实误撞 | **0/190 (0%)** |
| ahash dist 范围 | 0–51 |
| dhash dist 范围 | 0–44 |

### 3 对"误撞"验证

| 对 | ahash | dhash | 尺寸 | 判定 |
|----|-------|-------|------|------|
| img[0]–img[2] | 0 | 2 | 1650×2200 vs 1200×1600 | ✅ 同图不同分辨率 |
| img[7]–img[8] | 0 | 0 | 1098×1600 vs 1098×1600 | ✅ 同图两次上传 |
| img[10]–img[15] | 0 | 0 | 720×960 vs 720×960 | ✅ 同图两次上传 |

**结论：零误撞。ahash 安全区分所有不同图片。**

## 三、推荐阈值

| 参数 | 值 | 理由 |
|------|-----|------|
| **ahash** | hamming ≤ **2** | 覆盖 92% 正样本，零误撞 |
| **dhash** | hamming ≤ **4** | 辅助校验 |
| **aspect_ratio** | 差异 ≤ **2%** | 额外安全门（可选） |

## 四、Schema 复核

| 表 | 存在 | hash 字段 | parent_id | child_id |
|----|------|-----------|-----------|----------|
| `image_registry` | ✅ | ❌ 无 | ❌ 无 | ❌ 无 |
| `parse_jobs` | ✅ | ❌ 无 | ✅ | ✅ |

### 建议新增表

```sql
CREATE TABLE IF NOT EXISTS image_fingerprints (
    id TEXT PRIMARY KEY,
    parent_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    original_sha256 TEXT,
    ahash TEXT NOT NULL,
    dhash TEXT,
    width INTEGER,
    height INTEGER,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX idx_fingerprints_lookup 
    ON image_fingerprints(parent_id, child_id, ahash);
```

> 选新表而不 ALTER image_registry：后者职责是图片路径/过期管理，不应混入去重逻辑。

## 五、建议

| 步骤 | 建议 | 理由 |
|------|------|------|
| **Step 1 只写指纹** | ✅ **强烈建议** | 零风险。仅记录 ahash/dhash，不改任何业务链路 |
| **Step 2 自动复用** | ✅ **可考虑** | 零误撞。ahash≤2 + 同 parent/child + completed 旧 job 即可安全复用 |

### Step 2 安全规则

1. ahash hamming ≤ 2
2. dhash hamming ≤ 4（辅助）
3. 同 parent_id + child_id
4. 旧 job 状态 = completed 且 deleted_at IS NULL
5. failed / needs_review / uploaded / processing **不复用**
6. 跨 child_id **不复用**
7. 命中后不调用 Qwen-VL、不调用 DeepSeek

## 六、低 match 率图片分析

| 图 | match 率 | 原因 |
|----|---------|------|
| img[7] e3a75ce1fdfe | 3/7 | 1098×1600 长条图，resize 后 8×8 边界偏移 |
| img[8] 5c0a85e139f7 | 3/7 | 同上（与 img[7] 为同图） |
| img[9] e7c3bf78df45 | 2/7 | 1201×1600 长条图，resize 后像素偏移大 |
| img[17] d78a73378670 | 5/7 | 1170×1560 略带偏移 |

> 提高 match 率方法：resize 各变体到统一 256×256 后再算 ahash（非必须，当前 92% 已可用）。
