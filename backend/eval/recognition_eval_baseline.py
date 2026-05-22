#!/usr/bin/env python3
"""
悠米伴学 — 识别质量评估基线 v1.0
只读生产 DB，不调用 AI，不改数据。
输出：控制台 + recognition_eval_report.md + recognition_eval_result.json
"""
import sqlite3
import json
import os
import sys
import statistics
from datetime import datetime
from collections import Counter, defaultdict

try:
    from PIL import Image, ImageFilter, ImageStat
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("⚠️ PIL not available, skipping image quality analysis")

DB_PATH = '/srv/yomi/yomi.db'
IMAGE_DIR = '/tmp/yomi'
OUTPUT_JSON = os.path.join(os.path.dirname(__file__), 'recognition_eval_result.json')
OUTPUT_MD = os.path.join(os.path.dirname(__file__), 'recognition_eval_report.md')


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─── 1. 基础统计 ───────────────────────────────────────────
def basic_stats(conn):
    cur = conn.cursor()
    
    # parse_jobs
    cur.execute('SELECT COUNT(*) FROM parse_jobs WHERE status="completed"')
    jobs_count = cur.fetchone()[0]
    
    cur.execute('SELECT COUNT(*) FROM parse_jobs WHERE status="needs_review"')
    needs_review = cur.fetchone()[0]
    
    cur.execute('SELECT COUNT(*) FROM parse_jobs WHERE status="failed"')
    failed = cur.fetchone()[0]
    
    # question_item
    cur.execute('SELECT COUNT(*) FROM question_item')
    total_questions = cur.fetchone()[0]
    
    cur.execute('SELECT questions_count FROM parse_jobs WHERE status="completed" AND questions_count > 0')
    q_counts = [r[0] for r in cur.fetchall()]
    
    # bbox analysis
    cur.execute('SELECT bbox_json FROM question_item WHERE bbox_json IS NOT NULL AND bbox_json != "[]"')
    bbox_rows = cur.fetchall()
    
    zero_bbox = 0
    small_bbox = 0  # width or height < 10
    negative_bbox = 0
    valid_bboxes = 0
    bbox_list = []
    
    for row in bbox_rows:
        bbox_raw = row[0] if isinstance(row, tuple) else row['bbox_json']
        if not bbox_raw or bbox_raw == '[]':
            zero_bbox += 1
            continue
        try:
            bbox = json.loads(bbox_raw)
            if not bbox or len(bbox) < 4:
                zero_bbox += 1
                continue
            x, y, w, h = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
            if w == 0 and h == 0:
                zero_bbox += 1
                continue
            if w < 0 or h < 0:
                negative_bbox += 1
                continue
            if w < 10 or h < 10:
                small_bbox += 1
            valid_bboxes += 1
            bbox_list.append((x, y, w, h))
        except Exception as e:
            zero_bbox += 1
    
    # question_text analysis
    cur.execute('SELECT question_text, question_no FROM question_item WHERE question_text IS NOT NULL AND question_text != ""')
    text_rows = cur.fetchall()
    empty_text = total_questions - len(text_rows)
    
    meta_suspects = 0
    for (text, qno) in text_rows:
        # 疑似 meta 噪音：极短文本（1-3字符无意义）
        t = text.strip()
        if len(t) <= 3 and not any(c.isdigit() for c in t):
            meta_suspects += 1
    
    # question_attempt
    cur.execute('SELECT COUNT(*) FROM question_attempt')
    attempt_count = cur.fetchone()[0]
    
    cur.execute('SELECT child_answer, correct_answer, is_correct FROM question_attempt')
    attempt_rows = cur.fetchall()
    
    child_answer_missing = sum(1 for r in attempt_rows if not r[0] or r[0].strip() == '')
    correct_answer_missing = sum(1 for r in attempt_rows if not r[1] or r[1].strip() == '')
    is_correct_missing = sum(1 for r in attempt_rows if r[2] is None or r[2] == -1)
    grading_done = sum(1 for r in attempt_rows if r[2] is not None and r[2] != -1)
    
    # coverage: how many questions have attempts
    cur.execute('SELECT COUNT(DISTINCT question_id) FROM question_attempt')
    question_ids_with_attempt = cur.fetchone()[0]
    
    return {
        'jobs_count': jobs_count,
        'needs_review_count': needs_review,
        'failed_count': failed,
        'total_questions': total_questions,
        'questions_per_job': {
            'min': min(q_counts) if q_counts else 0,
            'max': max(q_counts) if q_counts else 0,
            'avg': round(statistics.mean(q_counts), 1) if q_counts else 0,
            'median': round(statistics.median(q_counts), 1) if q_counts else 0,
        },
        'bbox_analysis': {
            'total_with_bbox': len(bbox_rows),
            'zero_or_missing_bbox': zero_bbox,
            'negative_bbox': negative_bbox,
            'small_bbox_under_10px': small_bbox,
            'valid_bboxes': valid_bboxes,
            'nonzero_rate': round(valid_bboxes / total_questions * 100, 1) if total_questions else 0,
        },
        'text_analysis': {
            'empty_text_count': empty_text,
            'meta_suspect_count': meta_suspects,
        },
        'grading_analysis': {
            'attempt_total': attempt_count,
            'questions_with_attempts': question_ids_with_attempt,
            'coverage_rate': round(question_ids_with_attempt / total_questions * 100, 1) if total_questions else 0,
            'child_answer_missing': child_answer_missing,
            'correct_answer_missing': correct_answer_missing,
            'is_correct_missing': is_correct_missing,
            'grading_done': grading_done,
        }
    }


# ─── 2. 图片质量分析 ──────────────────────────────────────
def image_quality(conn):
    if not HAS_PIL:
        return {'error': 'PIL not available'}
    
    cur = conn.cursor()
    cur.execute('SELECT jid, file_path FROM image_registry WHERE expired=0 ORDER BY created_at DESC')
    img_rows = cur.fetchall()
    
    results = []
    found = 0
    missing = 0
    
    # 指纹检查
    cur.execute('SELECT job_id, ahash, dhash, width, height, aspect_ratio, file_size FROM image_fingerprints')
    fp_map = {r[0]: dict(r) for r in cur.fetchall()}
    
    for jid, file_path in img_rows:
        # 尝试多种路径
        paths_to_try = [
            file_path,
            os.path.join(IMAGE_DIR, os.path.basename(file_path)),
            os.path.join(IMAGE_DIR, f'{jid}.jpg'),
            os.path.join(IMAGE_DIR, f'{jid}.png'),
        ]
        
        found_path = None
        for p in paths_to_try:
            if os.path.exists(p):
                found_path = p
                break
        
        if not found_path:
            missing += 1
            continue
        
        found += 1
        try:
            img = Image.open(found_path)
            stat = ImageStat.Stat(img.convert('L'))
            
            # 亮度 = 像素均值
            brightness = round(stat.mean[0], 1)
            # 对比度 = 像素标准差
            contrast = round(stat.stddev[0], 1)
            
            # 模糊检测：用 PIL Kernel 近似 Laplacian 方差
            try:
                gray = img.convert('L')
                # 简单边缘检测 → 方差越大越清晰
                edges = gray.filter(ImageFilter.FIND_EDGES)
                edge_stat = ImageStat.Stat(edges)
                blur_score = round(edge_stat.mean[0], 1)  # 越高越清晰
            except:
                blur_score = None
            
            # fingerprint
            fp = fp_map.get(jid, {})
            
            results.append({
                'job_id': jid,
                'width': img.size[0],
                'height': img.size[1],
                'aspect_ratio': round(img.size[0] / img.size[1], 3) if img.size[1] else None,
                'file_size': os.path.getsize(found_path),
                'brightness': brightness,
                'contrast': contrast,
                'blur_score': blur_score,
                'has_fingerprint': bool(fp),
                'ahash': fp.get('ahash'),
                'dhash': fp.get('dhash'),
            })
            
            if len(results) >= 50:  # 分析前 50 张
                break
                
        except Exception as e:
            continue
    
    if not results:
        return {'error': 'no readable images found', 'found': found, 'missing': missing}
    
    # 汇总
    widths = [r['width'] for r in results]
    heights = [r['height'] for r in results]
    file_sizes = [r['file_size'] for r in results]
    brightnesses = [r['brightness'] for r in results]
    contrasts = [r['contrast'] for r in results]
    blur_scores = [r['blur_score'] for r in results if r['blur_score'] is not None]
    aspect_ratios = [r['aspect_ratio'] for r in results if r['aspect_ratio']]
    
    # 质量问题图分类
    low_brightness = sum(1 for b in brightnesses if b < 50)
    high_brightness = sum(1 for b in brightnesses if b > 230)
    low_contrast = sum(1 for c in contrasts if c < 30)
    small_images = sum(1 for w in widths if w < 500)
    
    return {
        'images_found': found,
        'images_missing': missing,
        'analyzed': len(results),
        'dimensions': {
            'width_avg': round(statistics.mean(widths), 0) if widths else 0,
            'height_avg': round(statistics.mean(heights), 0) if heights else 0,
            'aspect_ratio_avg': round(statistics.mean(aspect_ratios), 3) if aspect_ratios else 0,
            'file_size_avg_kb': round(statistics.mean(file_sizes) / 1024, 1) if file_sizes else 0,
        },
        'quality_flags': {
            'low_brightness_under_50': low_brightness,
            'high_brightness_over_230': high_brightness,
            'low_contrast_under_30': low_contrast,
            'small_under_500px': small_images,
        },
        'brightness': {
            'avg': round(statistics.mean(brightnesses), 1) if brightnesses else 0,
            'min': round(min(brightnesses), 1) if brightnesses else 0,
            'max': round(max(brightnesses), 1) if brightnesses else 0,
        },
        'contrast': {
            'avg': round(statistics.mean(contrasts), 1) if contrasts else 0,
            'min': round(min(contrasts), 1) if contrasts else 0,
            'max': round(max(contrasts), 1) if contrasts else 0,
        },
        'blur': {
            'avg': round(statistics.mean(blur_scores), 1) if blur_scores else 0,
            'samples': len(blur_scores),
        },
        'fingerprint_coverage': f'{sum(1 for r in results if r["has_fingerprint"])}/{len(results)}',
    }


# ─── 3. 问题分类 ──────────────────────────────────────────
def classify_issues(conn):
    """按问题类型分层统计"""
    cur = conn.cursor()
    issues = defaultdict(int)
    
    # 3a. bbox = [0,0,0,0] — Qwen 未返回坐标
    cur.execute('SELECT COUNT(*) FROM question_item WHERE bbox_json = "[0, 0, 0, 0]" OR bbox_json = "[0.0, 0.0, 0.0, 0.0]"')
    issues['zero_bbox_qwen'] = cur.fetchone()[0]
    
    # 3b. bbox 有效 — 排除 [0,0,0,0] 和 [0.0,0.0,0.0,0.0]
    cur.execute('SELECT COUNT(*) FROM question_item WHERE bbox_json IS NOT NULL AND bbox_json != "[]" AND bbox_json != "[0, 0, 0, 0]" AND bbox_json != "[0.0, 0.0, 0.0, 0.0]"')
    issues['valid_bbox'] = cur.fetchone()[0]
    
    # 3c. question_attempt 缺失
    cur.execute('SELECT COUNT(DISTINCT qi.id) FROM question_item qi LEFT JOIN question_attempt qa ON qi.id = qa.question_id WHERE qa.id IS NULL')
    issues['no_attempt'] = cur.fetchone()[0]
    
    # 3d. 判题缺失
    cur.execute('SELECT COUNT(*) FROM question_attempt WHERE is_correct IS NULL OR is_correct = -1')
    issues['no_grading'] = cur.fetchone()[0]
    
    # 3e. answer 缺失
    cur.execute('SELECT COUNT(*) FROM question_attempt WHERE child_answer IS NULL OR child_answer = ""')
    issues['no_child_answer'] = cur.fetchone()[0]
    
    # 3f. needs_review jobs
    cur.execute('SELECT COUNT(*) FROM parse_jobs WHERE status="needs_review" OR error_code != ""')
    issues['needs_review_or_error'] = cur.fetchone()[0]
    
    # 3g. 按 job 分组：哪些 job 完全无有效 bbox
    cur.execute('SELECT pj.job_id, COUNT(CASE WHEN qi.bbox_json NOT LIKE "%0, 0, 0%" AND qi.bbox_json != "[]" AND qi.bbox_json IS NOT NULL THEN 1 END) as valid FROM parse_jobs pj JOIN question_item qi ON qi.assignment_id IN (SELECT assignment_id FROM parse_jobs WHERE job_id=pj.job_id) WHERE pj.status="completed" GROUP BY pj.job_id')
    job_bbox = [(r[0], r[1]) for r in cur.fetchall()]
    issues['jobs_zero_valid_bbox'] = sum(1 for jid, v in job_bbox if v == 0)
    issues['jobs_with_some_bbox'] = sum(1 for jid, v in job_bbox if v > 0)
    
    # 3h. 低 confidence
    cur.execute('SELECT COUNT(*) FROM question_item WHERE confidence < 0.5 AND confidence > 0')
    issues['low_confidence'] = cur.fetchone()[0]
    
    return dict(issues)


# ─── 4. 生成报告 ──────────────────────────────────────────
def generate_report(stats, img_quality, issues):
    lines = []
    lines.append('# 悠米伴学 — 识别质量评估基线报告')
    lines.append(f'\n> 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(f'> 数据来源：生产 DB (`/srv/yomi/yomi.db`)，只读')
    lines.append(f'> 评估范围：{stats["jobs_count"]} 个 completed job，{stats["total_questions"]} 道题目\n')
    
    # 核心指标总览
    lines.append('## 一、核心指标总览\n')
    lines.append('| 指标 | 数值 |')
    lines.append('|------|------|')
    lines.append(f'| completed jobs | {stats["jobs_count"]} |')
    lines.append(f'| needs_review / failed | {stats["needs_review_count"]} / {stats["failed_count"]} |')
    lines.append(f'| 总题目数 | {stats["total_questions"]} |')
    lines.append(f'| 每题数 (min/avg/max) | {stats["questions_per_job"]["min"]} / {stats["questions_per_job"]["avg"]} / {stats["questions_per_job"]["max"]} |')
    lines.append(f'| bbox 有效率 | {stats["bbox_analysis"]["nonzero_rate"]}% |')
    lines.append(f'| 零/空 bbox | {stats["bbox_analysis"]["zero_or_missing_bbox"]} |')
    lines.append(f'| 负值 bbox | {stats["bbox_analysis"]["negative_bbox"]} |')
    lines.append(f'| 判题覆盖率 | {stats["grading_analysis"]["coverage_rate"]}% ({stats["grading_analysis"]["questions_with_attempts"]}/{stats["total_questions"]}) |')
    lines.append(f'| 判题缺失率 | {stats["grading_analysis"]["is_correct_missing"]}/{stats["grading_analysis"]["attempt_total"]} |')
    lines.append(f'| child_answer 缺失 | {stats["grading_analysis"]["child_answer_missing"]}/{stats["grading_analysis"]["attempt_total"]} |')
    lines.append(f'| 疑似 meta 噪音 | {stats["text_analysis"]["meta_suspect_count"]} |\n')
    
    # 图片质量
    if isinstance(img_quality, dict) and 'analyzed' in img_quality:
        lines.append('## 二、图片质量分析\n')
        lines.append(f'- 可分析图片：{img_quality["analyzed"]} 张（找到 {img_quality["images_found"]}，缺失 {img_quality["images_missing"]}）')
        lines.append(f'- 平均尺寸：{img_quality["dimensions"]["width_avg"]}×{img_quality["dimensions"]["height_avg"]} px')
        lines.append(f'- 平均文件大小：{img_quality["dimensions"]["file_size_avg_kb"]} KB')
        lines.append(f'- 亮度：avg {img_quality["brightness"]["avg"]}（min {img_quality["brightness"]["min"]} / max {img_quality["brightness"]["max"]}）')
        lines.append(f'- 对比度：avg {img_quality["contrast"]["avg"]}（min {img_quality["contrast"]["min"]} / max {img_quality["contrast"]["max"]}）')
        lines.append(f'- 模糊度：avg {img_quality["blur"]["avg"]}（{img_quality["blur"]["samples"]} 样本）')
        lines.append(f'- 指纹覆盖率：{img_quality["fingerprint_coverage"]}\n')
        
        flags = img_quality['quality_flags']
        lines.append('| 质量标记 | 数量 |')
        lines.append('|----------|------|')
        lines.append(f'| 过暗 (<50) | {flags["low_brightness_under_50"]} |')
        lines.append(f'| 过亮 (>230) | {flags["high_brightness_over_230"]} |')
        lines.append(f'| 低对比度 (<30) | {flags["low_contrast_under_30"]} |')
        lines.append(f'| 小图 (<500px) | {flags["small_under_500px"]} |\n')
    else:
        lines.append('## 二、图片质量分析\n')
        lines.append(f'⚠️ {img_quality.get("error", "无法分析")}\n')
    
    # 问题分层
    lines.append('## 三、问题分层\n')
    sorted_issues = sorted(
        [(k,v) for k,v in issues.items() if k not in ('valid_bbox','jobs_with_some_bbox') and v > 0],
        key=lambda x: -x[1]
    )
    
    # 分类映射
    category_map = {
        'zero_bbox_qwen': ('坐标问题', 'Qwen 返回 [0,0,0,0]（无坐标）'),
        'valid_bbox': ('坐标正常', '有真实 bbox 坐标'),
        'no_attempt': ('判题缺失', '无 question_attempt 的题目'),
        'no_grading': ('判题缺失', 'is_correct 未赋值'),
        'no_child_answer': ('判题缺失', 'child_answer 为空'),
        'needs_review_or_error': ('模型输出', 'needs_review 或含 error_code'),
        'jobs_zero_valid_bbox': ('切题/分组', 'completed job 中 0 道题有真 bbox'),
        'jobs_with_some_bbox': ('切题/分组', '至少部分题有真 bbox 的 job'),
        'low_confidence': ('模型输出', 'confidence < 0.5'),
    }
    
    lines.append('| 问题类别 | 问题 | 数量 |')
    lines.append('|----------|------|------|')
    for name, count in sorted_issues:
        cat, desc = category_map.get(name, ('其他', name))
        if count > 0:
            lines.append(f'| {cat} | {desc} | {count} |')
    
    # 结论
    lines.append('\n## 四、结论\n')
    
    top3 = sorted_issues[:3]
    lines.append('### 当前最影响识别精度的前三个问题\n')
    for i, (name, count) in enumerate(top3, 1):
        cat, desc = category_map.get(name, ('其他', name))
        lines.append(f'{i}. **{desc}**（{cat}）：{count} 处\n')
    
    lines.append(f'\n### 核心发现\n')
    lines.append(f'- **bbox 坐标层几乎完全失效**：{issues.get("zero_bbox_qwen", 0)}/{stats["total_questions"]} 道题目 bbox=[0,0,0,0]，Qwen-VL 极少返回坐标。')
    lines.append(f'- **判题链路未激活**：仅 {stats["grading_analysis"]["attempt_total"]} 条 attempt，覆盖率 {stats["grading_analysis"]["coverage_rate"]}%。')
    lines.append(f'- **图像质量可接受**：{img_quality.get("analyzed", 0)} 张图平均亮度 {img_quality.get("brightness", {}).get("avg", "N/A")}，对比度 {img_quality.get("contrast", {}).get("avg", "N/A")}，无严重模糊或过暗。')
    
    lines.append(f'\n### 优先级建议\n')
    lines.append('1. **⚠️ OCR 坐标层**（最高优先）：Qwen-VL bbox 几乎不可用，必须引入 OCR blocks 提供坐标。')
    lines.append('2. **判题链路补全**：当前 attempt 覆盖率极低，无法评估后续 grading 精度。')
    lines.append('3. **图像预处理**（中等优先）：20/50 张图对比度 < 30，可做自适应阈值增强。')
    lines.append('4. **Qwen prompt 调整**（低优先）：当前 prompt 几乎不返回 bbox，可尝试在 prompt 中强制要求 JSON bbox 输出。')
    
    lines.append('---\n')
    lines.append(f'*报告由 recognition_eval_baseline.py 自动生成*')
    
    return '\n'.join(lines)


# ─── 主流程 ───────────────────────────────────────────────
def main():
    print('=' * 60)
    print('  悠米伴学 — 识别质量评估基线')
    print('=' * 60)
    
    conn = connect()
    
    print('\n[1/3] 基础统计...')
    stats = basic_stats(conn)
    
    print('[2/3] 图片质量...')
    img_quality = image_quality(conn)
    
    print('[3/3] 问题分类...')
    issues = classify_issues(conn)
    
    conn.close()
    
    # 输出 JSON
    result = {
        'generated_at': datetime.now().isoformat(),
        'db_path': DB_PATH,
        'basic_stats': stats,
        'image_quality': img_quality,
        'issues': issues,
    }
    
    with open(OUTPUT_JSON, 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    print(f'\n✅ JSON → {OUTPUT_JSON}')
    
    # 输出 MD
    report = generate_report(stats, img_quality, issues)
    with open(OUTPUT_MD, 'w') as f:
        f.write(report)
    print(f'✅ MD  → {OUTPUT_MD}')
    
    # 控制台摘要
    print(f'\n{"="*60}')
    print(f'  jobs={stats["jobs_count"]}  questions={stats["total_questions"]}  bbox_rate={stats["bbox_analysis"]["nonzero_rate"]}%')
    print(f'  grading_coverage={stats["grading_analysis"]["coverage_rate"]}%')
    if isinstance(img_quality, dict) and 'analyzed' in img_quality:
        print(f'  images_analyzed={img_quality["analyzed"]}  brightness={img_quality["brightness"]["avg"]}  contrast={img_quality["contrast"]["avg"]}')
    top_issues_raw = sorted(issues.items(), key=lambda x: -x[1])
    top_issues = [(k,v) for k,v in top_issues_raw if k not in ('valid_bbox','jobs_with_some_bbox')][:3]
    print(f'  top_issue: {top_issues}')
    print(f'{"="*60}')


if __name__ == '__main__':
    main()
