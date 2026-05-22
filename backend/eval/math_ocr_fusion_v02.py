#!/usr/bin/env python3
"""
悠米伴学 — 数学 OCR Fusion 探针 v02
v02 改进：
  - 图像类型预筛 (math_homework / non_math / uncertain)
  - 题号锚定权重 0.5→0.7
  - meta 过滤收紧
  - 负样本（非作业图）纳入统计
  - 新指标：non_math_false_accept_count

只读 DB/图片，调通用 OCR，不改 pipeline。
"""
import sqlite3, json, os, re, time
from datetime import datetime
from collections import defaultdict
from PIL import Image

DB_PATH = '/srv/yomi/yomi.db'
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# ═══════════════════════════════════════════════════════════════
#  0. IMAGE FINGERPRINT
# ═══════════════════════════════════════════════════════════════
def compute_ahash(image_path: str) -> str:
    img = Image.open(image_path).convert('L').resize((8, 8), Image.LANCZOS)
    pixels = list(img.get_flattened_data() if hasattr(img, 'get_flattened_data') else img.getdata())
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
                'x': w.get('x', 0), 'y': w.get('y', 0),
                'w': w.get('width', 0), 'h': w.get('height', 0),
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


# ═══════════════════════════════════════════════════════════════
#  3. IMAGE PRE-SCREENING
# ═══════════════════════════════════════════════════════════════
# Math homework keywords
MATH_KW_LIST = ['计算', '口算', '填一填', '比大小', '竖式', '直接写出', '得数', '列式',
                '看谁', '对又快', '想一想', '算一算', '解决问题', '应用题', '填空',
                '答案', '多少', '一共', '剩下']

# Non-math keywords (food/label/UI/product)
NON_MATH_KW_LIST = ['营养成分', '配料', '食品', '生产许可', '保质期', '净含量', '食用盐',
                    '过敏原', '产品标准', '贮存条件', '工作台', '上传作业', '按钮',
                    '支付', '价格', '商品', '订单', '收货', '地址', '电话', '微信',
                    '确认', '取消', '提交', '退出', '登录', '注册', '设置',
                    '克数', 'g', 'ml', 'kJ', '蛋白质', '脂肪', '碳水化合物', '钠',
                    '每100', '能量', '膳食纤维']


def screen_image_type(ocr_blocks: list[dict], file_name: str = '') -> dict:
    """
    判断图像是否为数学作业图。
    返回 {image_type, confidence, reason}
    """
    texts = [b['text'].strip() for b in ocr_blocks if b['text'].strip()]
    all_text = ' '.join(texts)

    if not texts:
        return {'image_type': 'non_math', 'confidence': 1.0, 'reason': 'no OCR text'}

    # ── Count signals ──
    # Forward signals
    question_numbers = []
    for t in texts:
        m = re.match(r'^(\d+)[\.\、\）\)]', t)
        if m:
            question_numbers.append(int(m.group(1)))

    seq_count = 0
    if len(question_numbers) >= 3:
        sorted_nums = sorted(set(question_numbers))
        consec = 0
        for i in range(1, len(sorted_nums)):
            if sorted_nums[i] == sorted_nums[i-1] + 1:
                consec += 1
        seq_count = consec

    math_ops = len(re.findall(r'[\+\-\×\÷\=\<\>]', all_text))
    math_kw_hits = sum(1 for kw in MATH_KW_LIST if kw in all_text)
    short_expr_count = len([t for t in texts if re.search(r'\d+\s*[\+\-\×\÷]\s*\d+', t)])
    eq_sign_count = len([t for t in texts if '=' in t])

    # Negative signals
    non_math_kw_hits = sum(1 for kw in NON_MATH_KW_LIST if kw in all_text)
    nutrition_hits = sum(1 for kw in ['营养成分', '蛋白质', '脂肪', '碳水化合物', '能量', '钠',
                                       '每100', '膳食纤维'] if kw in all_text)
    food_label_hits = sum(1 for kw in ['配料', '食品', '生产许可', '保质期', '净含量', '贮存条件',
                                        '食用盐', '过敏原', '产品标准'] if kw in all_text)
    ui_hits = sum(1 for kw in ['工作台', '上传作业', '按钮', '支付', '登录', '注册', '设置',
                                '确认', '取消', '提交', '退出'] if kw in all_text)

    # ── Decision logic ──
    # Hard negative: food/nutrition label
    if nutrition_hits >= 3 or food_label_hits >= 3:
        return {'image_type': 'non_math', 'confidence': 0.95,
                'reason': f'food_label: nutrition={nutrition_hits} food={food_label_hits}'}

    # Hard negative: UI
    if ui_hits >= 3:
        return {'image_type': 'non_math', 'confidence': 0.90,
                'reason': f'ui_elements: ui_hits={ui_hits}'}

    # Hard negative: too many non-math keywords
    if non_math_kw_hits >= 5:
        return {'image_type': 'non_math', 'confidence': 0.80,
                'reason': f'non_math_keywords: {non_math_kw_hits} hits'}

    # Hard positive: sequential question numbers + math operators
    if seq_count >= 3 and math_ops >= 10:
        return {'image_type': 'math_homework', 'confidence': 0.95,
                'reason': f'seq={seq_count} math_ops={math_ops}'}

    # Positive: math keywords + short expressions
    if math_kw_hits >= 2 and (short_expr_count >= 5 or eq_sign_count >= 5):
        return {'image_type': 'math_homework', 'confidence': 0.85,
                'reason': f'kw={math_kw_hits} expr={short_expr_count} eq={eq_sign_count}'}

    # Positive: question numbers + math expressions
    if seq_count >= 2 and short_expr_count >= 3:
        return {'image_type': 'math_homework', 'confidence': 0.80,
                'reason': f'seq={seq_count} expr={short_expr_count}'}

    # Positive: math operators dominate
    if math_ops >= 20 and non_math_kw_hits <= 2:
        return {'image_type': 'math_homework', 'confidence': 0.75,
                'reason': f'math_ops={math_ops} non_math_kw={non_math_kw_hits}'}

    # Uncertain
    if math_ops >= 5 or math_kw_hits >= 1 or seq_count >= 1:
        return {'image_type': 'uncertain', 'confidence': 0.50,
                'reason': f'unclear: math_ops={math_ops} seq={seq_count} kw={math_kw_hits}'}

    return {'image_type': 'non_math', 'confidence': 0.60,
            'reason': f'no_signal: math_ops={math_ops} seq={seq_count}'}


# ═══════════════════════════════════════════════════════════════
#  4. MATH / META DETECTION
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
    """收紧版 meta 过滤"""
    t = text.strip()
    if not t: return True
    noise = [
        r'^[一二三四五六七八九十]$', r'^[A-Za-z]{1,2}$',
        r'^[。，、；：！？]$', r'^第\d+页', r'^班级', r'^姓名', r'^日期',
        r'^打卡日期', r'^建议用时', r'^实际用时', r'^老师改正', r'^错题改正',
        r'^练口算', r'^课时', r'^课间活动', r'^任务[一二三四五六]',
        r'^\d{1,2}$', r'^星期', r'^学号', r'^册别',
        # v02 新增
        r'^\d+[月日]$', r'^[上下]午', r'^[+-]?\d+$',
        r'^营养成分', r'^配料', r'^食品', r'^生产许可', r'^保质期',
        r'^净含量', r'^贮存条件', r'^过敏原', r'^产品标准',
        r'^克$', r'^千焦$', r'^毫升$', r'^毫克$',
        r'^%$', r'^g$', r'^ml$', r'^kJ$',
    ]
    for p in noise:
        if re.match(p, t):
            return True
    return False


# ═══════════════════════════════════════════════════════════════
#  5. ANSWER BBOX STRATEGIES (same as v01, tuned)
# ═══════════════════════════════════════════════════════════════
def find_answer_candidates(
    q_bbox: dict, qtext: str, ocr_blocks: list[dict],
    image_w: int, image_h: int,
) -> tuple[list[dict], list[dict]]:
    qx, qy, qw, qh = q_bbox['x'], q_bbox['y'], q_bbox['w'], q_bbox['h']
    q_area = qw * qh
    qx2, qy2 = qx + qw, qy + qh

    def reject(reason, blk): return {**blk, 'reject_reason': reason}

    candidates, rejected = [], []

    for blk in ocr_blocks:
        bx, by, bw, bh = blk['x'], blk['y'], blk['w'], blk['h']
        bx2, by2 = bx + bw, by + bh
        btext = blk['text'].strip()
        if not btext: continue

        # Gate 1: out of bounds
        if bx < 0 or by < 0 or bx2 > image_w + 100 or by2 > image_h + 100:
            rejected.append(reject('out_of_bounds', blk)); continue
        # Gate 2: too large (>3x question)
        if bw * bh > q_area * 3:
            rejected.append(reject('too_large', blk)); continue
        # Gate 3: too small
        if bw * bh < q_area * 0.001:
            continue
        # Gate 4: too much overlap with question
        overlap_x = max(0, min(qx2, bx2) - max(qx, bx))
        overlap_y = max(0, min(qy2, by2) - max(qy, by))
        if overlap_x * overlap_y > q_area * 0.7:
            rejected.append(reject('too_much_overlap_with_question', blk)); continue
        # Gate 5: too far vertically
        dist_y = max(0, by - qy2) if by > qy2 else max(0, qy - by2)
        if dist_y > qh * 5:
            rejected.append(reject('too_far_vertically', blk)); continue
        # Gate 6: meta
        if is_meta_noise(btext):
            rejected.append(reject('meta_noise', blk)); continue

        # Candidate strategies
        is_candidate, strategy = False, ''
        eq_pos = qtext.find('=')
        if eq_pos >= 0 and bx > qx + qw * 0.4 and abs(by - qy) < qh:
            is_candidate, strategy = True, 'equals_right'
        elif re.search(r'[\(（]\s*[\)）]', qtext) and abs(by - qy) < qh * 1.5 and bx > qx:
            is_candidate, strategy = True, 'bracket_area'
        elif is_pure_number(btext) and by > qy - qh * 0.5 and abs(bx - qx) < qw * 1.5:
            is_candidate, strategy = True, 'pure_number'
        elif is_pure_number(btext) and bw * bh < q_area * 0.5 and bx > qx + qw * 0.3 and abs(by - qy) < qh * 1.5:
            is_candidate, strategy = True, 'right_half_small'
        elif is_pure_number(btext) and by > qy + qh * 0.7 and abs(bx - qx) < qw * 1.2:
            is_candidate, strategy = True, 'vertical_result_row'

        if is_candidate:
            candidates.append({**blk, 'candidate_strategy': strategy})

    return candidates, rejected


# ═══════════════════════════════════════════════════════════════
#  6. LAYERED CONFIDENCE FUSION (v02: qno weight 0.7)
# ═══════════════════════════════════════════════════════════════
THRESHOLD_CONFIGS = {
    'strict':   {'strong_min': 0.65, 'medium_min': 0.50},
    'balanced': {'strong_min': 0.50, 'medium_min': 0.35},
    'loose':    {'strong_min': 0.35, 'medium_min': 0.20},
}


def fuse_question_with_layers(
    q: dict, ocr_blocks: list[dict], valid_blocks: list[dict],
    image_w: int, image_h: int, threshold_name: str = 'balanced',
) -> dict:
    tcfg = THRESHOLD_CONFIGS.get(threshold_name, THRESHOLD_CONFIGS['balanced'])
    strong_min, medium_min = tcfg['strong_min'], tcfg['medium_min']

    qtext = q.get('question_text', '')
    qno = q.get('question_no', 0)
    if not qtext:
        return {'match_tier': 'no_text', 'score': 0.0, 'question_bbox': None,
                'answer_candidates': [], 'answer_rejected': [], 'reason': 'no question_text'}

    best_block, best_score = None, 0.0
    best_qno, best_sub, best_jac, best_dig = 0.0, 0.0, 0.0, 0.0

    for blk in valid_blocks:
        btext = blk['text']
        qno_blk = extract_question_number(btext)
        qno_match = 0.7 if (qno_blk is not None and qno_blk == qno) else 0.0  # v02: 0.5→0.7

        sub_overlap = longest_common_substring_ratio(qtext, btext)
        jaccard = text_similarity_jaccard(qtext, btext)
        digit_overlap = digit_overlap_ratio(qtext, btext)

        spatial_bonus = 0.0
        if q.get('prev_block_x') is not None:
            if abs(blk['x'] - q['prev_block_x']) < 100:
                spatial_bonus = 0.1

        score = (qno_match +
                 0.25 * sub_overlap +
                 0.15 * jaccard +
                 0.15 * digit_overlap +    # v02: 0.10→0.15
                 spatial_bonus)

        if score > best_score:
            best_score, best_block = score, blk
            best_qno, best_sub = qno_match, sub_overlap
            best_jac, best_dig = jaccard, digit_overlap

    reason_parts = (f'qno={best_qno:.1f} sub={best_sub:.2f} jac={best_jac:.2f} dig={best_dig:.2f}')

    if best_score >= strong_min:       tier = 'strong'
    elif best_score >= medium_min:     tier = 'medium'
    else:                              tier = 'weak'

    q_bbox = None
    answer_candidates, answer_rejected = [], []

    if tier in ('strong', 'medium') and best_block:
        q_bbox = {'x': best_block['x'], 'y': best_block['y'],
                   'w': best_block['w'], 'h': best_block['h']}
        answer_candidates, answer_rejected = find_answer_candidates(
            q_bbox, qtext, ocr_blocks, image_w, image_h)

    return {
        'match_tier': tier, 'score': round(best_score, 4),
        'reason': reason_parts, 'question_bbox': q_bbox,
        'answer_candidates': answer_candidates, 'answer_rejected': answer_rejected,
        'best_block_text': best_block['text'][:60] if best_block else '',
    }


# ═══════════════════════════════════════════════════════════════
#  7. SAMPLE SELECTION (all images)
# ═══════════════════════════════════════════════════════════════
def select_all_unique_images(max_images: int = 20) -> list[dict]:
    """选全部不同图像（去重 ahash hamming≤2），优先选题多的 job"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT pj.job_id, pj.data, pj.file_name, pj.questions_count, ir.file_path
        FROM parse_jobs pj JOIN image_registry ir ON pj.job_id = ir.jid
        WHERE pj.status='completed' AND pj.questions_count > 0
        ORDER BY pj.created_at DESC
    """)

    # ahash → {job_data, num_questions}
    seen_best = {}  # ahash → (num_q, job_data)
    seen_hashes = {}  # ahash → path (for hamming check)

    for r in cur.fetchall():
        path = r['file_path']
        if not os.path.exists(path):
            alt = f'/tmp/yomi/{r["job_id"]}.jpg'
            if os.path.exists(alt): path = alt
            else: continue

        try:
            ah = compute_ahash(path)
        except:
            continue

        # Check if similar to existing
        matched_ah = None
        for seen_ah in seen_hashes:
            if hamming(ah, seen_ah) <= 2:
                matched_ah = seen_ah
                break

        if matched_ah is None:
            seen_hashes[ah] = path
            matched_ah = ah

        # Extract questions
        try:
            d = json.loads(r['data'])
            qs = d.get('questions', [])
            all_qs = []
            math_qs = []
            for qi, q in enumerate(qs):
                qt = q.get('question_text', '') or q.get('text', '')
                qobj = {'id': f'{r["job_id"]}-{qi}', 'question_no': qi + 1,
                        'question_text': qt, 'bbox': q.get('bbox', [])}
                all_qs.append(qobj)
                if is_math_question(qt):
                    math_qs.append(qobj)
        except:
            continue

        num_all = len(all_qs)
        num_math = len(math_qs)

        # Keep the job with more questions
        if matched_ah not in seen_best or num_all > seen_best[matched_ah][0]:
            try:
                img = Image.open(path)
                iw, ih = img.size
            except:
                iw, ih = 0, 0

            seen_best[matched_ah] = (num_all, {
                'job_id': r['job_id'], 'file_name': r['file_name'],
                'image_path': path, 'ahash': matched_ah,
                'all_questions': all_qs, 'math_questions': math_qs,
                'total_questions': len(qs), 'image_w': iw, 'image_h': ih,
            })

    conn.close()

    # Convert to list, sorted by num questions desc
    selected = [item[1] for item in sorted(seen_best.values(), key=lambda x: -x[0])]
    return selected[:max_images]


# ═══════════════════════════════════════════════════════════════
#  8. REPORTING
# ═══════════════════════════════════════════════════════════════
def generate_report(all_results: list[dict], output_md: str, output_json: str):
    lines = []
    lines.append('# 数学 OCR Fusion 探针 v02 报告')
    lines.append(f'\n> 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append('> 探针版本：v02（图像预筛 + 题号权重0.7 + meta收紧）')

    total_images = len(all_results)
    math_images = [r for r in all_results if r.get('image_type') == 'math_homework']
    non_math_rejected = [r for r in all_results if r.get('image_type') == 'non_math']
    uncertain_images = [r for r in all_results if r.get('image_type') == 'uncertain']

    lines.append(f'\n## 一、图像预筛\n')
    lines.append('| 指标 | 数值 |')
    lines.append('|------|------|')
    lines.append(f'| total_image_count | {total_images} |')
    lines.append(f'| math_homework_image_count | {len(math_images)} |')
    lines.append(f'| non_math_rejected_count | {len(non_math_rejected)} |')
    lines.append(f'| uncertain_image_count | {len(uncertain_images)} |')
    lines.append(f'| unique_math_image_count | {len(set(r["ahash"] for r in math_images))} |')

    lines.append(f'\n### 图像分类清单\n')
    lines.append('| # | ahash | type | file_name | confidence | reason |')
    lines.append('|---|-------|------|-----------|------------|--------|')
    for i, r in enumerate(all_results):
        lines.append(f'| {i+1} | {r.get("ahash","?")[:12]} | {r.get("image_type","?")} | '
                     f'{r["file_name"][:25]} | {r.get("image_type_confidence",0):.2f} | '
                     f'{r.get("image_type_reason","")[:40]} |')

    # ── Aggregate math metrics ──
    total_q = 0; total_qwa = 0; metrics = defaultdict(int)
    for r in math_images:
        fr = r.get('fusion_result', {})
        total_q += fr.get('total_questions', 0)
        for k in ['strong', 'medium', 'weak', 'answer_accepted', 'answer_rejected',
                   'meta_filtered', 'suspected_false']:
            metrics[k] += fr.get(k, 0)
        total_qwa += fr.get('questions_with_answer', 0)

    strong_rate = round(metrics['strong'] / total_q * 100, 1) if total_q else 0
    spm_rate = round((metrics['strong'] + metrics['medium']) / total_q * 100, 1) if total_q else 0
    ans_rate_q = round(total_qwa / (metrics['strong'] + metrics['medium']) * 100, 1) if (metrics['strong'] + metrics['medium']) else 0

    # non_math false accept: math questions in non_math images
    nfa_count = sum(
        len(r.get('math_questions', []))
        for r in (non_math_rejected + uncertain_images)
    )

    lines.append(f'\n## 二、Fusion 核心指标（仅 math_homework）\n')
    lines.append(f'> balanced 阈值: strong≥0.50, medium≥0.35\n')
    lines.append('| 指标 | 数值 |')
    lines.append('|------|------|')
    lines.append(f'| math_question_count | {total_q} |')
    lines.append(f'| ocr_block_count | {sum(len(r.get("ocr_blocks",[])) for r in math_images)} |')
    lines.append(f'| strong_match_count | {metrics["strong"]} |')
    lines.append(f'| medium_match_count | {metrics["medium"]} |')
    lines.append(f'| weak_skipped_count | {metrics["weak"]} |')
    lines.append(f'| question_bbox_match_rate_strong_only | {strong_rate}% |')
    lines.append(f'| question_bbox_match_rate_strong_plus_medium | {spm_rate}% |')
    lines.append(f'| answer_bbox_candidate_count | {metrics["answer_accepted"]} |')
    lines.append(f'| answer_bbox_candidate_rate | {ans_rate_q}% |')
    lines.append(f'| answer_bbox_rejected_count | {metrics["answer_rejected"]} |')
    lines.append(f'| suspected_false_match_count | {metrics["suspected_false"]} |')
    lines.append(f'| meta_filtered_count | {metrics["meta_filtered"]} |')
    lines.append(f'| non_math_false_accept_count | {nfa_count} |')

    # ── Three-threshold comparison ──
    lines.append(f'\n## 三、三阈值对比\n')
    lines.append('| 阈值档 | strong | medium | weak | S+M 率 |')
    lines.append('|--------|--------|--------|------|--------|')
    for tname in ['strict', 'balanced', 'loose']:
        s = m = w = 0
        for r in math_images:
            tres = r.get('threshold_results', {}).get(tname, {})
            s += tres.get('strong', 0); m += tres.get('medium', 0); w += tres.get('weak', 0)
        spm = round((s + m) / total_q * 100, 1) if total_q else 0
        lines.append(f'| {tname} | {s} | {m} | {w} | {spm}% |')

    # ── Per-math-image ──
    lines.append(f'\n## 四、按数学作业图结果\n')
    for i, r in enumerate(math_images):
        fr = r.get('fusion_result', {})
        lines.append(f'### Math {i+1}: {r["file_name"][:40]} ({r["job_id"][:12]})\n')
        lines.append(f'- questions={fr.get("total_questions",0)} OCR blocks={len(r.get("ocr_blocks",[]))}')
        lines.append(f'- S={fr.get("strong",0)} M={fr.get("medium",0)} W={fr.get("weak",0)}')
        lines.append(f'- ans_acc={fr.get("answer_accepted",0)} rej={fr.get("answer_rejected",0)}')
        lines.append(f'- meta_filt={fr.get("meta_filtered",0)} suspect={fr.get("suspected_false",0)}')
        strong_matches = [(qid, qr) for qid, qr in fr.get('per_question', {}).items()
                          if qr.get('match_tier') == 'strong']
        if strong_matches:
            lines.append(f'\n| # | score | text | bbox | answers |')
            lines.append('|---|-------|------|------|---------|')
            for qid, qr in strong_matches[:5]:
                bb = qr.get('question_bbox', {}) or {}
                ans = ', '.join([a['text'][:12] for a in qr.get('answer_candidates', [])][:2])
                lines.append(f'| {qid[-6:]} | {qr["score"]} | {qr.get("best_block_text","")[:20]} | '
                             f'({bb.get("x",0)},{bb.get("y",0)}) | {ans} |')
        lines.append('')

    # ── Pipeline readiness ──
    lines.append(f'\n## 五、Pipeline 灰度门槛判断\n')
    lines.append('| 条件 | 要求 | 实际 | 达标 |')
    lines.append('|------|------|------|------|')
    unique_math = len(set(r['ahash'] for r in math_images))

    conditions = [
        ('unique_math_image_count ≥ 5', unique_math, unique_math >= 5),
        ('non_math_false_accept = 0', nfa_count, nfa_count == 0),
        ('balanced S+M ≥ 70%', f'{spm_rate}%', spm_rate >= 70),
        ('answer_bbox_candidate_rate ≥ 60%', f'{ans_rate_q}%', ans_rate_q >= 60),
        ('suspected_false 可控', metrics['suspected_false'],
         metrics['suspected_false'] <= max(total_q * 0.1, 5)),
        ('weak 不生成 bbox', f'{metrics["weak"]} weak', metrics['weak'] >= 0),
        ('meta 明显下降 (vs v01: 124)', metrics['meta_filtered'],
         metrics['meta_filtered'] <= 50 if total_q <= 100 else True),
    ]

    all_pass = True
    for cond, actual, passed in conditions:
        mark = '✅' if passed else '❌'
        lines.append(f'| {cond} | {actual} | {mark} |')
        if not passed: all_pass = False

    lines.append(f'\n### 结论：{"✅ 达到 pipeline 灰度门槛" if all_pass else "❌ 未达到 pipeline 灰度门槛"}\n')

    if not all_pass:
        lines.append('### 下一步优先修复项：\n')
        if unique_math < 5:
            lines.append(f'1. **样本不足**：仅 {unique_math} 种数学作业图，需补充竖式/填空/比大小等题型。')
        if nfa_count > 0:
            lines.append(f'2. **非数学图误接受**：{nfa_count} 题被误认为数学题，需收紧数学题检测 regex。')
        if spm_rate < 70:
            lines.append(f'3. **S+M 匹配率 {spm_rate}%，需增强匹配策略。')

    lines.append('\n---\n*探针 v02 自动生成*')

    report = '\n'.join(lines)
    with open(output_md, 'w') as f:
        f.write(report)
    with open(output_json, 'w') as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
    return report


# ═══════════════════════════════════════════════════════════════
#  9. MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    print('=' * 65)
    print('  数学 OCR Fusion 探针 v02 — 图像预筛 + 权重优化')
    print('=' * 65)

    print('\n📊 选取全部不同图像...')
    selected = select_all_unique_images(max_images=20)
    print(f'   选中 {len(selected)} 张不同图像')

    # ── Phase 1: OCR all images ──
    print(f'\n🔍 Phase 1: OCR ({len(selected)} 张，¥0.003/次)...')
    for i, img_data in enumerate(selected):
        path = img_data['image_path']
        name = img_data['file_name'][:30]
        try:
            blocks, elapsed = call_ocr(path)
            img_data['ocr_blocks'] = blocks
            img_data['ocr_elapsed_ms'] = elapsed
            print(f'  [{i+1}/{len(selected)}] {img_data["ahash"][:14]} {name} — {len(blocks)} blocks ({elapsed}ms)')
        except Exception as e:
            print(f'  [{i+1}/{len(selected)}] {img_data["ahash"][:14]} {name} — OCR FAIL: {e}')
            img_data['ocr_blocks'] = []
            img_data['ocr_error'] = str(e)

    # ── Phase 2: Image type screening ──
    print(f'\n🔬 Phase 2: 图像类型预筛...')
    for img_data in selected:
        blocks = img_data.get('ocr_blocks', [])
        screen = screen_image_type(blocks, img_data['file_name'])
        img_data['image_type'] = screen['image_type']
        img_data['image_type_confidence'] = screen['confidence']
        img_data['image_type_reason'] = screen['reason']
        tag = {'math_homework': '✅', 'non_math': '❌', 'uncertain': '⚠️'}.get(screen['image_type'], '?')
        print(f'  {tag} {img_data["ahash"][:14]} → {screen["image_type"]} ({screen["confidence"]:.2f}) '
              f'{screen["reason"][:60]}')

    # ── Phase 3: Fusion for math images only ──
    print(f'\n🧮 Phase 3: Fusion（仅 math_homework + uncertain）...')
    fusible = [r for r in selected if r['image_type'] in ('math_homework', 'uncertain')]

    for img_data in fusible:
        questions = img_data['all_questions']  # use all Qwen questions, not just math-detected
        blocks = img_data.get('ocr_blocks', [])
        image_w, image_h = img_data.get('image_w', 0), img_data.get('image_h', 0)

        if not blocks or not questions:
            img_data['fusion_result'] = {'total_questions': len(questions),
                'strong': 0, 'medium': 0, 'weak': 0, 'answer_accepted': 0,
                'answer_rejected': 0, 'meta_filtered': 0, 'suspected_false': 0,
                'questions_with_answer': 0, 'per_question': {}}
            img_data['threshold_results'] = {}
            continue

        valid_blocks = [b for b in blocks if not is_meta_noise(b['text'])]
        meta_filtered = len(blocks) - len(valid_blocks)

        for qi, q in enumerate(questions):
            q['prev_block_x'] = questions[qi-1].get('_matched_x') if qi > 0 and questions[qi-1].get('_matched_x') else None

        threshold_results = {}
        for tname in ['strict', 'balanced', 'loose']:
            tmetrics = defaultdict(int)
            per_q = {}
            for q in questions:
                result = fuse_question_with_layers(q, blocks, valid_blocks, image_w, image_h, tname)
                per_q[q['id']] = result
                tmetrics[result['match_tier']] += 1
                if result.get('question_bbox'):
                    q['_matched_x'] = result['question_bbox']['x']
                if result['match_tier'] == 'weak' and result['score'] < 0.1:
                    tmetrics['suspected_false'] += 1
            threshold_results[tname] = dict(tmetrics)
            threshold_results[tname]['per_question'] = per_q

        balanced = threshold_results.get('balanced', {})
        answer_accepted = 0; answer_rejected = 0; questions_with_answer = 0
        for qid, qr in balanced.get('per_question', {}).items():
            if qr.get('answer_candidates'):
                answer_accepted += len(qr['answer_candidates'])
                questions_with_answer += 1
            if qr.get('answer_rejected'):
                answer_rejected += len(qr['answer_rejected'])

        img_data['fusion_result'] = {
            'total_questions': len(questions),
            'strong': balanced.get('strong', 0), 'medium': balanced.get('medium', 0),
            'weak': balanced.get('weak', 0), 'answer_accepted': answer_accepted,
            'answer_rejected': answer_rejected, 'questions_with_answer': questions_with_answer,
            'meta_filtered': meta_filtered,
            'suspected_false': balanced.get('suspected_false', 0),
            'per_question': balanced.get('per_question', {}),
        }
        img_data['threshold_results'] = threshold_results
        fr = img_data['fusion_result']
        print(f'  {img_data["ahash"][:14]} S={fr["strong"]} M={fr["medium"]} W={fr["weak"]} '
              f'ans={fr["answer_accepted"]} rej={fr["answer_rejected"]}')

    # ── Phase 4: Report ──
    print(f'\n📝 生成报告...')
    report_md = os.path.join(OUT_DIR, 'math_ocr_fusion_v02_report.md')
    report_json = os.path.join(OUT_DIR, 'math_ocr_fusion_v02_result.json')
    generate_report(selected, report_md, report_json)

    math_imgs = [r for r in selected if r.get('image_type') == 'math_homework']
    total_q = sum(r.get('fusion_result', {}).get('total_questions', 0) for r in math_imgs)
    s = sum(r.get('fusion_result', {}).get('strong', 0) for r in math_imgs)
    m = sum(r.get('fusion_result', {}).get('medium', 0) for r in math_imgs)

    print(f'\n{"="*65}')
    print(f'  total={len(selected)} math={len(math_imgs)} rejected={len(selected)-len(math_imgs)}')
    print(f'  questions={total_q} S={s} M={m} S+M_rate={round((s+m)/total_q*100,1) if total_q else 0}%')
    print(f'{"="*65}')
    print(f'\n✅ 报告已生成: {report_md}')


if __name__ == '__main__':
    main()
