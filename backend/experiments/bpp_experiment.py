#!/usr/bin/env python3
"""
B++ 实验：答案区优先识别
- OCR定位题区+答案区 → 裁小图（题组3-5题/块）→ Qwen管道识别
- 拍摄质量前置检查
- 数学/英语规则判题
- 质量门
"""
import os, sys, time, json, re, asyncio, io
from pathlib import Path
from PIL import Image, ImageEnhance, ImageFilter, ImageStat
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vision_client import QwenVLClient
from ocr_client import AliyunOCRClient

# ─── 配置 ──────────────────────────────────────────────
QWEN_TIMEOUT = 8
QWEN_MAX_TOKENS = 500
QWEN_CONCURRENCY = 2
COST_PER_1K_TOKENS = 0.003
IMG_MAX_HEIGHT = 900
IMG_QUALITY = 70

# 所有14张样本
SAMPLES = {
    "math":     "/tmp/yomi/samples/img_aebd4fe0604c.jpg",
    "chinese":  "/tmp/yomi/samples/img_9d310897f49c.jpg",
    "english":  "/tmp/yomi/samples/img_2602c37c11b0.jpg",
    "negative": "/tmp/yomi/samples/img_0ccb93bbe033.jpg",
}
# 10张回归（排除4张已用样本）
_used_paths = set(SAMPLES.values())
_all_samples = sorted(
    [f"/tmp/yomi/samples/{f}" for f in os.listdir("/tmp/yomi/samples")
     if f.startswith("img_") and f.endswith(".jpg")]
)
REGRESSION = [f for f in _all_samples if f not in _used_paths][:10]

QWEN_PROMPT = (
    "图中每题一行: 题号|题目内容|孩子手写答案|题型(口算/竖式/选择/填空/连线/画图/其他)|"
    "置信度(0-1)。无答案填无。不要标题日期。不要解释。"
)

@dataclass
class RunResult:
    subject: str
    total_ms: float = 0
    quality_ms: float = 0
    ocr_ms: float = 0
    qwen_ms: float = 0
    grade_ms: float = 0
    questions_count: int = 0
    child_answer_non_empty: int = 0
    answer_bbox_non_empty: int = 0
    is_correct_count: int = 0
    need_answer_key: int = 0
    quality_reject: str = ""      # 空=通过
    is_homework: bool = False
    negative_blocked: bool = False
    cost_est: float = 0.0
    errors: list = field(default_factory=list)

# ═══════════════════════════════════════════════════════
# 0. 拍摄质量检查
# ═══════════════════════════════════════════════════════
def check_quality(img_bytes: bytes, ocr_blocks: list) -> str:
    """返回空字符串=通过, 否则返回拒绝原因"""
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("L")
        w, h = img.size

        # 过暗：平均亮度 < 80
        stat = ImageStat.Stat(img)
        brightness = stat.mean[0]
        if brightness < 80:
            return "too_dark"

        # 模糊：拉普拉斯方差 < 50
        lap = img.filter(ImageFilter.Kernel((3,3),
            [-1,-1,-1,-1,8,-1,-1,-1,-1], scale=1, offset=128))
        lap_stat = ImageStat.Stat(lap)
        if lap_stat.var[0] < 50:
            return "too_blurry"

        # 字太小：中位block高度 < 12px
        if ocr_blocks:
            heights = [b.get("h", 0) for b in ocr_blocks]
            if heights:
                med_h = sorted(heights)[len(heights)//2]
                if med_h < 12:
                    return "text_too_small"

        # 半页图：内容区占比 < 40%
        if ocr_blocks:
            ys = [b["y"] for b in ocr_blocks]
            content_h = max(ys) - min(ys) + max(heights) if ys else h
            if content_h / h < 0.4:
                return "half_page"

    except Exception as e:
        return ""  # 出错不拦截

    return ""


# ═══════════════════════════════════════════════════════
# 1. 分类器
# ═══════════════════════════════════════════════════════
def is_homework_image(ocr_blocks: list) -> bool:
    if not ocr_blocks:
        return False
    text = " ".join(b["text"] for b in ocr_blocks)

    hw_signals = [
        r'\d{1,3}\s*[+\-×÷=]\s*\d+', r'口算|竖式计算|脱式计算',
        r'填空题|选择题|判断题|连线题|比大小', r'看拼音|读拼音|比一比|组词|造句',
        r'第\d+课|识字|写字|词语|生字', r'\b(Unit|Lesson|Review)\s*\d',
        r'\b(apple|banana|cat|dog|book|pen)\b', r'[A-D][.、）\)]\s',
        r'年级|班级|姓名|日期|得分', r'作业|练习|课时|单元|测试|试卷',
        r'建议用时|实际用时|家长签字|批改',
    ]
    non_signals = [
        r'登录|注册|密码|验证码|扫码|二维码', r'手机号|微信|支付|购买|订单',
        r'用户协议|隐私政策|欢迎回来|工作台', r'退出|注销|切换账号',
        r'\b(login|register|password|verify|welcome)\b', r'Failed to fetch',
    ]
    hw = sum(1 for p in hw_signals if re.search(p, text, re.I))
    non = sum(1 for p in non_signals if re.search(p, text, re.I))
    return hw > non and hw >= 1


# ═══════════════════════════════════════════════════════
# 2. 答案区定位 → 裁小图（题组3-5题/块）
# ═══════════════════════════════════════════════════════
def locate_answer_zones(blocks: list, img_w: int, img_h: int) -> list:
    """
    基于OCR blocks定位题区+答案区，返回裁剪区域列表。
    每个区域覆盖3-5题（题组），尽量只含题目+答案区。
    """
    if not blocks:
        return [(0, 0, img_w, img_h)]

    # 找题号标记
    q_pat = re.compile(r'^\s*(\d{1,3})[.、．)）\s]')
    blocks_sorted = sorted(blocks, key=lambda b: (b["y"], b["x"]))

    markers = []
    for i, b in enumerate(blocks_sorted):
        m = q_pat.match(b["text"].strip())
        if m:
            markers.append({"num": int(m.group(1)), "y": b["y"], "x": b["x"], "h": b["h"], "idx": i})

    if len(markers) < 2:
        # 无题号：按y自然分段
        if len(blocks_sorted) >= 10:
            ys = sorted(set(b["y"] for b in blocks_sorted))
            n_groups = min(4, len(ys)//5 + 1)
            step = len(ys) // n_groups if n_groups > 0 else len(ys)
            regions = []
            for g in range(n_groups):
                g_start = ys[g * step] if g * step < len(ys) else 0
                g_end = ys[min((g+1)*step, len(ys)-1)] + 60 if (g+1)*step < len(ys) else img_h
                regions.append((0, max(0, g_start-10), img_w, min(img_h, g_end-g_start+10)))
            return regions[:4]  # 最多4块
        return [(0, 0, img_w, img_h)]

    # 按题号分组：每组3-5题
    GROUP_SIZE = 4
    regions = []
    for g_start in range(0, len(markers), GROUP_SIZE):
        g_end = min(g_start + GROUP_SIZE, len(markers))
        g_markers = markers[g_start:g_end]

        # 题目区：第一题标记到最后一题标记+答案预估高度
        y_top = max(0, g_markers[0]["y"] - 6)

        # 答案区预估：在最后一题下方约80-120px（手写答案区）
        if g_end < len(markers):
            y_bot = min(img_h, markers[g_end]["y"] - 6)
        else:
            y_bot = min(img_h, g_markers[-1]["y"] + g_markers[-1]["h"] + 100)

        # 左右边距：利用OCR blocks的x范围
        x_vals = [b["x"] for b in blocks_sorted]
        x_left = max(0, min(x_vals) - 10)
        x_right = min(img_w, max(b["x"]+b.get("w",50) for b in blocks_sorted) + 10)

        if y_bot - y_top > 20 and x_right - x_left > 30:
            regions.append((x_left, y_top, x_right - x_left, y_bot - y_top))

    return regions[:6]  # 最多6块


# ═══════════════════════════════════════════════════════
# 3. 数学/英语判题（复用自B+）
# ═══════════════════════════════════════════════════════
def grade_math(qt: str, ans: str) -> tuple:
    if not ans or ans in ("无","null","none"):
        return None, None
    try:
        from math_grader import _try_math_rule_grading
        r = _try_math_rule_grading(qt, str(ans))
        if r: return r.get("is_correct"), r.get("explanation","")
    except: pass

    m = re.search(r'([\d]+)\s*([+\-×÷])\s*([\d]+)\s*=', qt)
    if m:
        n1, op, n2 = int(m.group(1)), m.group(2), int(m.group(3))
        correct = str(n1+n2) if op=='+' else str(n1-n2) if op=='-' else str(n1*n2) if op in ('×','*') else str(n1//n2)
        return ans.strip()==correct, correct
    return None, None

def grade_english(qt: str, ans: str) -> tuple:
    if not ans or ans in ("无","null","none"):
        return None, None
    # 字母题: "A( )"
    m = re.search(r'([A-Za-z])\s*[（(]\s*[）)]', qt)
    if m:
        exp = m.group(1)
        return ans.upper()==exp.upper(), exp
    # 字母匹配: "( )d"
    m = re.search(r'[（(]\s*[）)]\s*([a-z])', qt, re.I)
    if m:
        exp = m.group(1).upper()
        return ans.upper()==exp, exp
    return None, None


# ═══════════════════════════════════════════════════════
# 4. 解析管道输出
# ═══════════════════════════════════════════════════════
def parse_pipe(content: str) -> list:
    qs = []
    for l in content.strip().split("\n"):
        l = l.strip()
        if not l or l.startswith("```"): continue
        parts = l.split("|")
        if len(parts) < 2: continue
        ans = parts[2].strip() if len(parts) > 2 else None
        if ans in ("无","null","none","","孩子未写","未写"): ans = None
        qt = re.sub(r'^\d+[.、．)）]\s*', '', parts[1].strip()).strip()
        # 置信度安全解析
        conf = 0.7
        if len(parts) > 4 and parts[4].strip():
            try: conf = float(parts[4].strip())
            except ValueError: conf = 0.7
        if qt:
            qs.append({
                "question_text": qt,
                "child_answer": ans,
                "question_type": parts[3].strip() if len(parts)>3 else "其他",
                "confidence": conf,
            })
    return qs


# ═══════════════════════════════════════════════════════
# 5. B++ 主流程
# ═══════════════════════════════════════════════════════
async def run_bpp(subject: str, path: str, qwen: QwenVLClient, ocr_c: AliyunOCRClient,
                  sem: asyncio.Semaphore) -> RunResult:
    r = RunResult(subject=subject)
    t0 = time.time()
    loop = asyncio.get_event_loop()

    with open(path, "rb") as f:
        raw = f.read()

    # ── 图片压缩 ──
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    if h > IMG_MAX_HEIGHT:
        scale = IMG_MAX_HEIGHT / h
        img = img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)
    buf = io.BytesIO(); img.save(buf, format="JPEG", quality=IMG_QUALITY)
    img_bytes = buf.getvalue()
    iw, ih = img.size

    # ── OCR ──
    t_ocr = time.time()
    ocr = await loop.run_in_executor(None, lambda: ocr_c.recognize(img_bytes))
    r.ocr_ms = ocr.get("latency_ms", 0)
    blocks = ocr.get("blocks", [])

    # ── 质量检查 ──
    t_q = time.time()
    r.quality_reject = check_quality(raw, blocks)
    r.quality_ms = (time.time() - t_q) * 1000
    if r.quality_reject:
        r.total_ms = (time.time() - t0) * 1000
        return r

    # ── 分类 ──
    r.is_homework = is_homework_image(blocks)
    if not r.is_homework:
        r.negative_blocked = True
        r.total_ms = (time.time() - t0) * 1000
        return r

    if not blocks:
        r.total_ms = (time.time() - t0) * 1000
        return r

    # ── 答案区定位 + 裁小图 ──
    regions = locate_answer_zones(blocks, iw, ih)
    if not regions:
        r.total_ms = (time.time() - t0) * 1000
        return r

    # ── 并发Qwen（每个小块） ──
    t_qwen_start = time.time()

    async def _qwen_one(region):
        rx, ry, rw, rh = region
        rx = max(0, rx); ry = max(0, ry)
        rw = min(rw, iw-rx); rh = min(rh, ih-ry)
        if rw <= 0 or rh <= 0:
            return {"success": False}
        crop = img.crop((rx, ry, rx+rw, ry+rh))
        cbuf = io.BytesIO(); crop.save(cbuf, format="JPEG", quality=75)
        async with sem:
            return await loop.run_in_executor(
                None, lambda: qwen._call(image_bytes=cbuf.getvalue(), prompt=QWEN_PROMPT,
                                         max_tokens=QWEN_MAX_TOKENS, timeout=QWEN_TIMEOUT))

    qwen_results = await asyncio.gather(*[_qwen_one(rg) for rg in regions], return_exceptions=True)
    r.qwen_ms = (time.time() - t_qwen_start) * 1000

    # ── 汇总 + 去重 ──
    all_qs = []
    total_tokens = 0
    for qr in qwen_results:
        if isinstance(qr, Exception) or not qr.get("success"):
            continue
        all_qs.extend(parse_pipe(qr.get("content","")))
        total_tokens += qr.get("usage",{}).get("total_tokens", 350)

    # 去重
    seen = set()
    dedup = []
    for q in all_qs:
        key = q["question_text"][:25]
        if key not in seen:
            seen.add(key)
            dedup.append(q)
    all_qs = dedup

    r.questions_count = len(all_qs)
    r.cost_est = total_tokens / 1000 * COST_PER_1K_TOKENS

    # ── 答案统计 ──
    r.child_answer_non_empty = sum(1 for q in all_qs if q.get("child_answer"))
    r.answer_bbox_non_empty = 0  # Qwen不管bbox

    # ── 判题 ──
    t_grade = time.time()
    need_key = 0
    for q in all_qs:
        ans = q.get("child_answer")
        qt = q.get("question_text","")
        qtype = q.get("question_type","")

        if not ans: continue

        # 数学
        if subject == "math" or any(k in qtype for k in ("口算","竖式","填空")):
            is_c, _ = grade_math(qt, ans)
            if is_c is not None:
                r.is_correct_count += 1
                continue

        # 英语
        if "english" in subject.lower() or any(k in qtype for k in ("选择","填空","连线")):
            is_c, _ = grade_english(qt, ans)
            if is_c is not None:
                r.is_correct_count += 1
            else:
                # 选择题无标准答案 → need_answer_key
                if re.search(r'[A-D][.、）\)]', qt, re.I) and ans.upper() in "ABCD":
                    need_key += 1

    r.need_answer_key = need_key
    r.grade_ms = (time.time() - t_grade) * 1000
    r.total_ms = (time.time() - t0) * 1000
    return r


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════
async def main():
    qwen = QwenVLClient()
    ocr = AliyunOCRClient()
    sem = asyncio.Semaphore(QWEN_CONCURRENCY)

    # 合并所有14张
    all_samples = dict(SAMPLES)
    for i, f in enumerate(REGRESSION):
        name = f"reg_{os.path.basename(f)[:10]}"
        if f not in all_samples.values():
            all_samples[name] = f

    print(f"B++ 实验：{len(all_samples)} 张样本")
    print(f"timeout={QWEN_TIMEOUT}s qwen_tokens={QWEN_MAX_TOKENS} concurrency={QWEN_CONCURRENCY}")
    print("=" * 70)

    results = {}
    total_cost = 0
    for name, path in all_samples.items():
        if not os.path.exists(path):
            continue
        print(f"\n📷 {name[:20]}...", end=" ", flush=True)
        r = await run_bpp(name, path, qwen, ocr, sem)
        results[name] = r
        total_cost += r.cost_est

        # 单行摘要
        if r.quality_reject:
            print(f"🚫质量拒绝: {r.quality_reject} {r.total_ms:.0f}ms")
        elif r.negative_blocked:
            print(f"🚫非作业拦截 {r.total_ms:.0f}ms")
        else:
            over = "⚠️" if r.total_ms > 10000 else ""
            print(f"{r.total_ms:.0f}ms q={r.questions_count} a={r.child_answer_non_empty} "
                  f"g={r.is_correct_count} nk={r.need_answer_key} {over}")

    # ─── 汇总 ───
    ok = [r for r in results.values() if r.is_homework and not r.quality_reject and not r.negative_blocked]
    under10 = [r for r in ok if r.total_ms <= 10000]
    ans0 = [r for r in ok if r.questions_count > 0 and r.child_answer_non_empty == 0]
    rej = [r for r in results.values() if r.quality_reject]
    neg = [r for r in results.values() if r.negative_blocked]

    print(f"\n{'='*70}")
    print(f"📊 B++ 汇总 (总费用 ¥{total_cost:.4f})")
    print(f"  总样本={len(results)} 作业={len(ok)} 质量拒绝={len(rej)} 非作业拦截={len(neg)}")
    if ok:
        avg_t = sum(r.total_ms for r in ok)/len(ok)
        avg_q = sum(r.questions_count for r in ok)/len(ok)
        avg_a = sum(r.child_answer_non_empty for r in ok)/len(ok)
        avg_c = sum(r.is_correct_count for r in ok)/len(ok)
        cover_a = sum(r.child_answer_non_empty for r in ok)/max(1,sum(r.questions_count for r in ok))*100
        print(f"  ≤10s: {len(under10)}/{len(ok)}  平均: {avg_t:.0f}ms  q={avg_q:.0f}  a={avg_a:.0f}  g={avg_c:.0f}")
        print(f"  答案覆盖率: {cover_a:.0f}%  超时: {len(ok)-len(under10)}  假结果: {len(ans0)}")
        print(f"  {'✅ 达标' if len(under10)>=11 and cover_a>=85 and len(ans0)==0 and len(neg)==0 else '❌ 未达标'}")

if __name__ == "__main__":
    asyncio.run(main())
