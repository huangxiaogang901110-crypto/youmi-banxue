#!/usr/bin/env python3
"""
A/B/C 三方案对比实验 v2 — 管道格式 Qwen prompt
方案A：全图Qwen（管道格式，timeout=7s）
方案B：OCR版面→裁2大块→并发Qwen
方案C：OCR题号→题组裁小图→逐批Qwen
"""
import os, sys, time, json, re, asyncio, io
from pathlib import Path
from typing import Optional
from PIL import Image
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vision_client import QwenVLClient
from ocr_client import AliyunOCRClient

# ─── 配置 ──────────────────────────────────────────────
QWEN_TIMEOUT = 7
QWEN_MAX_TOKENS = 600
QWEN_CONCURRENCY = 2
COST_PER_1K_TOKENS = 0.003
MAX_BUDGET = 1.5
IMG_MAX_HEIGHT = 800
IMG_QUALITY = 65

SAMPLES = {
    "math":    "/tmp/yomi/samples/img_aebd4fe0604c.jpg",
    "chinese": "/tmp/yomi/samples/img_9d310897f49c.jpg",
    "english": "/tmp/yomi/samples/img_2602c37c11b0.jpg",
    "negative": "/tmp/yomi/samples/img_0ccb93bbe033.jpg",
}

# PIPE-FORMAT prompt — Qwen快速输出
QWEN_PROMPT_PIPE = (
    "图中每题输出一行: 题号|题目内容|孩子手写答案。"
    "孩子未写填'无'。不要标题/日期。不要解释。"
)

# JSON prompt (方案A备选，用于对比)
QWEN_PROMPT_JSON = (
    "识别图中所有题目。严格JSON数组："
    '[{"q":"题干","a":"孩子答案或null","bbox":[x,y,w,h],"abbox":[x,y,w,h]或null,'
    '"sub":"math/chinese/english","conf":0-1}]\n'
    "孩子未写填null。只输出JSON数组。"
)

@dataclass
class RunResult:
    plan: str
    subject: str
    total_ms: float = 0
    ocr_ms: float = 0
    qwen_ms: float = 0
    questions_count: int = 0
    child_answer_non_empty: int = 0
    answer_bbox_non_empty: int = 0
    is_correct_count: int = 0
    noise_count: int = 0
    negative_rejected: Optional[bool] = None
    cost_est: float = 0.0
    error: str = ""

# ─── 工具 ──────────────────────────────────────────────
def load_image(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()

def compress_image(img_bytes: bytes, max_height: int = IMG_MAX_HEIGHT, quality: int = IMG_QUALITY) -> bytes:
    img = Image.open(io.BytesIO(img_bytes))
    w, h = img.size
    if h > max_height:
        scale = max_height / h
        img = img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()

def crop_region(img_bytes: bytes, region: tuple) -> bytes:
    img = Image.open(io.BytesIO(img_bytes))
    x, y, w, h = region
    x, y = max(0, x), max(0, y)
    w = min(w, img.width - x)
    h = min(h, img.height - y)
    if w <= 0 or h <= 0:
        return img_bytes
    cropped = img.crop((x, y, x+w, y+h))
    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=75)
    return buf.getvalue()

def get_image_size(img_bytes: bytes) -> tuple:
    img = Image.open(io.BytesIO(img_bytes))
    return img.size

def parse_pipe_output(content: str) -> list:
    """解析管道格式: 题号|题目内容|孩子答案"""
    questions = []
    for line in content.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("```") or line.startswith("题号"):
            continue
        parts = line.split("|")
        if len(parts) >= 2:
            q = {"question_text": parts[0].strip() if len(parts) > 0 else "",
                 "child_answer": parts[-1].strip() if len(parts) > 1 else None}
            # 清理题号前缀
            q["question_text"] = re.sub(r'^\d+[.、．)）\s]*', '', q["question_text"]).strip()
            if q["child_answer"] and q["child_answer"] in ("无", "null", "none", "", "没有", "孩子未写"):
                q["child_answer"] = None
            questions.append(q)
    return questions

def parse_json_output(content: str) -> list:
    json_match = re.search(r'\[.*\]', content, re.DOTALL)
    if not json_match:
        return []
    try:
        return json.loads(json_match.group())
    except json.JSONDecodeError:
        return []

def count_child_answers(questions: list) -> int:
    return sum(1 for q in questions if q.get("child_answer") and
               str(q["child_answer"]).lower() not in ("null", "none", "无", ""))

def is_negative_sample(subject: str) -> bool:
    return subject == "negative"

# ─── 方案A：全图Qwen ───────────────────────────────────
async def plan_a(client: QwenVLClient, img_bytes: bytes, subject: str,
                 sem: asyncio.Semaphore, use_json: bool = False) -> RunResult:
    result = RunResult(plan="A", subject=subject)
    t0 = time.time()
    img = compress_image(img_bytes)
    prompt = QWEN_PROMPT_JSON if use_json else QWEN_PROMPT_PIPE
    mt = 1500 if use_json else QWEN_MAX_TOKENS

    loop = asyncio.get_event_loop()
    async with sem:
        r = await loop.run_in_executor(
            None, lambda: client._call(image_bytes=img, prompt=prompt,
                                        max_tokens=mt, timeout=QWEN_TIMEOUT))

    result.total_ms = (time.time() - t0) * 1000
    result.qwen_ms = r.get("latency_ms", 0)

    if not r["success"]:
        result.error = r.get("error", "qwen_timeout")[:40]
        if is_negative_sample(subject):
            result.negative_rejected = True  # 负样本超时=合理拒绝
        return result

    content = r["content"]
    questions = parse_json_output(content) if use_json else parse_pipe_output(content)

    result.questions_count = len(questions)
    result.child_answer_non_empty = count_child_answers(questions)
    usage = r.get("usage", {})
    total_tokens = usage.get("total_tokens", QWEN_MAX_TOKENS + 500)
    result.cost_est = total_tokens / 1000 * COST_PER_1K_TOKENS

    if is_negative_sample(subject):
        result.negative_rejected = (result.questions_count == 0)
    return result


# ─── 方案B：OCR→大块→并发Qwen ──────────────────────────
async def plan_b(client_qwen: QwenVLClient, client_ocr: AliyunOCRClient,
                 img_bytes: bytes, subject: str, sem: asyncio.Semaphore) -> RunResult:
    result = RunResult(plan="B", subject=subject)
    t0 = time.time()
    loop = asyncio.get_event_loop()

    # Step 1: OCR
    img = compress_image(img_bytes)
    ocr = await loop.run_in_executor(None, lambda: client_ocr.recognize(img))
    result.ocr_ms = ocr.get("latency_ms", 0)
    blocks = ocr.get("blocks", [])

    if not blocks:
        result.error = "ocr_no_blocks"
        result.total_ms = (time.time() - t0) * 1000
        if is_negative_sample(subject):
            result.negative_rejected = True
        return result

    # Step 2: 按y坐标分2大块
    img_w, img_h = get_image_size(img)
    y_vals = [b["y"] for b in blocks]
    mid_y = sorted(y_vals)[len(y_vals)//2] if len(y_vals) >= 8 else img_h//2

    overlap = int(img_h * 0.08)
    regions = [
        (0, 0, img_w, mid_y + overlap),
        (0, mid_y - overlap, img_w, img_h - mid_y + overlap),
    ]

    async def _call(region):
        crop = crop_region(img, region)
        async with sem:
            r = await loop.run_in_executor(
                None, lambda: client_qwen._call(
                    image_bytes=crop, prompt=QWEN_PROMPT_PIPE,
                    max_tokens=QWEN_MAX_TOKENS, timeout=QWEN_TIMEOUT))
        return r

    qwen_results = await asyncio.gather(*[_call(r) for r in regions], return_exceptions=True)

    all_questions = []
    total_qwen_ms = 0
    total_tokens = 0
    for r in qwen_results:
        if isinstance(r, Exception):
            continue
        if r.get("success"):
            all_questions.extend(parse_pipe_output(r.get("content", "")))
            total_qwen_ms = max(total_qwen_ms, r.get("latency_ms", 0))
            usage = r.get("usage", {})
            total_tokens += usage.get("total_tokens", 400)

    result.qwen_ms = total_qwen_ms
    result.total_ms = (time.time() - t0) * 1000
    result.questions_count = len(all_questions)
    result.child_answer_non_empty = count_child_answers(all_questions)
    result.cost_est = total_tokens / 1000 * COST_PER_1K_TOKENS

    if is_negative_sample(subject):
        result.negative_rejected = (result.questions_count <= 1)
    return result


# ─── 方案C：OCR→题组→逐批Qwen ──────────────────────────
async def plan_c(client_qwen: QwenVLClient, client_ocr: AliyunOCRClient,
                 img_bytes: bytes, subject: str, sem: asyncio.Semaphore) -> RunResult:
    result = RunResult(plan="C", subject=subject)
    t0 = time.time()
    loop = asyncio.get_event_loop()

    img = compress_image(img_bytes)
    ocr = await loop.run_in_executor(None, lambda: client_ocr.recognize(img))
    result.ocr_ms = ocr.get("latency_ms", 0)
    blocks = ocr.get("blocks", [])

    if not blocks:
        result.error = "ocr_no_blocks"
        result.total_ms = (time.time() - t0) * 1000
        return result

    # 找题号模式
    q_pat = re.compile(r'^\s*(\d{1,3})[.、．)）\s]')
    blocks_sorted = sorted(blocks, key=lambda b: (b["y"], b["x"]))

    markers = []
    for i, b in enumerate(blocks_sorted):
        m = q_pat.match(b["text"].strip())
        if m:
            markers.append({"num": int(m.group(1)), "y": b["y"], "x": b["x"], "h": b["h"]})

    # 去重相邻同号
    markers_dedup = []
    for m in markers:
        if not markers_dedup or m["num"] != markers_dedup[-1]["num"]:
            markers_dedup.append(m)

    if len(markers_dedup) < 3:
        # 无题号，按y等分3块
        img_w, img_h = get_image_size(img)
        chunk_h = img_h // 3
        groups = [(0, i*chunk_h, img_w, chunk_h + int(img_h*0.06)) for i in range(3)]
    else:
        # 按5-8题分组
        img_w, img_h = get_image_size(img)
        groups = []
        batch_size = 6
        for g_start in range(0, len(markers_dedup), batch_size):
            g_end = min(g_start + batch_size, len(markers_dedup))
            g_markers = markers_dedup[g_start:g_end]
            y_min = max(0, g_markers[0]["y"] - 8)
            y_max = min(img_h, (markers_dedup[g_end]["y"] - 8) if g_end < len(markers_dedup)
                        else g_markers[-1]["y"] + g_markers[-1]["h"] + 60)
            groups.append((0, y_min, img_w, y_max - y_min))

    if not groups:
        result.error = "no_groups"
        result.total_ms = (time.time() - t0) * 1000
        return result

    all_questions = []
    total_qwen_ms = 0
    total_tokens = 0

    for region in groups:
        crop = crop_region(img, region)
        # 跳过太小的区域
        cw, ch = get_image_size(crop)
        if ch < 20:
            continue

        async with sem:
            r = await loop.run_in_executor(
                None, lambda: client_qwen._call(
                    image_bytes=crop, prompt=QWEN_PROMPT_PIPE,
                    max_tokens=500, timeout=QWEN_TIMEOUT))

        if r.get("success"):
            qs = parse_pipe_output(r.get("content", ""))
            all_questions.extend(qs)
            total_qwen_ms += r.get("latency_ms", 0)
            usage = r.get("usage", {})
            total_tokens += usage.get("total_tokens", 300)

    result.qwen_ms = total_qwen_ms
    result.total_ms = (time.time() - t0) * 1000
    result.questions_count = len(all_questions)
    result.child_answer_non_empty = count_child_answers(all_questions)
    result.cost_est = total_tokens / 1000 * COST_PER_1K_TOKENS

    if is_negative_sample(subject):
        result.negative_rejected = (result.questions_count <= 1)
    return result


# ─── 主流程 ────────────────────────────────────────────
async def main():
    qwen = QwenVLClient()
    ocr = AliyunOCRClient()
    sem = asyncio.Semaphore(QWEN_CONCURRENCY)

    print("=" * 70)
    print("A/B/C 三方案对比实验 v2 — 管道格式 prompt")
    print(f"timeout={QWEN_TIMEOUT}s mt={QWEN_MAX_TOKENS} concurrency={QWEN_CONCURRENCY}")
    print("=" * 70)

    all_results = []
    total_cost = 0.0

    for subject, path in SAMPLES.items():
        if not os.path.exists(path):
            continue
        img_bytes = load_image(path)
        c = compress_image(img_bytes)
        print(f"\n{'─'*55}")
        print(f"📷 {subject}: {len(img_bytes)}B → {len(c)}B")

        # 方案A
        try:
            ra = await plan_a(qwen, img_bytes, subject, sem)
            all_results.append(ra); total_cost += ra.cost_est
            status = "✅" if not ra.error else f"⏱️超时"
            print(f"  A: q={ra.questions_count} ans={ra.child_answer_non_empty} "
                  f"qwen={ra.qwen_ms:.0f}ms total={ra.total_ms:.0f}ms ¥{ra.cost_est:.4f} {status}")
        except Exception as e:
            print(f"  A: ❌ {e}")

        if total_cost > MAX_BUDGET * 0.75:
            print(f"  ⚠️ 费用 ¥{total_cost:.2f}/{MAX_BUDGET}，跳过后续"); continue

        # 方案B
        try:
            rb = await plan_b(qwen, ocr, img_bytes, subject, sem)
            all_results.append(rb); total_cost += rb.cost_est
            neg = f" 拒✅" if rb.negative_rejected else (" 漏❌" if rb.negative_rejected is False else "")
            print(f"  B: q={rb.questions_count} ans={rb.child_answer_non_empty} "
                  f"ocr={rb.ocr_ms:.0f}ms qwen={rb.qwen_ms:.0f}ms total={rb.total_ms:.0f}ms "
                  f"¥{rb.cost_est:.4f} {'⏱️' if rb.error else '✅'}{neg}")
        except Exception as e:
            print(f"  B: ❌ {e}")

        if total_cost > MAX_BUDGET * 0.75:
            print(f"  ⚠️ 费用 ¥{total_cost:.2f}/{MAX_BUDGET}，跳过后续"); continue

        # 方案C
        try:
            rc = await plan_c(qwen, ocr, img_bytes, subject, sem)
            all_results.append(rc); total_cost += rc.cost_est
            neg = f" 拒✅" if rc.negative_rejected else (" 漏❌" if rc.negative_rejected is False else "")
            print(f"  C: q={rc.questions_count} ans={rc.child_answer_non_empty} "
                  f"ocr={rc.ocr_ms:.0f}ms qwen={rc.qwen_ms:.0f}ms total={rc.total_ms:.0f}ms "
                  f"¥{rc.cost_est:.4f} {'⏱️' if rc.error else '✅'}{neg}")
        except Exception as e:
            print(f"  C: ❌ {e}")

    # ─── 汇总 ─────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"📊 汇总矩阵 (总费用 ¥{total_cost:.4f})")
    print(f"{'='*70}")

    for plan_name in ["A", "B", "C"]:
        prs = [r for r in all_results if r.plan == plan_name]
        if not prs:
            continue
        print(f"\n## 方案{plan_name}")
        print(f"{'学科':<10} {'总耗时':>7} {'Qwen':>7} {'OCR':>7} {'题数':>5} "
              f"{'答案':>4} {'费用':>7} {'负样本':>6}")
        print("-" * 65)
        for r in prs:
            neg_s = ""
            if r.negative_rejected is True: neg_s = "✓拒"
            elif r.negative_rejected is False: neg_s = "✗漏"
            status = "⏱️" if r.error else "✅"
            print(f"{r.subject:<10} {r.total_ms:>6.0f}ms {r.qwen_ms:>6.0f}ms {r.ocr_ms:>6.0f}ms "
                  f"{r.questions_count:>4} {r.child_answer_non_empty:>3} ¥{r.cost_est:>6.4f} {status} {neg_s}")

        ok = [r for r in prs if not r.error]
        if ok:
            avg_t = sum(r.total_ms for r in ok)/len(ok)
            avg_q = sum(r.questions_count for r in ok)/len(ok)
            avg_a = sum(r.child_answer_non_empty for r in ok)/len(ok)
            print(f"{'[平均]':<10} {avg_t:>6.0f}ms {'':>7} {'':>7} {avg_q:>4.0f} {avg_a:>3.0f}")

    print(f"\n{'='*70}")
    print("✅ 实验完成")

if __name__ == "__main__":
    asyncio.run(main())
