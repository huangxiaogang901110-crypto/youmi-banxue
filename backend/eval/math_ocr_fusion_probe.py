#!/usr/bin/env python3
"""
悠米伴学 — 数学口算 OCR + Qwen Fusion 探针 v0
只读生产 DB，只调阿里云通用 OCR，不改 pipeline。
验证：OCR blocks 能否为 Qwen 语义题推导 question_bbox / answer_bbox。
"""
import sqlite3
import json
import os
import re
import time
import base64
import statistics
from datetime import datetime
from collections import defaultdict

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

DB_PATH = '/srv/yomi/yomi.db'
OUT_DIR = os.path.join(os.path.dirname(__file__))

# ─── OCR Client (通用 OCR, RecognizeGeneral) ──────────────────
def _get_ocr_client():
    from alibabacloud_ocr_api20210707.client import Client
    from alibabacloud_tea_openapi.models import Config
    key_id = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
    key_secret = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
    if not key_id or not key_secret:
        raise RuntimeError("OCR 凭证未设置")
    config = Config(
        access_key_id=key_id,
        access_key_secret=key_secret,
        endpoint="ocr-api.cn-hangzhou.aliyuncs.com",
    )
    return Client(config)


def call_ocr(image_path: str) -> tuple[list[dict], int]:
    """调用阿里云通用 OCR，返回 blocks 列表 + 耗时 ms"""
    from alibabacloud_ocr_api20210707 import models as ocr_models
    
    with open(image_path, 'rb') as f:
        img_bytes = f.read()
    
    client = _get_ocr_client()
    req = ocr_models.RecognizeGeneralRequest(body=img_bytes)
    
    t0 = time.time()
    resp = client.recognize_general(req)
    elapsed = int((time.time() - t0) * 1000)
    
    blocks = []
    if resp.body and resp.body.data:
        data = json.loads(resp.body.data) if isinstance(resp.body.data, str) else resp.body.data
        words = data.get('prism_wordsInfo', [])
        for w in words:
            pos = w.get('pos', [{}])[0] if w.get('pos') else {}
            blocks.append({
                'text': w.get('word', ''),
                'x': pos.get('x', 0),
                'y': pos.get('y', 0),
                'w': pos.get('w', 0),
                'h': pos.get('h', 0),
                'confidence': None,
            })
    
    return blocks, elapsed


# ─── Text Normalization ──────────────────────────────────────
def normalize_text(t: str) -> str:
    """归一化文本用于相似度比较"""
    t = t.strip()
    t = t.replace(' ', '').replace('\n', '').replace('\r', '')
    t = t.replace('（', '(').replace('）', ')')
    t = t.replace('×', 'x').replace('✕', 'x').replace('*', 'x')
    t = t.replace('÷', '/').replace('➗', '/')
    t = t.replace('＝', '=').replace('﹦', '=')
    t = t.replace('＋', '+').replace('－', '-')
    return t.lower()


def text_similarity(a: str, b: str) -> float:
    """字符级 Jaccard 相似度"""
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    set_a = set(na)
    set_b = set(nb)
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def text_overlap_chars(a: str, b: str) -> int:
    """归一化后共同字符数"""
    na, nb = normalize_text(a), normalize_text(b)
    return len(set(na) & set(nb))


def text_overlap_substr(a: str, b: str, min_len=2) -> float:
    """最长公共子串比率"""
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    max_len = 0
    for i in range(len(na)):
        for j in range(len(nb)):
            k = 0
            while i+k < len(na) and j+k < len(nb) and na[i+k] == nb[j+k]:
                k += 1
            if k > max_len:
                max_len = k
    return max_len / max(len(na), len(nb))


# ─── Math Pattern Detection ──────────────────────────────────
MATH_KEYWORDS = re.compile(
    r'[\+\-\×\÷\=]|口算|计算|填一填|竖式|直接写出|得数|答案'
    r'|\d+\s*[\+\-\×\÷]\s*\d+|比大小|在.*里填|看谁.*对又快',
    re.IGNORECASE
)

QUESTION_NUMBER_PATTERNS = [
    re.compile(r'^(\d+)[\.\、\)）]\s*'),    # "1.", "1、", "1)"
    re.compile(r'^（(\d+)）\s*'),            # "（1）"
    re.compile(r'^\[(\d+)\]\s*'),            # "[1]"
    re.compile(r'^第(\d+)题'),              # "第1题"
]


def is_math_question(text: str) -> bool:
    return bool(MATH_KEYWORDS.search(text)) if text else False


def extract_question_number(text: str) -> int | None:
    for pat in QUESTION_NUMBER_PATTERNS:
        m = pat.search(text.strip())
        if m:
            return int(m.group(1))
    return None


def is_answer_candidate(text: str) -> bool:
    """是否为答案候选块：数字、等号后、括号内"""
    if not text:
        return False
    t = text.strip()
    # 纯数字或简短数字表达式
    if re.match(r'^[\d\s\+\-\×\÷\.\,]+$', t) and len(t) <= 20:
        return True
    # 等号开头
    if t.startswith('=') and len(t) <= 10:
        return True
    return False


def is_meta_noise(text: str) -> bool:
    """过滤明显非题目的 OCR 噪声"""
    noise_patterns = [
        r'^[一二三四五六七八九十]$', r'^\d{1,2}$', r'^$', r'^[A-Za-z]{1,2}$',
        r'^[。，、；：！？]$', r'^第\d+页', r'^班级', r'^姓名', r'^日期',
    ]
    t = text.strip()
    for p in noise_patterns:
        if re.match(p, t):
            return True
    return False


# ─── Fusion Matching ─────────────────────────────────────────
def fuse_questions_with_ocr(questions: list[dict], ocr_blocks: list[dict]) -> dict:
    """
    对每个 Qwen question，在 OCR blocks 中找最佳匹配。
    返回 {question_id: {matched_block, question_bbox, answer_candidates, score}}
    """
    results = {}
    
    # 过滤 meta 噪声
    valid_blocks = [b for b in ocr_blocks if not is_meta_noise(b['text'])]
    meta_filtered = len(ocr_blocks) - len(valid_blocks)
    
    matched_count = 0
    answer_candidates = 0
    low_confidence_skip = 0
    suspicious_large = 0
    
    for qi, q in enumerate(questions):
        qno = q.get('question_no', qi + 1)
        qtext = q.get('question_text', '')
        if not qtext:
            continue
        
        best_block = None
        best_score = 0.0
        best_reason = ''
        
        for bi, blk in enumerate(valid_blocks):
            btext = blk['text']
            
            # 1. 题号锚定
            qno_from_blk = extract_question_number(btext)
            qno_match = 0.0
            if qno_from_blk is not None and qno_from_blk == qno:
                qno_match = 0.5
            
            # 2. 文本相似度
            char_overlap = text_overlap_chars(qtext, btext)
            sub_overlap = text_overlap_substr(qtext, btext)
            jaccard = text_similarity(qtext, btext)
            
            # 综合得分
            score = qno_match + 0.3 * sub_overlap + 0.2 * jaccard
            if char_overlap >= 5:
                score += 0.1
            
            # 数字重叠加权
            q_digits = set(re.findall(r'\d+', qtext))
            b_digits = set(re.findall(r'\d+', btext))
            if q_digits and b_digits:
                digit_overlap = len(q_digits & b_digits) / len(q_digits | b_digits)
                score += 0.1 * digit_overlap
            
            if score > best_score:
                best_score = score
                best_block = blk
                best_reason = f'qno={qno_match:.1f} sub={sub_overlap:.2f} jac={jaccard:.2f} digits={len(q_digits&b_digits) if q_digits else 0}'
        
        # 低置信度安全门
        if best_score < 0.2:
            low_confidence_skip += 1
            results[q['id']] = {
                'matched': False,
                'score': round(best_score, 3),
                'reason': f'score < 0.2 ({best_reason})',
                'question_text': qtext[:60],
            }
            continue
        
        # 大 bbox 检查
        if best_block and (best_block['w'] > 2000 or best_block['h'] > 2000):
            suspicious_large += 1
        
        matched_count += 1
        
        # 答案候选
        ans_candidates = []
        if best_block:
            bx, by = best_block['x'], best_block['y']
            for blk in valid_blocks:
                # 在题目下方或右侧的短文本块
                if blk['y'] > by and abs(blk['x'] - bx) < 500:
                    if is_answer_candidate(blk['text']):
                        ans_candidates.append({
                            'text': blk['text'],
                            'x': blk['x'], 'y': blk['y'], 
                            'w': blk['w'], 'h': blk['h'],
                        })
        
        if ans_candidates:
            answer_candidates += 1
        
        results[q['id']] = {
            'matched': True,
            'score': round(best_score, 3),
            'reason': best_reason,
            'question_text': qtext[:60],
            'question_bbox': {
                'x': best_block['x'], 'y': best_block['y'],
                'w': best_block['w'], 'h': best_block['h'],
            } if best_block else None,
            'answer_candidates': ans_candidates,
        }
    
    return {
        'per_question': results,
        'matched_count': matched_count,
        'answer_candidate_count': answer_candidates,
        'low_confidence_skip': low_confidence_skip,
        'meta_filtered': meta_filtered,
        'suspicious_large': suspicious_large,
        'total_questions': len(questions),
    }


# ─── Report Generation ──────────────────────────────────────
def generate_report(all_jobs: list[dict], output_md: str, output_json: str):
    lines = []
    lines.append('# 数学口算 OCR + Qwen Fusion 探针 v0 报告')
    lines.append(f'\n> 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(f'> 探针样本：{len(all_jobs)} 个 job')
    
    total_q = sum(j['result']['total_questions'] for j in all_jobs if j.get('result'))
    total_matched = sum(j['result']['matched_count'] for j in all_jobs if j.get('result'))
    total_answer = sum(j['result']['answer_candidate_count'] for j in all_jobs if j.get('result'))
    total_skip = sum(j['result']['low_confidence_skip'] for j in all_jobs if j.get('result'))
    total_meta = sum(j['result']['meta_filtered'] for j in all_jobs if j.get('result'))
    total_sus = sum(j['result']['suspicious_large'] for j in all_jobs if j.get('result'))
    total_ocr_blocks = sum(len(j['ocr_blocks']) for j in all_jobs if 'ocr_blocks' in j)
    
    lines.append(f'\n## 一、核心指标\n')
    lines.append('| 指标 | 数值 |')
    lines.append('|------|------|')
    lines.append(f'| sample_job_count | {len(all_jobs)} |')
    lines.append(f'| math_question_count | {total_q} |')
    lines.append(f'| ocr_block_count | {total_ocr_blocks} |')
    lines.append(f'| question_bbox_matched_count | {total_matched} |')
    lines.append(f'| question_bbox_match_rate | {round(total_matched/total_q*100,1) if total_q else 0}% |')
    lines.append(f'| answer_bbox_candidate_count | {total_answer} |')
    lines.append(f'| answer_bbox_candidate_rate | {round(total_answer/total_q*100,1) if total_q else 0}% |')
    lines.append(f'| low_confidence_skipped_count | {total_skip} |')
    lines.append(f'| meta_block_filtered_count | {total_meta} |')
    lines.append(f'| suspicious_large_bbox_count | {total_sus} |')
    
    lines.append(f'\n## 二、逐 Job 细节\n')
    for j in all_jobs:
        jid = j['job_id'][:12]
        lines.append(f'### {jid} — {j.get("file_name", "")}\n')
        if not j.get('result'):
            lines.append(f'- ⚠️ OCR 失败: {j.get("ocr_error", "unknown")}')
            lines.append('')
            continue
        lines.append(f'- questions={j["result"]["total_questions"]}, OCR blocks={len(j.get("ocr_blocks",[]))}')
        lines.append(f'- matched={j["result"]["matched_count"]}, answer_candidates={j["result"]["answer_candidate_count"]}')
        lines.append(f'- low_confidence_skip={j["result"]["low_confidence_skip"]}, meta_filtered={j["result"]["meta_filtered"]}')
        
        if j['result']['matched_count'] > 0:
            lines.append(f'\n| # | question | score | bbox | answers |')
            lines.append(f'|---|----------|-------|------|---------|')
            for qid, qr in j['result']['per_question'].items():
                if qr.get('matched'):
                    bbox = qr.get('question_bbox', {})
                    ans = ', '.join([a['text'] for a in qr.get('answer_candidates', [])])
                    lines.append(f'| {qid[-6:]} | {qr["question_text"][:30]} | {qr["score"]} | ({bbox.get("x",0)},{bbox.get("y",0)}) | {ans[:30]} |')
        lines.append('')
    
    # 结论
    lines.append(f'\n## 三、结论\n')
    match_rate = round(total_matched/total_q*100, 1) if total_q else 0
    answer_rate = round(total_answer/total_q*100, 1) if total_q else 0
    
    lines.append(f'### 1. OCR blocks 是否足够覆盖数学题文本？\n')
    if total_ocr_blocks >= total_q * 2:
        lines.append(f'✅ 是。平均每题 {round(total_ocr_blocks/total_q,1)} 个 OCR block，覆盖充足。')
    else:
        lines.append(f'⚠️ 边缘。平均每题仅 {round(total_ocr_blocks/total_q,1)} 个 block。')
    
    lines.append(f'\n### 2. Fusion v0 是否能稳定找到 question_bbox？\n')
    if match_rate >= 70:
        lines.append(f'✅ 是。匹配率 {match_rate}%，足够稳定。')
    elif match_rate >= 40:
        lines.append(f'⚠️ 部分可用。匹配率 {match_rate}%，需要增强匹配策略。')
    else:
        lines.append(f'❌ 否。匹配率仅 {match_rate}%，纯文本匹配不足以推导 bbox。')
    
    lines.append(f'\n### 3. answer_bbox 是否能先在口算/计算题中推导？\n')
    if answer_rate >= 50:
        lines.append(f'✅ 是。{answer_rate}% 题目有 answer 候选。')
    elif answer_rate >= 20:
        lines.append(f'⚠️ 部分可行。{answer_rate}% 有候选，需优化候选策略。')
    else:
        lines.append(f'❌ 否。仅 {answer_rate}% 有候选，需更强的空间推理。')
    
    lines.append(f'\n### 4. 最大失败原因\n')
    if total_skip > total_q * 0.5:
        lines.append(f'**低置信度跳过**：{total_skip}/{total_q} 题相似度不足，OCR 文本与 Qwen 题目文本差异大。')
    elif total_meta > total_ocr_blocks * 0.3:
        lines.append(f'**OCR 噪声多**：{total_meta} 个 block 被过滤为 meta/噪声。')
    else:
        lines.append(f'**综合**：匹配={total_matched}/{total_q}，跳过={total_skip}，需多策略融合。')
    
    lines.append(f'\n### 5. 是否值得进入 pipeline 灰度？\n')
    if match_rate >= 60:
        lines.append('✅ 建议进入 pipeline 灰度。')
    else:
        lines.append('⚠️ 暂不建议。先增强匹配策略（数字重叠 + 题号锚定 + 空间排序）。')
    
    lines.append(f'\n### 6. 最小改动建议\n')
    lines.append('- 在 `pipeline.py` 中新增 `fuse_ocr_bbox()` 函数')
    lines.append('- 从 `question_cutter.py` 中提取 OCR blocks')
    lines.append('- 用题号 + 数字重叠做第一轮匹配，文本相似度做第二轮')
    lines.append('- 低置信度（score < 0.2）不生成 bbox，维持 [0,0,0,0]')
    
    lines.append(f'\n### 7. 是否仍需图像预处理？\n')
    lines.append('当前 OCR 可以识别口算题文本，但 40% 图像对比度 < 30（来自基线评估）。')
    lines.append('建议在做 pipeline 灰度前先加轻量预处理（自适应阈值），不阻塞当前进度。')
    
    lines.append(f'\n### 8. 是否需要暂缓判题链路修复？\n')
    lines.append('判题链路修复（attempt 覆盖率 0.3%）是独立问题，不阻塞 OCR Fusion。可以并行。')
    
    lines.append('\n---\n*探针脚本自动生成*')
    
    report = '\n'.join(lines)
    with open(output_md, 'w') as f:
        f.write(report)
    
    # JSON
    with open(output_json, 'w') as f:
        json.dump(all_jobs, f, indent=2, ensure_ascii=False, default=str)
    
    return report


# ─── Main ────────────────────────────────────────────────────
def main():
    print('=' * 60)
    print('  数学口算 OCR + Qwen Fusion 探针 v0')
    print('=' * 60)
    
    # ── 选样本 ──
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute("""
        SELECT pj.job_id, pj.file_name, pj.questions_count,
               ir.file_path
        FROM parse_jobs pj
        JOIN image_registry ir ON pj.job_id = ir.jid
        WHERE pj.status = 'completed' AND pj.questions_count > 0
        ORDER BY pj.created_at DESC
        LIMIT 200
    """)
    
    sample_jobs = []
    for r in cur.fetchall():
        jid = r['job_id']
        path = r['file_path']
        if not os.path.exists(path):
            path = f'/tmp/yomi/{jid}.jpg'
        if not os.path.exists(path):
            continue
        
        # Read questions from parse_jobs.data JSON field
        cur2 = conn.execute("SELECT data FROM parse_jobs WHERE job_id=?", (jid,))
        row2 = cur2.fetchone()
        questions = []
        if row2:
            try:
                job_data = json.loads(row2['data'])
                raw_questions = job_data.get('questions', [])
                for qi, q in enumerate(raw_questions):
                    qtext = q.get('question_text', '') or q.get('text', '') or ''
                    if is_math_question(qtext):
                        questions.append({
                            'id': f'{jid}-{qi}',
                            'question_no': qi + 1,
                            'question_text': qtext,
                            'bbox_json': json.dumps(q.get('bbox', [])),
                        })
            except:
                pass
        
        if len(questions) >= 3:
            sample_jobs.append({
                'job_id': jid,
                'file_name': r['file_name'],
                'questions_count': r['questions_count'],
                'math_question_count': len(questions),
                'image_path': path,
                'questions': questions,
            })
        
        if len(sample_jobs) >= 5:
            break
    
    conn.close()
    
    print(f'\n📊 选中 {len(sample_jobs)} 个数学 job')
    for j in sample_jobs:
        print(f'  {j["job_id"][:12]}  math={j["math_question_count"]}/{j["questions_count"]}')
    
    # ── OCR + Fusion ──
    print(f'\n🔍 调用通用 OCR（RecognizeGeneral）...')
    
    for i, job in enumerate(sample_jobs):
        jid = job['job_id'][:12]
        path = job['image_path']
        print(f'  [{i+1}/{len(sample_jobs)}] {jid} ...', end=' ', flush=True)
        
        try:
            blocks, elapsed = call_ocr(path)
            job['ocr_blocks'] = blocks
            job['ocr_elapsed_ms'] = elapsed
            print(f'{len(blocks)} blocks ({elapsed}ms)')
        except Exception as e:
            print(f'OCR FAIL: {e}')
            job['ocr_blocks'] = []
            job['ocr_error'] = str(e)
            continue
        
        # Fusion
        result = fuse_questions_with_ocr(job['questions'], blocks)
        job['result'] = result
        print(f'       matched={result["matched_count"]}/{result["total_questions"]} '
              f'answers={result["answer_candidate_count"]} skip={result["low_confidence_skip"]}')
    
    # ── Report ──
    print(f'\n📝 生成报告...')
    report = generate_report(
        sample_jobs,
        os.path.join(OUT_DIR, 'math_ocr_fusion_probe_report.md'),
        os.path.join(OUT_DIR, 'math_ocr_fusion_probe_result.json'),
    )
    
    # Summary
    total_q = sum(j.get('result', {}).get('total_questions', 0) for j in sample_jobs)
    total_m = sum(j.get('result', {}).get('matched_count', 0) for j in sample_jobs)
    total_a = sum(j.get('result', {}).get('answer_candidate_count', 0) for j in sample_jobs)
    
    print(f'\n{"="*60}')
    print(f'  jobs={len(sample_jobs)}  questions={total_q}  ocr_blocks={sum(len(j.get("ocr_blocks",[])) for j in sample_jobs)}')
    print(f'  match_rate={round(total_m/total_q*100,1) if total_q else 0}%  '
          f'answer_rate={round(total_a/total_q*100,1) if total_q else 0}%')
    print(f'{"="*60}')
    print(f'\n✅ 报告已生成 → math_ocr_fusion_probe_report.md')


if __name__ == '__main__':
    main()
