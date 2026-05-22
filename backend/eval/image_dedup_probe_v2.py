#!/usr/bin/env python3
"""悠米伴学 — 图片去重 P0 正负样本扩大验证 v2"""
import hashlib, json, os, sys, io, glob
from datetime import datetime
from pathlib import Path
from PIL import Image, ExifTags

CANONICAL_SIZE = 768
HASH_SIZE = 8
OUT_DIR = Path(__file__).parent

def sha256_hex(data): return hashlib.sha256(data).hexdigest()

def get_exif_orientation(img):
    try:
        exif = img._getexif()
        if exif:
            for tag, value in exif.items():
                if ExifTags.TAGS.get(tag) == "Orientation":
                    return value
    except: pass
    return 1

def strip_exif(img):
    data = list(img.getdata())
    clean = Image.new(img.mode, img.size)
    clean.putdata(data)
    return clean

def ahash(img):
    gray = img.convert("L").resize((HASH_SIZE, HASH_SIZE), Image.LANCZOS)
    pixels = list(gray.getdata())
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if p > avg else "0" for p in pixels)
    return hex(int(bits, 2))[2:].zfill(16)

def dhash(img):
    gray = img.convert("L").resize((HASH_SIZE + 1, HASH_SIZE), Image.LANCZOS)
    pixels = list(gray.getdata())
    bits = ""
    for row in range(HASH_SIZE):
        for col in range(HASH_SIZE):
            bits += "1" if pixels[row*(HASH_SIZE+1)+col] > pixels[row*(HASH_SIZE+1)+col+1] else "0"
    return hex(int(bits, 2))[2:].zfill(16)

def hamming(h1, h2):
    if len(h1) != len(h2): return 999
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")

def generate_variants(source_path):
    variants = []
    source = Image.open(source_path)
    def add(name, data, img, note=""):
        variants.append({"name":name,"data":data,"img":img,"note":note})

    with open(source_path, "rb") as f:
        add("original_file", f.read(), source, "原始文件")

    buf = io.BytesIO(); strip_exif(source).save(buf, format="JPEG", quality=95)
    add("jpeg_q95", buf.getvalue(), Image.open(buf), "JPEG q95")

    buf = io.BytesIO(); strip_exif(source).save(buf, format="JPEG", quality=75)
    add("jpeg_q75", buf.getvalue(), Image.open(buf), "JPEG q75")

    buf = io.BytesIO(); strip_exif(source).save(buf, format="JPEG", quality=60)
    add("jpeg_q60", buf.getvalue(), Image.open(buf), "JPEG q60")

    buf = io.BytesIO(); strip_exif(source).save(buf, format="JPEG", quality=92)
    add("jpeg_q92_noexif", buf.getvalue(), Image.open(buf), "去EXIF q92")

    w, h = source.size
    rsz = source.resize((int(w*0.85), int(h*0.85)), Image.LANCZOS)
    buf = io.BytesIO(); strip_exif(rsz).save(buf, format="JPEG", quality=85)
    add("resize_85pct", buf.getvalue(), Image.open(buf), "resize 85%")

    # 最长边 1024
    ratio = 1024 / max(w, h)
    rsz2 = source.resize((int(w*ratio), int(h*ratio)), Image.LANCZOS)
    buf = io.BytesIO(); strip_exif(rsz2).save(buf, format="JPEG", quality=85)
    add("resize_max1024", buf.getvalue(), Image.open(buf), "最长边1024")

    # 最长边 768
    ratio = 768 / max(w, h)
    rsz3 = source.resize((int(w*ratio), int(h*ratio)), Image.LANCZOS)
    buf = io.BytesIO(); strip_exif(rsz3).save(buf, format="JPEG", quality=85)
    add("resize_max768", buf.getvalue(), Image.open(buf), "最长边768")

    return variants

def compute_fingerprints(variants, img_id):
    results = []
    for v in variants:
        img = v["img"]
        results.append({
            "img_id": img_id,
            "variant": v["name"],
            "file_size": len(v["data"]),
            "width": img.size[0],
            "height": img.size[1],
            "aspect_ratio": round(img.size[0]/img.size[1], 4),
            "original_sha256": sha256_hex(v["data"]),
            "ahash": ahash(img),
            "dhash": dhash(img),
        })
    return results

def main():
    # 收集 20 张图
    paths = glob.glob("/tmp/yomi/*.jpg")
    hashes = {}
    for p in paths:
        h = sha256_hex(open(p,'rb').read())
        if h not in hashes: hashes[h] = p

    samples = []
    for h, p in sorted(hashes.items(), key=lambda x: os.path.getsize(x[1]), reverse=True):
        sz = os.path.getsize(p)
        if sz < 50000: continue
        try:
            img = Image.open(p); w, hi = img.size
            if w < 200 or hi < 200: continue
            if w/hi > 10 or hi/w > 10: continue
            samples.append({"path":p,"size":sz,"width":w,"height":hi,"sha":h[:16]})
            if len(samples) >= 20: break
        except: pass

    print(f"📷 {len(samples)} 张真实作业图，每张生成 8 变体 → 共 {len(samples)*8} 样本\n")

    all_results = []
    for idx, s in enumerate(samples):
        path = s["path"]
        variants = generate_variants(path)
        results = compute_fingerprints(variants, idx)
        all_results.extend(results)
        orig = results[0]
        matches = sum(1 for r in results[1:] if r["ahash"] == orig["ahash"])
        print(f"  [{idx:2d}] {os.path.basename(path)[:25]:25s} {s['size']:>7}B  {s['width']}x{s['height']}  ahash_match={matches}/7")

    # ── 正样本分析（同图不同变体）──
    print("\n─── 正样本：同图变体间 ahash/dhash 距离 ───")
    pos_dists_ah = []
    pos_dists_dh = []
    pos_ah_all_match = 0
    pos_ah_th2_match = 0

    for idx in range(len(samples)):
        img_variants = [r for r in all_results if r["img_id"] == idx]
        orig = img_variants[0]
        for v in img_variants[1:]:
            ad = hamming(orig["ahash"], v["ahash"])
            dd = hamming(orig["dhash"], v["dhash"])
            pos_dists_ah.append(ad)
            pos_dists_dh.append(dd)
            if ad == 0: pos_ah_all_match += 1
            if ad <= 2: pos_ah_th2_match += 1

    total_pos = len(pos_dists_ah)
    print(f"  总正样本对: {total_pos}")
    print(f"  ahash distance=0: {pos_ah_all_match}/{total_pos} ({pos_ah_all_match*100//total_pos}%)")
    print(f"  ahash distance≤2: {pos_ah_th2_match}/{total_pos} ({pos_ah_th2_match*100//total_pos}%)")
    print(f"  ahash dist 分布: min={min(pos_dists_ah)} max={max(pos_dists_ah)} avg={sum(pos_dists_ah)/total_pos:.1f}")
    print(f"  dhash dist 分布: min={min(pos_dists_dh)} max={max(pos_dists_dh)} avg={sum(pos_dists_dh)/total_pos:.1f}")

    # ── 负样本分析（不同图 original 之间）──
    print("\n─── 负样本：不同图片 original_file 间 ahash/dhash 距离 ──")
    originals = [r for r in all_results if r["variant"] == "original_file"]
    neg_dists_ah = []
    neg_dists_dh = []
    neg_pairs = []
    danger_pairs = []

    for i in range(len(originals)):
        for j in range(i+1, len(originals)):
            a = originals[i]; b = originals[j]
            ad = hamming(a["ahash"], b["ahash"])
            dd = hamming(a["dhash"], b["dhash"])
            neg_dists_ah.append(ad)
            neg_dists_dh.append(dd)
            neg_pairs.append({"i":a["img_id"],"j":b["img_id"],"ah_dist":ad,"dh_dist":dd})
            if ad <= 4:
                danger_pairs.append({"i":a["img_id"],"j":b["img_id"],"ah_dist":ad,"dh_dist":dd,
                    "w1":a["width"],"h1":a["height"],"w2":b["width"],"h2":b["height"],
                    "ar1":a["aspect_ratio"],"ar2":b["aspect_ratio"]})

    total_neg = len(neg_dists_ah)
    collision_th2 = sum(1 for d in neg_dists_ah if d <= 2)
    collision_th4 = sum(1 for d in neg_dists_ah if d <= 4)
    print(f"  总负样本对: {total_neg}")
    print(f"  ahash distance≤2 误撞: {collision_th2}/{total_neg} ({collision_th2*100//max(total_neg,1)}%)")
    print(f"  ahash distance≤4 误撞: {collision_th4}/{total_neg} ({collision_th4*100//max(total_neg,1)}%)")
    print(f"  ahash dist 分布: min={min(neg_dists_ah)} max={max(neg_dists_ah)} avg={sum(neg_dists_ah)/total_neg:.1f}")
    print(f"  dhash dist 分布: min={min(neg_dists_dh)} max={max(neg_dists_dh)} avg={sum(neg_dists_dh)/total_neg:.1f}")

    if danger_pairs:
        danger_pairs.sort(key=lambda x: x["ah_dist"])
        print(f"\n  ⚠️ 危险对 (ah_dist≤4): {len(danger_pairs)} 对")
        for dp in danger_pairs[:10]:
            print(f"    img[{dp['i']:2d}] - img[{dp['j']:2d}]  ah={dp['ah_dist']}  dh={dp['dh_dist']}  "
                  f"size={dp['w1']}x{dp['h1']} vs {dp['w2']}x{dp['h2']}  ar={dp['ar1']} vs {dp['ar2']}")

    # ── 汇总 ──
    summary = {
        "total_images": len(samples),
        "total_variants_per_image": 8,
        "total_samples": len(all_results),
        "positive": {
            "total_pairs": total_pos,
            "ahash_exact_match": f"{pos_ah_all_match}/{total_pos} ({pos_ah_all_match*100//total_pos}%)",
            "ahash_threshold_2": f"{pos_ah_th2_match}/{total_pos} ({pos_ah_th2_match*100//total_pos}%)",
            "ahash_dist_range": f"{min(pos_dists_ah)}-{max(pos_dists_ah)}",
            "dhash_dist_range": f"{min(pos_dists_dh)}-{max(pos_dists_dh)}",
        },
        "negative": {
            "total_pairs": total_neg,
            "ahash_collision_th2": f"{collision_th2}/{total_neg}",
            "ahash_collision_th4": f"{collision_th4}/{total_neg}",
            "ahash_dist_range": f"{min(neg_dists_ah)}-{max(neg_dists_ah)}",
            "dhash_dist_range": f"{min(neg_dists_dh)}-{max(neg_dists_dh)}",
            "danger_pairs_count": len(danger_pairs),
        },
        "recommended_threshold": {
            "ahash": 2,
            "dhash": 4,
            "aspect_ratio_max_diff": 0.02,
        },
        "step1_write_only": "✅ 建议 — 只写指纹零风险",
        "step2_auto_reuse": "见报告结论",
    }

    with open(OUT_DIR/"image_dedup_probe_v2_result.json", "w") as f:
        json.dump({"summary": summary, "all_results": all_results, "danger_pairs": danger_pairs,
                    "timestamp": datetime.now().isoformat()}, f, indent=2, ensure_ascii=False)

    # ── MD 报告 ──
    md = [
        "# 悠米伴学 — 图片去重 P0 正负样本扩大验证 v2",
        f"\n> {datetime.now().strftime('%Y-%m-%d %H:%M')} | {len(samples)} 张图 × 8 变体 = {len(all_results)} 样本",
        "",
        "## 正样本（同图不同变体）",
        f"- 总对数: {total_pos}",
        f"- ahash exact match (dist=0): {pos_ah_all_match}/{total_pos} (**{pos_ah_all_match*100//total_pos}%**)",
        f"- ahash dist≤2: {pos_ah_th2_match}/{total_pos} (**{pos_ah_th2_match*100//total_pos}%**)",
        f"- ahash dist 范围: {min(pos_dists_ah)}–{max(pos_dists_ah)}",
        f"- dhash dist 范围: {min(pos_dists_dh)}–{max(pos_dists_dh)}",
        "",
        "## 负样本（不同图片间）",
        f"- 总对数: {total_neg}",
        f"- ahash dist≤2 误撞: {collision_th2}/{total_neg}",
        f"- ahash dist≤4 误撞: {collision_th4}/{total_neg}",
        f"- ahash dist 范围: {min(neg_dists_ah)}–{max(neg_dists_ah)}",
        f"- dhash dist 范围: {min(neg_dists_dh)}–{max(neg_dists_dh)}",
    ]

    if danger_pairs:
        md.append(f"\n### ⚠️ 危险对 (ah_dist≤4): {len(danger_pairs)} 对")
        for dp in danger_pairs[:10]:
            md.append(f"- img[{dp['i']}]–img[{dp['j']}] ah={dp['ah_dist']} dh={dp['dh_dist']} "
                      f"{dp['w1']}×{dp['h1']} vs {dp['w2']}×{dp['h2']}")

    step2 = "❌ 不建议 — 存在误撞风险" if collision_th2 > 0 else "✅ 可考虑 — 零误撞"
    md += [
        "",
        "## 推荐阈值",
        f"- ahash: hamming ≤ **2**",
        f"- dhash: hamming ≤ **4**",
        f"- aspect_ratio 差异 ≤ **2%**",
        "",
        "## 建议",
        f"- **Step 1 只写指纹**: ✅ 建议（零风险，为后续自动复用提供数据基础）",
        f"- **Step 2 自动复用**: {step2}",
    ]

    with open(OUT_DIR/"image_dedup_probe_v2_report.md", "w") as f:
        f.write("\n".join(md))

    print(f"\n✅ JSON: {OUT_DIR/'image_dedup_probe_v2_result.json'}")
    print(f"✅ MD:   {OUT_DIR/'image_dedup_probe_v2_report.md'}")

    if collision_th2 == 0:
        print("\n🎉 零误撞！ahash ≤2 可安全区分所有不同图。")
    else:
        print(f"\n⚠️ 存在 {collision_th2} 对误撞，需加 aspect_ratio 安全门或提高阈值。")


if __name__ == "__main__":
    main()
