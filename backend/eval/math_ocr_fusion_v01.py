#!/usr/bin/env python3
"""
悠米伴学 — 数学口算 OCR + Qwen Fusion 探针 v01
v01 改进：
  - 分层置信度 (strong/medium/weak)
  - 多策略 answer_bbox 候选 + 安全门
  - 三阈值测试 (0.35/0.5/0.65)
  - 图片去重 (ahash)
  - 按图片类型分组输出
  - 7 项 pipeline 灰度门槛判断

只读 DB/图片，调通用 OCR，不改 pipeline。
"""
import sqlite3, json, os, re, time, hashlib
from datetime import datetime
from collections import defaultdict
from PIL import Image

DB_PATH = '/srv/yomi/yomi.db'
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# ═══════════════════════════════════════════════════════════════
#  0. IMAGE FINGERPRINT
# ═══════════════════════════════════════════════════════════════
def compute_ahash(image_path: str) -> str:
    """8×8 灰度均值 hash"""
    img = Image.open(image_path).convert('L').resize((8, 8), Image.LANCZOS)
    pixels = list(img.getdata())
    avg = sum(pixels) / len(pixels)
    bits = ''.join('1' if p >= avg else '0' for p in pixels)
    return hex(int(bits, 2))[2:].zfill(16)


def hamming(a: str, b: str) -> int:
    return sum(c1 != c2 for c1, c2 in zip(a, b))


# ═══════════════════════════════════════════════════════════════
#  1. OCR CLIENT
# ═══════════════════════════════════════════════════════════════
_ocr_client = None

def _get_ocr_client():
    global _ocr_client
    if _ocr_client is None:
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
        _ocr_client = Client(config)
    return _ocr_client


def call_ocr(image_path: str) -> tuple[list[dict], int]:
    """调用阿里云通用 OCR (RecognizeGeneral)，返回 blocks + ms"""
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
        for w in data.get('prism_wordsInfo', []):
            blocks.append({
                'text': w.get('word', ''),
                'x': w.get('x', 0),
                'y': w.get('y', 0),
                'w': w.get('width', 0),
                'h': w.get('height', 0),
                'confidence': w.get('prob', None),
            })
    return blocks, elapsed

# ═══════════════════════════════════════════════════════════════
#  2. TEXT PROCESSING
# ═══════════════════════════════════════════════════════════════
def normalize_text(t: str) -> str:
    t = t.strip().replace(' ', '').replace('\n', '').replace('\r', '')
    t = t.replace('（', '(').replace('）', ')')
    t = t.replace('×', 'x').replace('✕', 'x').replace('*', 'x')
    t = t.replace('÷', '/').replace('➗', '/')
    t = t.replace('＝', '=').replace('﹦', '=')
    t = t.replace('＋', '+').replace('－', '-')
    return t.lower()


def text_similarity_jaccard(a: str, b: str) -> float:
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb: return 0.0
    sa, sb = set(na), set(nb)
    inter = sa & sb
    union = sa | sb
    return len(inter) / len(union) if union else 0.0


def longest_common_substring_ratio(a: str, b: str) -> float:
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb: return 0.0
    max_len = 0
    for i in range(len(na)):
        for j in range(len(nb)):
            k = 0
            while i + k < len(na) and j + k < len(nb) and na[i + k] == nb[j + k]:
                k += 1
            if k > max_len:
                max_len = k
    return max_len / max(len(na), len(nb))


def digit_overlap_ratio(a: str, b: str) -> float:
    da = set(re.findall(r'\d+', a))
    db = set(re.findall(r'\d+', b))
    if not da or not db: return 0.0
    inter = da & db
    union = da | db
    return len(inter) / len(union)


def char_overlap_count(a: str, b: str) -> int:
    na, nb = normalize_text(a), normalize_text(b)
    return len(set(na) & set(nb))


# ═══════════════════════════════════════════════════════════════
#  3. MATH / META DETECTION
# ═══════════════════════════════════════════════════════════════
MATH_KEYWORDS = re.compile(
    r'[\+\-\×\÷\=]|口算|计算|填一填|竖式|直接写出|得数|答案|比大小|在.*里填|看谁.*对又快',
    re.IGNORECASE
)

QUESTION_NO_PATTERNS = [
    re.compile(r'^(\d+)[\.\、\）\)]\s*'),
    re.compile(r'^（(\d+)）\s*'),
    re.compile(r'^\[(\d+)\]\s*'),
    re.compile(r'^第(\d+)题'),
]


def is_math_question(text: str) -> bool:
    return bool(MATH_KEYWORDS.search(text)) if text else False


def extract_question_number(text: str) -> int | None:
    for pat in QUESTION_NO_PATTERNS:
        m = pat.search(text.strip())
        if m:
            return int(m.group(1))
    return None


def is_pure_number(text: str) -> bool:
    t = text.strip()
    return bool(re.match(r'^[\d\s\+\-\×\÷\.\,\(\)\[\]]+$', t) and len(t) <= 20)


def is_meta_noise(text: str) -> bool:
    t = text.strip()
    if not t: return True
    noise = [
        r'^[一二三四五六七八九十]$', r'^[A-Za-z]{1,2}$',
        r'^[。，、；：！？]$', r'^第\d+页', r'^班级', r'^姓名', r'^日期',
        r'^打卡日期', r'^建议用时', r'^实际用时', r'^老师改正', r'^错题改正',
        r'^练口算', r'^课时', r'^课间活动', r'^任务一',
        r'^\d{1,2}$', r'^星期', r'^学号', r'^册别',
    ]
    for p in noise:
        if re.match(p, t):
            return True
    return False


def is_red_pen_mark(text: str) -> bool:
    """检测可能的红笔/批改标记"""
    marks = ['✓', '√', '✗', '×', '○', '?', '老师', '批注', '改正', '注意', '认真']
    t = text.strip()
    return any(m in t for m in marks) and len(t) <= 3


# ═══════════════════════════════════════════════════════════════
#  4. ENHANCED ANSWER_BBOX STRATEGIES
# ═══════════════════════════════════════════════════════════════
def find_answer_candidates(
    q_bbox: dict,      # {'x','y','w','h'} — question bbox
    qtext: str,
    ocr_blocks: list[dict],
    image_w: int,
    image_h: int,
) -> tuple[list[dict], list[dict]]:
    """
    多策略找 answer_bbox 候选。
    返回 (accepted_candidates, rejected_candidates)
    """
    qx, qy, qw, qh = q_bbox['x'], q_bbox['y'], q_bbox['w'], q_bbox['h']
    q_area = qw * qh
    qx2, qy2 = qx + qw, qy + qh

    def reject(reason: str, blk: dict) -> dict:
        return {**blk, 'reject_reason': reason}

    candidates = []
    rejected = []

    for blk in ocr_blocks:
        bx, by, bw, bh = blk['x'], blk['y'], blk['w'], blk['h']
        bx2, by2 = bx + bw, by + bh
        btext = blk['text'].strip()
        if not btext:
            continue

        # ── Safety gates ──
        # Gate 1: 越界
        if bx < 0 or by < 0 or bx2 > image_w + 100 or by2 > image_h + 100:
            rejected.append(reject('out_of_bounds', blk))
            continue

        # Gate 2: 面积过大 (超过整题 3x)
        if bw * bh > q_area * 3:
            rejected.append(reject('too_large', blk))
            continue

        # Gate 3: 面积过小 (< 0.1% of question area)
        if bw * bh < q_area * 0.001:
            continue  # too small to be meaningful answer, silently skip

        # Gate 4: 与题目 bbox 重合 > 70% (可能是题目本体)
        overlap_x = max(0, min(qx2, bx2) - max(qx, bx))
        overlap_y = max(0, min(qy2, by2) - max(qy, by))
        overlap_area = overlap_x * overlap_y
        if overlap_area > q_area * 0.7:
            rejected.append(reject('too_much_overlap_with_question', blk))
            continue

        # Gate 5: 距离题目主体过远 (> 3x question height)
        dist_y = max(0, by - qy2) if by > qy2 else max(0, qy - by2)
        if dist_y > qh * 3:
            rejected.append(reject('too_far_vertically', blk))
            continue

        # Gate 6: meta 区域
        if is_meta_noise(btext):
            rejected.append(reject('meta_noise', blk))
            continue

        # Gate 7: 红笔/批改
        if is_red_pen_mark(btext):
            rejected.append(reject('red_pen_mark', blk))
            continue

        # ════ Candidate strategies ════
        is_candidate = False
        strategy = ''

        # S1: 等号右侧区域
        eq_pos = qtext.find('=')
        if eq_pos >= 0 and bx > qx + qw * 0.4 and abs(by - qy) < qh:
            is_candidate = True
            strategy = 'equals_right'

        # S2: 横线/括号/空格附近
        if not is_candidate and re.search(r'[\(（]\\s*[\)）]', qtext):
            if abs(by - qy) < qh * 1.5 and bx > qx:
                is_candidate = True
                strategy = 'bracket_area'

        # S3: 纯数字答案
        if not is_candidate and is_pure_number(btext):
            if by > qy - qh * 0.5 and abs(bx - qx) < qw * 1.5:
                is_candidate = True
                strategy = 'pure_number'

        # S4: 题目右半区小面积数字 block
        if not is_candidate and is_pure_number(btext) and bw * bh < q_area * 0.5:
            if bx > qx + qw * 0.3 and abs(by - qy) < qh * 1.5:
                is_candidate = True
                strategy = 'right_half_small'

        # S5: 竖式题结果行（最下行）
        if not is_candidate and is_pure_number(btext):
            if by > qy + qh * 0.7 and abs(bx - qx) < qw * 1.2:
                is_candidate = True
                strategy = 'vertical_result_row'

        # S6: 比大小题中间符号附近
        cmp_keywords = ['比大小', '>', '<', '=', '大', '小']
        if not is_candidate and any(k in qtext for k in cmp_keywords):
            if is_pure_number(btext) and abs(by - qy) < qh and bx > qx:
                is_candidate = True
                strategy = 'comparison_area'

        if is_candidate:
            candidates.append({**blk, 'candidate_strategy': strategy})
        # else: block is in valid area but not answer candidate → silently skip

    return candidates, rejected


# ═══════════════════════════════════════════════════════════════
#  5. LAYERED CONFIDENCE FUSION
# ═══════════════════════════════════════════════════════════════
THRESHOLD_CONFIGS = {
    'strict':   {'strong_min': 0.65, 'medium_min': 0.50},
    'balanced': {'strong_min': 0.50, 'medium_min': 0.35},
    'loose':    {'strong_min': 0.35, 'medium_min': 0.20},
}


def fuse_question_with_layers(
    q: dict,
    ocr_blocks: list[dict],
    valid_blocks: list[dict],
    image_w: int,
    image_h: int,
    threshold_name: str = 'balanced',
) -> dict:
    """
    分层匹配：strong / medium / weak。
    strong → 确定 question_bbox
    medium → question_bbox + confidence=medium
    weak → 不生成 bbox，计入 skipped
    """
    tcfg = THRESHOLD_CONFIGS.get(threshold_name, THRESHOLD_CONFIGS['balanced'])
    strong_min = tcfg['strong_min']
    medium_min = tcfg['medium_min']

    qtext = q.get('question_text', '')
    qno = q.get('question_no', 0)
    if not qtext:
        return {'match_tier': 'no_text', 'score': 0.0, 'question_bbox': None,
                'answer_candidates': [], 'answer_rejected': [], 'reason': 'no question_text'}

    best_block = None
    best_score = 0.0
    best_qno_match = 0.0
    best_sub_overlap = 0.0
    best_jaccard = 0.0
    best_digit_overlap = 0.0

    for blk in valid_blocks:
        btext = blk['text']

        # 1. 题号锚定
        qno_blk = extract_question_number(btext)
        qno_match = 0.5 if (qno_blk is not None and qno_blk == qno) else 0.0

        # 2. 文本相似度
        sub_overlap = longest_common_substring_ratio(qtext, btext)
        jaccard = text_similarity_jaccard(qtext, btext)
        digit_overlap = digit_overlap_ratio(qtext, btext)
        char_overlap = char_overlap_count(qtext, btext)

        # 3. 空间顺序合理性（同列优先）
        spatial_bonus = 0.0
        if q.get('prev_block_x') is not None:
            prev_x = q['prev_block_x']
            if abs(blk['x'] - prev_x) < 100:  # 同一列
                spatial_bonus = 0.1

        # 综合得分
        score = (qno_match +
                 0.25 * sub_overlap +
                 0.15 * jaccard +
                 0.10 * digit_overlap +
                 spatial_bonus)
        if char_overlap >= 5:
            score += 0.05

        if score > best_score:
            best_score = score
            best_block = blk
            best_qno_match = qno_match
            best_sub_overlap = sub_overlap
            best_jaccard = jaccard
            best_digit_overlap = digit_overlap

    # ── 分层判定 ──
    reason_parts = (f'qno={best_qno_match:.1f} sub={best_sub_overlap:.2f} '
                    f'jac={best_jaccard:.2f} dig={best_digit_overlap:.2f}')

    if best_score >= strong_min:
        tier = 'strong'
    elif best_score >= medium_min:
        tier = 'medium'
    else:
        tier = 'weak'

    q_bbox = None
    answer_candidates = []
    answer_rejected = []

    if tier in ('strong', 'medium') and best_block:
        q_bbox = {
            'x': best_block['x'], 'y': best_block['y'],
            'w': best_block['w'], 'h': best_block['h'],
        }
        answer_candidates, answer_rejected = find_answer_candidates(
            q_bbox, qtext, ocr_blocks, image_w, image_h,
        )

    return {
        'match_tier': tier,
        'score': round(best_score, 4),
        'reason': reason_parts,
        'question_bbox': q_bbox,
        'answer_candidates': answer_candidates,
        'answer_rejected': answer_rejected,
        'best_block_text': best_block['text'][:60] if best_block else '',
    }


# ═══════════════════════════════════════════════════════════════
#  6. SAMPLE SELECTION
# ═══════════════════════════════════════════════════════════════
def select_unique_math_jobs(max_jobs: int = 8) -> list[dict]:
    """选不同图像的数学 job，去重（ahash hamming ≤ 2 视为同一张）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT pj.job_id, pj.data, pj.file_name, pj.questions_count, ir.file_path
        FROM parse_jobs pj JOIN image_registry ir ON pj.job_id = ir.jid
        WHERE pj.status='completed' AND pj.questions_count > 0
        ORDER BY pj.created_at DESC
    """)

    seen_hashes = {}  # ahash → job info
    selected = []

    for r in cur.fetchall():
        path = r['file_path']
        if not os.path.exists(path):
            alt = f'/tmp/yomi/{r["job_id"]}.jpg'
            if os.path.exists(alt):
                path = alt
            else:
                continue

        # Extract math questions
        try:
            d = json.loads(r['data'])
            qs = d.get('questions', [])
            math_qs = []
            for qi, q in enumerate(qs):
                qt = q.get('question_text', '') or q.get('text', '')
                if is_math_question(qt):
                    math_qs.append({
                        'id': f'{r["job_id"]}-{qi}',
                        'question_no': qi + 1,
                        'question_text': qt,
                        'bbox': q.get('bbox', []),
                    })
        except:
            continue

        if len(math_qs) < 3:
            continue

        # Compute ahash for dedup
        try:
            ah = compute_ahash(path)
        except:
            continue

        # Check if new image
        is_new = True
        for seen_ah, _ in seen_hashes.items():
            if hamming(ah, seen_ah) <= 2:
                is_new = False
                break

        if is_new:
            seen_hashes[ah] = path
            selected.append({
                'job_id': r['job_id'],
                'file_name': r['file_name'],
                'image_path': path,
                'ahash': ah,
                'math_questions': math_qs,
                'total_questions': len(qs),
            })

        if len(selected) >= max_jobs:
            break

    conn.close()

    # Get image dimensions
    for s in selected:
        try:
            img = Image.open(s['image_path'])
            s['image_w'], s['image_h'] = img.size
        except:
            s['image_w'], s['image_h'] = 0, 0

    return selected


# ═══════════════════════════════════════════════════════════════
#  7. REPORTING
# ═══════════════════════════════════════════════════════════════
def generate_report(all_results: list[dict], output_md: str, output_json: str):
    lines = []
    lines.append('# 数学 OCR Fusion 探针 v01 报告')
    lines.append(f'\n> 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(f'> 探针版本：v01（分层置信度 + 多策略 answer_bbox + 安全门）')

    # ── Aggregate metrics ──
    total_images = len(all_results)
    total_jobs = sum(len(r.get('selected_jobs', [])) for r in all_results)
    total_q = 0
    metrics = defaultdict(int)
    total_qwa = 0  # questions with answer

    for r in all_results:
        for j in r.get('selected_jobs', []):
            resultado = j.get('result', {})
            total_q += resultado.get('total_questions', 0)
            for k in ['strong', 'medium', 'weak', 'answer_accepted', 'answer_rejected',
                       'meta_filtered', 'suspected_false']:
                metrics[k] += resultado.get(k, 0)
            total_qwa += resultado.get('questions_with_answer', 0)

    lines.append(f'\n## 一、样本概况\n')
    lines.append('| 指标 | 数值 |')
    lines.append('|------|------|')
    lines.append(f'| unique_image_count | {total_images} |')
    lines.append(f'| sample_job_count | {total_jobs} |')
    lines.append(f'| math_question_count | {total_q} |')

    # Per-image details
    lines.append(f'\n### 图像清单\n')
    lines.append('| # | ahash | file_name | math_q | total_q | image_size |')
    lines.append('|---|-------|-----------|--------|---------|------------|')
    for i, r in enumerate(all_results):
        for j in r.get('selected_jobs', []):
            lines.append(f'| {i+1} | {j.get("ahash","?")[:12]} | {j["file_name"][:30]} | '
                         f'{len(j["math_questions"])} | {j["total_questions"]} | '
                         f'{j.get("image_w",0)}×{j.get("image_h",0)} |')

    # ── Core metrics (balanced threshold) ──
    lines.append(f'\n## 二、核心指标（balanced 阈值: strong≥0.50, medium≥0.35）\n')
    lines.append('| 指标 | 数值 |')
    lines.append('|------|------|')
    lines.append(f'| ocr_block_count | {sum(len(j.get("ocr_blocks",[])) for r in all_results for j in r.get("selected_jobs",[]))} |')
    lines.append(f'| strong_match_count | {metrics["strong"]} |')
    lines.append(f'| medium_match_count | {metrics["medium"]} |')
    lines.append(f'| weak_skipped_count | {metrics["weak"]} |')
    strong_rate = round(metrics['strong'] / total_q * 100, 1) if total_q else 0
    spm_rate = round((metrics['strong'] + metrics['medium']) / total_q * 100, 1) if total_q else 0
    lines.append(f'| question_bbox_match_rate_strong_only | {strong_rate}% |')
    lines.append(f'| question_bbox_match_rate_strong_plus_medium | {spm_rate}% |')
    lines.append(f'| answer_bbox_candidate_count | {metrics["answer_accepted"]} |')
    ans_rate_q = round(total_qwa / (metrics['strong'] + metrics['medium']) * 100, 1) if (metrics['strong'] + metrics['medium']) else 0
    lines.append(f'| answer_bbox_candidate_rate | {ans_rate_q}% |')
    lines.append(f'| answer_bbox_rejected_count | {metrics["answer_rejected"]} |')
    lines.append(f'| suspected_false_match_count | {metrics["suspected_false"]} |')
    lines.append(f'| meta_filtered_count | {metrics["meta_filtered"]} |')

    # ── Three-threshold comparison ──
    lines.append(f'\n## 三、三阈值对比\n')
    lines.append('| 阈值档 | strong | medium | weak | S+M 率 |')
    lines.append('|--------|--------|--------|------|--------|')
    for tname in ['strict', 'balanced', 'loose']:
        s = m = w = 0
        for r in all_results:
            for j in r.get('selected_jobs', []):
                tres = j.get('threshold_results', {}).get(tname, {})
                s += tres.get('strong', 0)
                m += tres.get('medium', 0)
                w += tres.get('weak', 0)
        spm = round((s + m) / total_q * 100, 1) if total_q else 0
        lines.append(f'| {tname} | {s} | {m} | {w} | {spm}% |')

    # ── Per-image-type results ──
    lines.append(f'\n## 四、按图像类型结果\n')
    for i, r in enumerate(all_results):
        for j in r.get('selected_jobs', []):
            jid = j['job_id'][:12]
            res = j.get('result', {})
            lines.append(f'### Image {i+1}: {j["file_name"][:40]} ({jid})\n')
            lines.append(f'- questions={res.get("total_questions",0)}, OCR blocks={len(j.get("ocr_blocks",[]))}')
            lines.append(f'- strong={res.get("strong",0)} medium={res.get("medium",0)} weak={res.get("weak",0)}')
            lines.append(f'- answer_accepted={res.get("answer_accepted",0)} rejected={res.get("answer_rejected",0)}')
            lines.append(f'- meta_filtered={res.get("meta_filtered",0)} suspected_false={res.get("suspected_false",0)}')
            # Show first 5 strong matches
            strong_matches = [(qid, qr) for qid, qr in res.get('per_question', {}).items()
                              if qr.get('match_tier') == 'strong']
            if strong_matches:
                lines.append(f'\n| # | tier | score | question_text | bbox | answers |')
                lines.append(f'|---|------|-------|---------------|------|---------|')
                for qid, qr in strong_matches[:5]:
                    bb = qr.get('question_bbox', {}) or {}
                    bbox_str = f'({bb.get("x",0)},{bb.get("y",0)})' if bb else 'N/A'
                    ans_list = [a['text'][:15] for a in qr.get('answer_candidates', [])]
                    ans_str = ', '.join(ans_list[:3])
                    lines.append(f'| {qid[-6:]} | {qr["match_tier"]} | {qr["score"]} | '
                                 f'{qr.get("best_block_text","")[:25]} | {bbox_str} | {ans_str} |')
            lines.append('')

    # ── Pipeline readiness ──
    lines.append(f'\n## 五、Pipeline 灰度门槛判断\n')
    lines.append('| 条件 | 要求 | 实际 | 达标 |')
    lines.append('|------|------|------|------|')

    conditions = [
        ('unique_image_count ≥ 5', total_images, total_images >= 5),
        ('S+M 匹配率 ≥ 70%', f'{spm_rate}%', spm_rate >= 70),
        ('strong 占比不过低', f'{metrics["strong"]}/{total_q}', metrics['strong'] >= total_q * 0.3),
        ('answer_bbox_candidate_rate ≥ 60%', f'{ans_rate_q}%', ans_rate_q >= 60),
        ('suspected_false 可控', str(metrics['suspected_false']), metrics['suspected_false'] <= total_q * 0.1),
        ('weak 被正确跳过', f'{metrics["weak"]} weak', metrics['weak'] > 0 or total_q == 0),
        ('meta 误匹配接近 0', str(metrics['meta_filtered']), metrics['meta_filtered'] <= 5),
    ]
    all_pass = True
    for cond, actual, passed in conditions:
        mark = '✅' if passed else '❌'
        lines.append(f'| {cond} | {actual} | {mark} |')
        if not passed:
            all_pass = False

    lines.append(f'\n### 结论：{"✅ 达到 pipeline 灰度门槛" if all_pass else "❌ 未达到 pipeline 灰度门槛"}\n')

    if not all_pass:
        lines.append('### 下一步优先修复项：\n')
        if total_images < 5:
            lines.append(f'1. **样本不足**：当前仅有 {total_images} 种不同图像，需补充竖式/填空/比大小/手写/涂改等题型样本。')
        if spm_rate < 70:
            lines.append(f'2. **匹配率不足**：S+M 匹配率 {spm_rate}%，需增强 OCR block 文本与 Qwen 题目文本的对齐策略。')
        if ans_rate_q < 60:
            lines.append(f'3. **answer_bbox 候选率不足**：当前 {ans_rate_q}%，需增强空间推理（等号右侧/填空区/竖式结果行）。')
        if metrics['suspected_false'] > total_q * 0.1:
            lines.append(f'4. **疑似误匹配过多**：{metrics["suspected_false"]} 个，需收紧匹配阈值或增加人工校验。')

    lines.append('\n---\n*探针 v01 自动生成*')

    report = '\n'.join(lines)
    with open(output_md, 'w') as f:
        f.write(report)

    with open(output_json, 'w') as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)

    return report


# ═══════════════════════════════════════════════════════════════
#  8. MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    print('=' * 65)
    print('  数学 OCR Fusion 探针 v01 — 分层置信度 + 安全门')
    print('=' * 65)

    # ── Select samples ──
    print('\n📊 选取样本（按 ahash 去重）...')
    selected = select_unique_math_jobs(max_jobs=8)

    if not selected:
        print('❌ 无可用数学作业样本')
        return

    print(f'   选中 {len(selected)} 张不同图像:')
    for s in selected:
        print(f'     {s["ahash"][:14]} | {s["file_name"][:40]} | '
              f'{len(s["math_questions"])}/{s["total_questions"]} math | '
              f'{s.get("image_w",0)}×{s.get("image_h",0)}')

    # ── OCR each unique image ──
    print(f'\n🔍 调用通用 OCR（RecognizeGeneral, ¥0.003/次）...')
    for i, img_data in enumerate(selected):
        path = img_data['image_path']
        print(f'  [{i+1}/{len(selected)}] {img_data["ahash"][:14]} ...', end=' ', flush=True)
        try:
            blocks, elapsed = call_ocr(path)
            img_data['ocr_blocks'] = blocks
            img_data['ocr_elapsed_ms'] = elapsed
            print(f'{len(blocks)} blocks ({elapsed}ms)')
        except Exception as e:
            print(f'OCR FAIL: {e}')
            img_data['ocr_blocks'] = []
            img_data['ocr_error'] = str(e)

    # ── Fusion for all 3 thresholds ──
    all_results = []
    for img_data in selected:
        questions = img_data['math_questions']
        blocks = img_data['ocr_blocks']
        image_w = img_data.get('image_w', 0)
        image_h = img_data.get('image_h', 0)

        if not blocks:
            img_data['result'] = {'total_questions': len(questions),
                                  'strong': 0, 'medium': 0, 'weak': 0,
                                  'answer_accepted': 0, 'answer_rejected': 0,
                                  'meta_filtered': 0, 'suspected_false': 0,
                                  'per_question': {}}
            img_data['threshold_results'] = {}
            all_results.append({'selected_jobs': [img_data]})
            continue

        # Filter meta noise
        valid_blocks = [b for b in blocks if not is_meta_noise(b['text'])]
        meta_filtered = len(blocks) - len(valid_blocks)

        # Add spatial context: prev_block_x for column detection
        for qi, q in enumerate(questions):
            q['prev_block_x'] = questions[qi-1].get('_matched_x') if qi > 0 and questions[qi-1].get('_matched_x') else None

        # Run fusion for each threshold
        threshold_results = {}
        for tname in ['strict', 'balanced', 'loose']:
            tmetrics = defaultdict(int)
            per_q = {}
            for q in questions:
                result = fuse_question_with_layers(
                    q, blocks, valid_blocks, image_w, image_h, threshold_name=tname,
                )
                per_q[q['id']] = result
                tmetrics[result['match_tier']] += 1
                # Track matched x for spatial context
                if result.get('question_bbox'):
                    q['_matched_x'] = result['question_bbox']['x']
                # suspected false: weak match that looks like it could be wrong
                if result['match_tier'] == 'weak' and result['score'] < 0.1:
                    tmetrics['suspected_false'] += 1
            threshold_results[tname] = dict(tmetrics)
            threshold_results[tname]['per_question'] = per_q

        # Use 'balanced' as default result
        balanced = threshold_results.get('balanced', {})
        answer_accepted = 0
        answer_rejected = 0
        questions_with_answer = 0
        for qid, qr in balanced.get('per_question', {}).items():
            if qr.get('answer_candidates'):
                answer_accepted += len(qr['answer_candidates'])
                questions_with_answer += 1
            if qr.get('answer_rejected'):
                answer_rejected += len(qr['answer_rejected'])

        img_data['result'] = {
            'total_questions': len(questions),
            'strong': balanced.get('strong', 0),
            'medium': balanced.get('medium', 0),
            'weak': balanced.get('weak', 0),
            'answer_accepted': answer_accepted,
            'answer_rejected': answer_rejected,
            'questions_with_answer': questions_with_answer,
            'meta_filtered': meta_filtered,
            'suspected_false': balanced.get('suspected_false', 0),
            'per_question': balanced.get('per_question', {}),
        }
        img_data['threshold_results'] = threshold_results

        print(f'       S={img_data["result"]["strong"]} M={img_data["result"]["medium"]} '
              f'W={img_data["result"]["weak"]} ans={answer_accepted} rej={answer_rejected}')

        all_results.append({'selected_jobs': [img_data]})

    # ── Generate report ──
    print(f'\n📝 生成报告...')
    report_md = os.path.join(OUT_DIR, 'math_ocr_fusion_v01_report.md')
    report_json = os.path.join(OUT_DIR, 'math_ocr_fusion_v01_result.json')
    generate_report(all_results, report_md, report_json)

    # Summary
    total_q = sum(j.get('result', {}).get('total_questions', 0)
                  for r in all_results for j in r.get('selected_jobs', []))
    strong = sum(j.get('result', {}).get('strong', 0)
                 for r in all_results for j in r.get('selected_jobs', []))
    medium = sum(j.get('result', {}).get('medium', 0)
                 for r in all_results for j in r.get('selected_jobs', []))
    weak = sum(j.get('result', {}).get('weak', 0)
               for r in all_results for j in r.get('selected_jobs', []))
    ans_acc = sum(j.get('result', {}).get('answer_accepted', 0)
                  for r in all_results for j in r.get('selected_jobs', []))
    ans_rej = sum(j.get('result', {}).get('answer_rejected', 0)
                  for r in all_results for j in r.get('selected_jobs', []))

    print(f'\n{"="*65}')
    print(f'  images={len(selected)}  questions={total_q}  '
          f'S={strong} M={medium} W={weak}')
    print(f'  S+M_rate={round((strong+medium)/total_q*100,1) if total_q else 0}%  '
          f'ans_acc={ans_acc}  ans_rej={ans_rej}')
    print(f'{"="*65}')
    print(f'\n✅ 报告已生成:')
    print(f'   {report_md}')
    print(f'   {report_json}')


if __name__ == '__main__':
    main()
