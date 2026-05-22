#!/usr/bin/env python3
"""
B+ 方案实验：10秒高精度识别可落地方案验证
- 前置分类器 + 图像预处理 + OCR → 裁2-3大块 → 并发Qwen(管道) + 数学/英语规则判题 + 质量门
独立脚本，不接主链路。
"""
import os, sys, time, json, re, asyncio, io
from pathlib import Path
from PIL import Image, ImageEnhance, ImageFilter
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vision_client import QwenVLClient
from ocr_client import AliyunOCRClient

# ─── 配置 ──────────────────────────────────────────────
QWEN_TIMEOUT = 10          # Qwen超时（秒），10s SLA下最多留给Qwen
QWEN_MAX_TOKENS = 600
QWEN_CONCURRENCY = 2
COST_PER_1K_TOKENS = 0.003
IMG_MAX_HEIGHT = 800
IMG_QUALITY = 65

SAMPLES = {
    "math":    "/tmp/yomi/samples/img_aebd4fe0604c.jpg",
    "chinese": "/tmp/yomi/samples/img_9d310897f49c.jpg",
    "english": "/tmp/yomi/samples/img_2602c37c11b0.jpg",
    "negative": "/tmp/yomi/samples/img_0ccb93bbe033.jpg",
}

QWEN_PROMPT = (
    "图中每题一行: 题号|题目内容|孩子手写答案|题型(口算/竖式/选择/填空/连线/画图/其他)|"
    "置信度(0-1)。无答案填无。不要标题日期。不要解释。"
)

@dataclass
class RunResult:
    subject: str
    total_ms: float = 0
    preprocess_ms: float = 0
    classify_ms: float = 0
    ocr_ms: float = 0
    qwen_ms: float = 0
    grade_ms: float = 0
    questions_count: int = 0
    child_answer_non_empty: int = 0
    answer_bbox_non_empty: int = 0
    is_correct_count: int = 0
    noise_count: int = 0
    is_homework: bool = False
    negative_blocked: bool = False
    cost_est: float = 0.0
    errors: list = field(default_factory=list)

# ═══════════════════════════════════════════════════════
# 1. 图像预处理
# ═══════════════════════════════════════════════════════
def preprocess_image(img_bytes: bytes) -> bytes:
    """轻量预处理：对比度增强 + 缩放。保持RGB，不做灰度化。"""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

    # 1) 对比度增强（Color增强器，保持色彩）
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.15)  # 轻度增强

    # 2) 缩放到目标高度
    w, h = img.size
    if h > IMG_MAX_HEIGHT:
        scale = IMG_MAX_HEIGHT / h
        img = img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=IMG_QUALITY)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════
# 2. 前置分类器：作业/非作业
# ═══════════════════════════════════════════════════════
def is_homework_image(ocr_blocks: list) -> bool:
    """基于OCR文本判断是否为作业图片"""
    if not ocr_blocks:
        return False

    text = " ".join(b["text"] for b in ocr_blocks)

    # 作业特征（三科通用）— 必须是完整词语，不是字符类
    hw_signals = [
        # 数学
        r'\d{1,3}\s*[+−\-×÷=]\s*\d+',              # 算式
        r'口算|竖式计算|脱式计算|列竖式',
        r'填空题|选择题|判断题|连线题|比大小',
        r'看图列式|解决问题|应用题',
        # 语文
        r'看拼音|读拼音|比一比|组词|造句',
        r'第\d+课|识字|写字|词语|生字',
        r'看拼音写|读一读|写一写|连一连',
        r'课文|朗读|背诵',
        # 英语
        r'\b(Unit|Lesson|Review)\s*\d',
        r'\b(apple|banana|cat|dog|elephant|book|pen|ruler)\b',
        r'[A-D][.、）\)]\s',
        r'\b(read|write|listen|circle|match|choose)\b',
        # 通用作业特征
        r'年级|班级|姓名|日期|得分|评价',
        r'作业|练习|课时|单元|测试|试卷',
        r'建议用时|实际用时|完成时间|用时',
        r'家长签字|老师批改|批改日期',
    ]

    # 非作业特征（强信号，命中一条就大幅减分）
    non_hw_signals = [
        r'登录|注册|密码|验证码|扫码|扫一扫|二维码',
        r'手机号|短信|微信|支付|购买|付款|订单|商品',
        r'用户协议|隐私政策|服务条款',
        r'欢迎回来|工作台|我的|设置|消息|通知中心',
        r'退出|注销|切换账号',
        r'\b(login|register|password|verify|sign\s*in|sign\s*up|welcome\s*back)\b',
        r'Failed to fetch|network error|connection refused',
        r'^\d{11}$',  # 纯手机号
    ]

    hw_score = sum(1 for p in hw_signals if re.search(p, text, re.IGNORECASE))
    non_score = sum(1 for p in non_hw_signals if re.search(p, text, re.IGNORECASE))

    return hw_score > non_score and hw_score >= 1


# ═══════════════════════════════════════════════════════
# 3. 数学规则判题
# ═══════════════════════════════════════════════════════
def grade_math(question_text: str, child_answer: str) -> tuple:
    """返回 (is_correct: Optional[bool], correct_answer: Optional[str])"""
    if not child_answer or child_answer in ("无", "null", "none"):
        return None, None
    try:
        from math_grader import grade as math_grade
        return math_grade(question_text, child_answer)
    except ImportError:
        pass

    # 内联轻量数学判题
    q = question_text.strip()
    a = child_answer.strip()

    # 提取算式: "24+37=" 或 "24 + 37 ="
    expr_match = re.search(r'([\d]+)\s*([+\-×÷])\s*([\d]+)\s*=', q)
    if expr_match:
        n1, op, n2 = int(expr_match.group(1)), expr_match.group(2), int(expr_match.group(3))
        if op == '+': correct = str(n1 + n2)
        elif op == '-': correct = str(n1 - n2)
        elif op in ('×', '*'): correct = str(n1 * n2)
        elif op in ('÷', '/'): correct = str(n1 // n2) if n2 != 0 and n1 % n2 == 0 else f"{n1/n2:.1f}"
        else: return None, None
        return a.strip() == correct, correct

    # 比大小: "45 ○ 54" → 中间符号
    cmp_match = re.search(r'([\d]+)\s*[○Oo]\s*([\d]+)', q)
    if cmp_match:
        n1, n2 = int(cmp_match.group(1)), int(cmp_match.group(2))
        correct = ">" if n1 > n2 else ("<" if n1 < n2 else "=")
        # 答案可能是 > < = 或文字
        if a.strip() in (">", "<", "="):
            return a.strip() == correct, correct

    # 填空: "8的一半是()"
    half_match = re.search(r'([\d]+)\s*的一半', q)
    if half_match:
        correct = str(int(half_match.group(1)) // 2)
        return a.strip() == correct, correct

    return None, None  # 无法规则判题


# ═══════════════════════════════════════════════════════
# 4. 英语规则判题
# ═══════════════════════════════════════════════════════
def grade_english(question_text: str, child_answer: str) -> tuple:
    """英语选择/字母/单词填空规则判题。返回 (is_correct, correct_answer)"""
    if not child_answer or child_answer in ("无", "null", "none"):
        return None, None

    q = question_text.strip()
    a = child_answer.strip()

    # 1) 字母书写题：如 "A( )" → 答案是 a 或 A
    letter_fill = re.search(r'([A-Za-z])\s*[（(]\s*[）)]', q)
    if letter_fill:
        expected = letter_fill.group(1)
        # 答案可能是大写或小写
        if a.upper() == expected.upper() or a.lower() == expected.lower():
            return True, expected
        else:
            return False, expected

    # 2) 字母连线/匹配：如 "( )d" → 答案 D
    letter_blank = re.search(r'[（(]\s*[）)]\s*([a-z])', q, re.IGNORECASE)
    if letter_blank:
        expected = letter_blank.group(1).upper()
        if a.upper() == expected:
            return True, expected
        else:
            return False, expected

    # 3) 单词填空（已知正确答案在题干中）：如 "c_t → cat"
    word_fill = re.search(r'([a-z])[_]+([a-z]*)', q, re.IGNORECASE)
    if word_fill:
        # 构建完整单词
        prefix, suffix = word_fill.group(1), word_fill.group(2)
        # 常见短词补全
        common_words = {
            ('c', 't'): 'cat', ('d', 'g'): 'dog', ('b', 'g'): 'big',
            ('p', 'n'): 'pen', ('r', 'n'): 'run', ('c', 'p'): 'cup',
        }
        key = (prefix.lower(), suffix.lower())
        if key in common_words:
            expected = common_words[key]
            return a.lower() == expected, expected

    # 4) 选择题：题干中有选项 A/B/C/D
    choices = re.findall(r'([A-D])[.、）\)]', q, re.IGNORECASE)
    if choices:
        # 孩子答案应该是选项字母
        opts = [c.upper() for c in choices]
        if a.upper() in opts:
            # 有选项但无标准答案 → 只验证答案在选项中，不判对错
            return None, None  # 不硬判

    return None, None


# ═══════════════════════════════════════════════════════
# 解析管道输出
# ═══════════════════════════════════════════════════════
def parse_pipe_output(content: str) -> list:
    """解析: 题号|题干|孩子答案|题型|置信度"""
    questions = []
    for line in content.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        parts = line.split("|")
        if len(parts) < 2:
            continue

        q = {
            "question_number": parts[0].strip(),
            "question_text": parts[1].strip() if len(parts) > 1 else "",
            "child_answer": parts[2].strip() if len(parts) > 2 else None,
            "question_type": parts[3].strip() if len(parts) > 3 else "其他",
            "confidence": float(parts[4].strip()) if len(parts) > 4 and parts[4].strip() else 0.7,
        }

        # 清理
        if q["child_answer"] in ("无", "null", "none", "", "没有", "孩子未写", "未写"):
            q["child_answer"] = None

        # 去掉题号前缀（必须跟明确分隔符.、．)）才剥离）
        q["question_text"] = re.sub(r'^\d+[.、．)）]\s*', '', q["question_text"]).strip()

        if q["question_text"]:  # 至少有题干
            questions.append(q)

    return questions


# ═══════════════════════════════════════════════════════
# 5. 主流程
# ═══════════════════════════════════════════════════════
async def run_bplus(subject: str, path: str, qwen: QwenVLClient, ocr_client: AliyunOCRClient,
                    sem: asyncio.Semaphore) -> RunResult:
    result = RunResult(subject=subject)
    t_total = time.time()
    loop = asyncio.get_event_loop()

    # ── Step 0: 加载图片 ──
    with open(path, "rb") as f:
        img_bytes = f.read()

    # ── Step 1: 图像预处理 ──
    t_pre = time.time()
    try:
        img_proc = preprocess_image(img_bytes)
    except Exception as e:
        img_proc = compress_image_simple(img_bytes)  # fallback
        result.errors.append(f"preprocess_fallback:{e}")
    result.preprocess_ms = (time.time() - t_pre) * 1000

    # ── Step 2: OCR ──
    t_ocr = time.time()
    ocr = await loop.run_in_executor(None, lambda: ocr_client.recognize(img_proc))
    result.ocr_ms = ocr.get("latency_ms", 0)
    blocks = ocr.get("blocks", [])

    # ── Step 3: 前置分类 ──
    t_cls = time.time()
    result.is_homework = is_homework_image(blocks)
    result.classify_ms = (time.time() - t_cls) * 1000

    if not result.is_homework:
        result.negative_blocked = True
        result.total_ms = (time.time() - t_total) * 1000
        return result

    # ── Step 4: 裁2-4大块（动态，根据blocks数量） ──
    img = Image.open(io.BytesIO(img_proc))
    iw, ih = img.size

    n_blocks = len(blocks)
    if n_blocks >= 30:
        # 4块 — 高密度文本
        y_sorted = sorted(b["y"] for b in blocks)
        q1, q2, q3 = y_sorted[n_blocks//4], y_sorted[n_blocks//2], y_sorted[3*n_blocks//4]
        overlap = int(ih * 0.04)
        regions = [
            (0, 0, iw, q2 + overlap),
            (0, q1 - overlap, iw, q3 - q1 + 2*overlap),
            (0, q2 - overlap, iw, ih - q2 + overlap),
        ]
    elif n_blocks >= 20:
        # 3块
        y_sorted = sorted(b["y"] for b in blocks)
        p33, p67 = y_sorted[n_blocks//3], y_sorted[2*n_blocks//3]
        overlap = int(ih * 0.05)
        regions = [
            (0, 0, iw, p67 + overlap),
            (0, p33 - overlap, iw, ih - p33 + overlap),
        ]
    elif n_blocks >= 8:
        mid_y = sorted(b["y"] for b in blocks)[n_blocks//2]
        overlap = int(ih * 0.06)
        regions = [
            (0, 0, iw, mid_y + overlap),
            (0, mid_y - overlap, iw, ih - mid_y + overlap),
        ]
    else:
        regions = [(0, 0, iw, ih)]

    # ── Step 5: 并发Qwen ──
    t_qwen_start = time.time()

    async def _qwen_one(region):
        x, y, w, h = region
        x = max(0, x); y = max(0, y)
        w = min(w, iw - x); h = min(h, ih - y)
        if w <= 0 or h <= 0:
            return {"success": False, "error": "invalid_region"}
        crop = img.crop((x, y, x+w, y+h))
        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=75)
        crop_bytes = buf.getvalue()

        async with sem:
            r = await loop.run_in_executor(
                None, lambda: qwen._call(
                    image_bytes=crop_bytes, prompt=QWEN_PROMPT,
                    max_tokens=QWEN_MAX_TOKENS, timeout=QWEN_TIMEOUT))
        return r

    qwen_results = await asyncio.gather(*[_qwen_one(r) for r in regions], return_exceptions=True)
    result.qwen_ms = (time.time() - t_qwen_start) * 1000

    # ── Step 6: 解析 + 去重 ──
    all_questions = []
    total_tokens = 0
    for r in qwen_results:
        if isinstance(r, Exception):
            result.errors.append(f"qwen_exception:{r}")
            continue
        if r.get("success"):
            qs = parse_pipe_output(r.get("content", ""))
            all_questions.extend(qs)
            usage = r.get("usage", {})
            total_tokens += usage.get("total_tokens", 400)
        else:
            result.errors.append(f"qwen_fail:{r.get('error','')[:30]}")

    # 去重（按题干前20字符）
    seen = set()
    deduped = []
    for q in all_questions:
        key = q["question_text"][:20]
        if key not in seen:
            seen.add(key)
            deduped.append(q)
    all_questions = deduped

    result.questions_count = len(all_questions)
    result.cost_est = total_tokens / 1000 * COST_PER_1K_TOKENS

    # ── Step 7: 规则判题 ──
    t_grade = time.time()
    debug_printed = 0
    for q in all_questions:
        child_ans = q.get("child_answer")
        qtype = q.get("question_type", "")
        qtext = q.get("question_text", "")

        if not child_ans:
            continue

        # 数学规则判题（仅数学学科）
        if subject == "math" or (subject != "chinese" and any(k in qtype for k in ("口算", "竖式", "填空"))):
            is_c, correct = grade_math(qtext, child_ans)
            if debug_printed < 2:
                print(f"    [grade_math] '{qtext[:40]}' ans='{child_ans}' → is_c={is_c} correct={correct}")
                debug_printed += 1
            if is_c is not None:
                q["is_correct"] = is_c
                q["correct_answer"] = correct
                result.is_correct_count += 1
                continue

        # 英语规则判题
        if subject == "english" or any(k in qtype for k in ("选择", "填空")):
            is_c, correct = grade_english(q["question_text"], child_ans)
            if is_c is not None:
                q["is_correct"] = is_c
                q["correct_answer"] = correct
                result.is_correct_count += 1
    result.grade_ms = (time.time() - t_grade) * 1000

    # ── Step 8: 质量门 ──
    result.child_answer_non_empty = sum(
        1 for q in all_questions if q.get("child_answer"))
    result.answer_bbox_non_empty = 0  # Qwen不管bbox

    # 统计噪声（误切的非题目内容）
    noise_pats = [
        r'^\s*(年级|班级|姓名|日期|用时|得分|评价|批改|第.*课|Lesson|Unit|建议|实际)\s*$',
        r'^\s*\d{1,2}[分秒]\s*$',
        r'^[√×✓✗✔✘⭐🌟👍👎]\s*$',
    ]
    for q in all_questions:
        for pat in noise_pats:
            if re.search(pat, q["question_text"], re.IGNORECASE):
                result.noise_count += 1
                break

    result.total_ms = (time.time() - t_total) * 1000
    return result


def compress_image_simple(img_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(img_bytes))
    w, h = img.size
    if h > IMG_MAX_HEIGHT:
        scale = IMG_MAX_HEIGHT / h
        img = img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=IMG_QUALITY)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════
async def main():
    qwen = QwenVLClient()
    ocr = AliyunOCRClient()
    sem = asyncio.Semaphore(QWEN_CONCURRENCY)

    print("=" * 70)
    print("B+ 方案实验：10秒高精度识别")
    print(f"预处理 + 分类 + OCR + 2块并发Qwen + 规则判题 + 质量门")
    print("=" * 70)

    results = {}
    total_cost = 0.0

    for subject, path in SAMPLES.items():
        if not os.path.exists(path):
            continue

        print(f"\n{'─'*55}")
        print(f"📷 {subject}...")
        r = await run_bplus(subject, path, qwen, ocr, sem)
        results[subject] = r
        total_cost += r.cost_est

        blocked = "🚫已拦截" if r.negative_blocked else ""
        answers = []
        # 列出答案样本
        print(f"  分类={'作业✅' if r.is_homework else '非作业'} {blocked} "
              f"预处理={r.preprocess_ms:.0f}ms OCR={r.ocr_ms:.0f}ms Qwen={r.qwen_ms:.0f}ms "
              f"判题={r.grade_ms:.0f}ms 总={r.total_ms:.0f}ms")
        print(f"  题目={r.questions_count} 答案={r.child_answer_non_empty} "
              f"判题={r.is_correct_count} 噪声={r.noise_count} ¥{r.cost_est:.4f}")
        if r.errors:
            print(f"  错误: {r.errors[:2]}")

    # ─── 汇总 ────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"📊 B+ 汇总 (总费用 ¥{total_cost:.4f})")
    print(f"{'='*70}")
    print(f"{'指标':<20} {'数学':>12} {'语文':>12} {'英语':>12} {'负样本':>12}")
    print("-" * 70)

    metrics = [
        ("总耗时(ms)", "total_ms"),
        ("预处理(ms)", "preprocess_ms"),
        ("OCR(ms)", "ocr_ms"),
        ("Qwen(ms)", "qwen_ms"),
        ("判题(ms)", "grade_ms"),
        ("题数", "questions_count"),
        ("答案>0", "child_answer_non_empty"),
        ("判对错", "is_correct_count"),
        ("噪声", "noise_count"),
        ("费用¥", "cost_est"),
    ]

    for label, attr in metrics:
        vals = []
        for s in ["math", "chinese", "english", "negative"]:
            r = results.get(s)
            if not r:
                vals.append("-")
            elif attr == "cost_est":
                vals.append(f"{getattr(r, attr):.4f}")
            else:
                vals.append(str(getattr(r, attr)))
        print(f"{label:<20} {vals[0]:>12} {vals[1]:>12} {vals[2]:>12} {vals[3]:>12}")

    # 特殊指标
    neg = results.get("negative")
    neg_blocked = "✅已拦截" if (neg and neg.negative_blocked) else "❌未拦截"
    print(f"{'负样本':<20} {neg_blocked:>50}")

    # QA 指标
    for s in ["math", "chinese", "english"]:
        r = results.get(s)
        if r and r.child_answer_non_empty == 0 and r.questions_count > 0:
            print(f"⚠️ {s}: 答案0/{r.questions_count} — 假结果危险！")

    print(f"\n{'='*70}")
    print("✅ 实验完成")

if __name__ == "__main__":
    asyncio.run(main())
