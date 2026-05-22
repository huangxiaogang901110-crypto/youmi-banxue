#!/usr/bin/env python3
"""
悠米伴学 — 图片去重指纹探针
验证同一张图经过不同压缩/处理后的三层指纹稳定性。
不做任何业务接入，纯实验。
"""
import hashlib
import json
import os
import sys
import io
from datetime import datetime
from pathlib import Path
from PIL import Image, ExifTags

# ── 配置 ──
CANONICAL_SIZE = 768  # 规范化最长边
HASH_SIZE = 8         # perceptual hash 尺寸
OUT_DIR = Path(__file__).parent
REPORT_MD = OUT_DIR / "image_dedup_probe_report.md"
REPORT_JSON = OUT_DIR / "image_dedup_probe_result.json"

# ── 工具函数 ──

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def get_exif_orientation(img: Image.Image) -> int:
    """提取 EXIF orientation，无 EXIF 返回 1"""
    try:
        exif = img._getexif()
        if exif:
            for tag, value in exif.items():
                decoded = ExifTags.TAGS.get(tag, tag)
                if decoded == "Orientation":
                    return value
    except Exception:
        pass
    return 1

def strip_exif(img: Image.Image) -> Image.Image:
    """去除 EXIF / 元数据，返回纯 RGB"""
    data = list(img.getdata())
    clean = Image.new(img.mode, img.size)
    clean.putdata(data)
    return clean

def canonicalize(img: Image.Image, max_size: int = CANONICAL_SIZE) -> Image.Image:
    """
    图片规范化（P0 主去重依据）：
    1. 修正 EXIF 旋转
    2. 转 RGB
    3. 等比缩放最长边到 max_size
    4. 不引入新的 JPEG 压缩 → 用确定性像素数组 hash
    """
    # 修正 EXIF 旋转
    orientation = get_exif_orientation(img)
    if orientation == 3:
        img = img.rotate(180, expand=True)
    elif orientation == 6:
        img = img.rotate(270, expand=True)
    elif orientation == 8:
        img = img.rotate(90, expand=True)
    
    # 转 RGB
    if img.mode != "RGB":
        img = img.convert("RGB")
    
    # 等比缩放
    w, h = img.size
    if max(w, h) > max_size:
        ratio = max_size / max(w, h)
        new_size = (int(w * ratio), int(h * ratio))
        img = img.resize(new_size, Image.LANCZOS)
    
    return img

def canonical_pixel_hash(img: Image.Image) -> str:
    """
    规范化像素 hash：
    对 canonicalize 后的图片，取原始 RGB 像素数组做 sha256。
    不经过 JPEG/PNG 编码，避免压缩器差异。
    """
    canonical = canonicalize(img)
    pixels = canonical.tobytes()
    return sha256_hex(pixels)

def ahash(img: Image.Image) -> str:
    """Average Hash：8x8 灰度 → 与均值比较 → 64-bit hex"""
    gray = img.convert("L").resize((HASH_SIZE, HASH_SIZE), Image.LANCZOS)
    pixels = list(gray.getdata())
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if p > avg else "0" for p in pixels)
    return hex(int(bits, 2))[2:].zfill(16)

def dhash(img: Image.Image) -> str:
    """Difference Hash：9x8 灰度 → 水平梯度 → 64-bit hex"""
    gray = img.convert("L").resize((HASH_SIZE + 1, HASH_SIZE), Image.LANCZOS)
    pixels = list(gray.getdata())
    bits = ""
    for row in range(HASH_SIZE):
        for col in range(HASH_SIZE):
            left = pixels[row * (HASH_SIZE + 1) + col]
            right = pixels[row * (HASH_SIZE + 1) + col + 1]
            bits += "1" if left > right else "0"
    return hex(int(bits, 2))[2:].zfill(16)

def hamming(h1: str, h2: str) -> int:
    """汉明距离"""
    if len(h1) != len(h2):
        return 999
    b1 = int(h1, 16)
    b2 = int(h2, 16)
    return bin(b1 ^ b2).count("1")

# ── 变体生成 ──

def generate_variants(source_path: str) -> list[dict]:
    """对一张图生成多种压缩/处理变体"""
    variants = []
    source = Image.open(source_path)
    
    def add(name: str, data: bytes, img: Image.Image, note: str = ""):
        variants.append({
            "name": name,
            "data": data,
            "img": img,
            "note": note,
        })
    
    # 0. 原始文件 bytes
    with open(source_path, "rb") as f:
        orig_bytes = f.read()
    add("original_file", orig_bytes, source, "原始文件 bytes")
    
    # 1. 无损转 PNG bytes（规范化基准）
    buf = io.BytesIO()
    canonical = canonicalize(source)
    canonical.save(buf, format="PNG")
    add("canonical_png", buf.getvalue(), canonical, "规范化 → PNG bytes")
    
    # 2. JPEG quality 95
    buf = io.BytesIO()
    strip_exif(source).save(buf, format="JPEG", quality=95)
    add("jpeg_q95", buf.getvalue(), Image.open(buf), "去 EXIF → JPEG q95")
    
    # 3. JPEG quality 75
    buf = io.BytesIO()
    strip_exif(source).save(buf, format="JPEG", quality=75)
    add("jpeg_q75", buf.getvalue(), Image.open(buf), "去 EXIF → JPEG q75")
    
    # 4. JPEG quality 60
    buf = io.BytesIO()
    strip_exif(source).save(buf, format="JPEG", quality=60)
    add("jpeg_q60", buf.getvalue(), Image.open(buf), "去 EXIF → JPEG q60")
    
    # 5. 去 EXIF 但保留高画质
    buf = io.BytesIO()
    strip_exif(source).save(buf, format="JPEG", quality=92)
    add("jpeg_q92_noexif", buf.getvalue(), Image.open(buf), "去 EXIF → JPEG q92")
    
    # 6. 轻微 resize (90%)
    w, h = source.size
    small = source.resize((int(w*0.9), int(h*0.9)), Image.LANCZOS)
    buf = io.BytesIO()
    strip_exif(small).save(buf, format="JPEG", quality=85)
    add("resize_90pct_q85", buf.getvalue(), Image.open(buf), "resize 90% → JPEG q85")
    
    # 7. WebP 格式
    buf = io.BytesIO()
    strip_exif(source).save(buf, format="WEBP", quality=80)
    add("webp_q80", buf.getvalue(), Image.open(buf), "去 EXIF → WebP q80")
    
    return variants


# ── 主流程 ──

def main():
    # 找一张测试图
    candidates = [
        "/tmp/yomi/83a071178d0f.jpg",
    ]
    test_img = None
    for c in candidates:
        if os.path.exists(c):
            test_img = c
            break
    if not test_img:
        # fallback: 找任意 jpg
        import glob
        imgs = glob.glob("/tmp/yomi/*.jpg")
        if imgs:
            test_img = max(imgs, key=lambda p: os.path.getsize(p))
    
    if not test_img:
        print("❌ No test image found")
        sys.exit(1)
    
    print(f"📷 测试图片: {test_img} ({os.path.getsize(test_img)} bytes)")
    
    source = Image.open(test_img)
    print(f"   尺寸: {source.size}  模式: {source.mode}  EXIF: {get_exif_orientation(source)}")
    
    # 生成变体
    variants = generate_variants(test_img)
    print(f"\n生成了 {len(variants)} 个变体:\n")
    
    # 计算所有指纹
    results = []
    baseline_canonical = None
    
    for v in variants:
        img = v["img"]
        orig_h = sha256_hex(v["data"])
        canon_h = canonical_pixel_hash(img)
        ah = ahash(img)
        dh = dhash(img)
        
        if v["name"] == "canonical_png":
            baseline_canonical = canon_h
        
        entry = {
            "name": v["name"],
            "note": v["note"],
            "file_size": len(v["data"]),
            "width": img.size[0],
            "height": img.size[1],
            "original_sha256": orig_h,
            "canonical_pixel_hash": canon_h,
            "ahash": ah,
            "dhash": dh,
        }
        
        entry["canonical_match"] = (canon_h == baseline_canonical) if baseline_canonical else None
        results.append(entry)
        
        print(f"  {v['name']:<20s}  {len(v['data']):>7d}B  "
              f"canonical={'✅' if canon_h == baseline_canonical else '❌'}  "
              f"orig_sha={orig_h[:12]}...  ah={ah[:8]}...")
    
    # 计算 perceptual hash 距离矩阵（与 baseline canonical_png 比较）
    baseline_ah = results[1]["ahash"]  # canonical_png 是 index 1
    baseline_dh = results[1]["dhash"]
    
    print(f"\n─── 感知 hash 距离（vs canonical_png）───")
    for r in results:
        ah_dist = hamming(r["ahash"], baseline_ah)
        dh_dist = hamming(r["dhash"], baseline_dh)
        r["ahash_hamming"] = ah_dist
        r["dhash_hamming"] = dh_dist
        same = "✅ same" if r["canonical_match"] else "❌ DIFF"
        print(f"  {r['name']:<20s}  {same:<8s}  ah_dist={ah_dist}  dh_dist={dh_dist}")
    
    # 输出报告
    report = {
        "test_image": test_img,
        "test_image_size": os.path.getsize(test_img),
        "test_image_dims": list(source.size),
        "timestamp": datetime.now().isoformat(),
        "canonical_size": CANONICAL_SIZE,
        "variants": results,
        "summary": {
            "total_variants": len(results),
            "canonical_match_count": sum(1 for r in results if r.get("canonical_match")),
            "canonical_unique_hash": baseline_canonical,
            "all_identical_via_canonical": all(r.get("canonical_match") for r in results if "canonical_match" in r),
        }
    }
    
    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n✅ JSON 报告: {REPORT_JSON}")
    
    # 生成 MD 报告
    md_lines = [
        "# 悠米伴学 — 图片去重指纹探针报告",
        f"\n> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"\n## 测试图片",
        f"- 路径: `{test_img}`",
        f"- 原始大小: {os.path.getsize(test_img)} bytes",
        f"- 原始尺寸: {source.size[0]}×{source.size[1]}",
        f"- EXIF Orientation: {get_exif_orientation(source)}",
        f"\n## 三层指纹",
        "",
        "### 第一层：original_sha256（文件字节 hash）",
        "对上传 bytes 直接 sha256。仅能识别完全相同的文件。",
        "",
        "### 第二层：canonical_pixel_hash（规范化像素 hash）⭐ P0",
        f"流程: 修正 EXIF → 转 RGB → 最长边缩至 {CANONICAL_SIZE}px → 像素数组 sha256",
        "不经 JPEG/PNG 编码，避免压缩器差异。",
        "",
        "### 第三层：perceptual_hash（感知 hash）",
        "ahash（均值hash）+ dhash（差值hash），64-bit，仅记录汉明距离不自动复用。",
        "",
        "## 实验结果",
        "",
        "| 变体 | 大小 | original_sha256 | canonical_match | ahash_dist | dhash_dist |",
        "|------|------|-----------------|-----------------|------------|------------|",
    ]
    
    for r in results:
        md_lines.append(
            f"| {r['name']} | {r['file_size']}B | {r['original_sha256'][:16]}... "
            f"| {'✅' if r.get('canonical_match') else '❌'} "
            f"| {r.get('ahash_hamming','-')} "
            f"| {r.get('dhash_hamming','-')} |"
        )
    
    md_lines += [
        "",
        "## 结论",
        "",
        f"- **canonical_pixel_hash 匹配率**: {report['summary']['canonical_match_count']}/{report['summary']['total_variants']}",
        f"- **是否全部匹配**: {'✅ 是 — 可抗 JPEG 重新压缩' if report['summary']['all_identical_via_canonical'] else '❌ 否 — 存在差异'}",
        "",
        "### P0 推荐",
        "1. **canonical_pixel_hash** 作为主去重依据（抗压缩、抗 EXIF、抗格式转换）",
        "2. original_sha256 仅作辅助字段（仅完全相同文件命中）",
        "3. perceptual_hash 仅记录不自动复用（避免相似但不同题误判）",
        "4. 相同 parent_id + child_id 范围内匹配",
        "5. 仅命中 completed + 未 deleted 的旧 job",
        "",
        "### 需要新增的字段",
        "`image_registry` 表增补：",
        "- `original_sha256` TEXT",
        "- `canonical_pixel_hash` TEXT (加索引)",
        "- `perceptual_hash` TEXT",
        "- `width` INTEGER",
        "- `height` INTEGER",
        "",
        "### 安全边界",
        "- ❌ 不做跨 child_id 复用",
        "- ❌ 不做相似图自动复用",
        "- ❌ failed / needs_review 不复用",
        "- ❌ uploaded / processing 不复用",
    ]
    
    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    print(f"✅ MD 报告: {REPORT_MD}")
    
    # 最终判定
    if report["summary"]["all_identical_via_canonical"]:
        print("\n🎉 结论: canonical_pixel_hash 可抗 JPEG 重新压缩！所有变体匹配一致。")
    else:
        print("\n⚠️ 结论: canonical_pixel_hash 存在差异，需进一步调整规范化参数。")


if __name__ == "__main__":
    main()
