"""
悠米 v5 — 作业识别主链路测试
上传 → poll → questions → grading → tutor(step) → tutor(solve)
"""
import os, sys, struct, time, asyncio, sqlite3, base64
import pytest
import httpx

# ═══ 配置 ═══
BASE       = "http://localhost:8001"
TEST_PHONE = "13900001111"
TEST_PWD   = "123456"
FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
SAMPLE_PATH = os.path.join(FIXTURE_DIR, "sample.bmp")

MAX_POLL_SEC = 120
POLL_INTERVAL = 2.5

# ═══ 生成测试图片 (纯 Python，不依赖 PIL) ═══
def _make_sample_bmp():
    """生成 200x60 白色 BMP，画 '12+34=?' 黑字"""
    W, H = 200, 60
    row_bytes = (W * 3 + 3) & ~3
    pixels = bytearray(b'\xff' * row_bytes * H)  # 全白

    font = {
        '1': [[0,1,0],[1,1,0],[0,1,0],[0,1,0],[0,1,0]],
        '2': [[1,1,1],[0,0,1],[1,1,1],[1,0,0],[1,1,1]],
        '3': [[1,1,1],[0,0,1],[1,1,1],[0,0,1],[1,1,1]],
        '4': [[1,0,1],[1,0,1],[1,1,1],[0,0,1],[0,0,1]],
        '+': [[0,0,0],[0,1,0],[1,1,1],[0,1,0],[0,0,0]],
        '=': [[0,0,0],[1,1,1],[0,0,0],[1,1,1],[0,0,0]],
        '?': [[1,1,1],[0,0,1],[0,1,1],[0,0,0],[0,1,0]],
    }

    def draw_char(ch, cx, cy, scale=2):
        if ch not in font: return
        for ri, row in enumerate(font[ch]):
            for ci, val in enumerate(row):
                if val:
                    for sy in range(scale):
                        for sx in range(scale):
                            px, py = cx + ci*scale + sx, cy + ri*scale + sy
                            if 0 <= px < W and 0 <= py < H:
                                off = py * row_bytes + px * 3
                                pixels[off:off+3] = b'\x00\x00\x00'

    text = "12+34=?"
    cw = 12
    sx = (W - len(text) * cw) // 2
    sy = (H - 14) // 2
    for i, ch in enumerate(text):
        draw_char(ch, sx + i * cw, sy)

    file_size = 14 + 40 + len(pixels)
    buf = bytearray()
    buf += struct.pack('<2sIHHI', b'BM', file_size, 0, 0, 54)
    buf += struct.pack('<IiiHHIIiiII', 40, W, H, 1, 24, 0, len(pixels), 0, 0, 0, 0)
    buf += pixels
    return bytes(buf)


def _ensure_sample():
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    if not os.path.exists(SAMPLE_PATH):
        with open(SAMPLE_PATH, 'wb') as f:
            f.write(_make_sample_bmp())


# ═══ Fixtures ═══
@pytest.fixture
async def client():
    async with httpx.AsyncClient(proxy=None, trust_env=False, timeout=15.0) as c:
        yield c


@pytest.fixture
async def token(client):
    r = await client.post(f"{BASE}/api/auth/login", json={
        "phone": TEST_PHONE, "password": TEST_PWD
    })
    assert r.json().get("ok") is True, f"登录失败: {r.json()}"
    return r.json()["data"]["token"]


@pytest.fixture
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# ═══ 主链路测试 ═══
@pytest.mark.asyncio
async def test_homework_upload_flow(client, auth_headers):
    """作业识别主链路：上传 → poll → questions → grading → tutor step → tutor solve"""
    _ensure_sample()

    # ── 1. 上传 ──
    with open(SAMPLE_PATH, "rb") as f:
        r = await client.post(
            f"{BASE}/api/parse-jobs",
            files={"file": ("sample.bmp", f, "image/bmp")},
            data={"source_type": "web_upload"},
            headers=auth_headers,
        )
    assert r.status_code == 200, f"上传失败: {r.status_code}"
    data = r.json()
    assert data.get("ok"), f"上传返回错误: {data}"
    job_id = data["data"]["job_id"]
    status = data["data"]["status"]
    assert job_id, "缺少 job_id"
    print(f"\n  📤 上传成功 job_id={job_id} status={status}")

    # ── 2. 轮询 ──
    elapsed = 0.0
    recovered = False
    while elapsed < MAX_POLL_SEC:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        r = await client.get(f"{BASE}/api/parse-jobs/{job_id}", headers=auth_headers)
        if r.status_code == 404:
            # job 可能过期，尝试 recover
            if not recovered:
                print(f"\n  🔄 尝试 recover...")
                rr = await client.get(f"{BASE}/api/parse-jobs/{job_id}/recover", headers=auth_headers)
                if rr.status_code == 200 and rr.json().get("ok"):
                    recovered = True
                    print(f"  ✅ recover 成功，继续 poll")
                    await asyncio.sleep(2)
                    continue
            print(f"\n  ❌ recover 失败或 job 不存在")
            pytest.fail("upload_poll_recover")
        jd = r.json()
        if not jd.get("ok"):
            continue
        js = jd["data"].get("status", "")
        if js == "completed":
            print(f"  ✅ job completed ({elapsed:.0f}s)")
            break
        if js in ("failed",):
            print(f"  ❌ job failed: {jd['data'].get('error_code','?')}")
            pytest.fail("upload_poll")
    else:
        # 超时 → 尝试 recover 后继续 poll
        if not recovered:
            print(f"\n  ⏱ 超时({MAX_POLL_SEC}s)，尝试 recover...")
            rr = await client.get(f"{BASE}/api/parse-jobs/{job_id}/recover", headers=auth_headers)
            if rr.status_code == 200 and rr.json().get("ok"):
                recovered = True
                for _ in range(15):
                    await asyncio.sleep(POLL_INTERVAL)
                    r = await client.get(f"{BASE}/api/parse-jobs/{job_id}", headers=auth_headers)
                    jd = r.json()
                    if jd.get("ok") and jd["data"].get("status") == "completed":
                        print(f"  ✅ recover 后 completed")
                        break
                else:
                    print(f"  ❌ recover 后仍超时")
                    pytest.fail("upload_poll_recover")
            else:
                pytest.fail("upload_poll_recover")

    # ── 3. 题目列表 ──
    r = await client.get(f"{BASE}/api/parse-jobs/{job_id}/questions", headers=auth_headers)
    assert r.status_code == 200
    qdata = r.json()
    assert qdata.get("ok"), f"获取题目失败: {qdata}"
    questions = qdata["data"]
    qcount = len(questions)
    assert qcount > 0, "题目数为 0"
    print(f"  📋 题目数: {qcount}")

    # ── 4. 判对错字段检查 ──
    with_child = sum(1 for q in questions if q.get("student_answer"))
    with_grading = sum(1 for q in questions if q.get("is_correct") is not None)
    print(f"  📊 有 student_answer: {with_child}/{qcount}  有 is_correct: {with_grading}/{qcount}")

    first_q = questions[0]
    qid = first_q["question_id"]
    assert qid, "question_id 缺失"
    assert first_q.get("question_text"), "question_text 缺失"
    assert "student_answer" in first_q, "student_answer 字段缺失"
    assert "is_correct" in first_q, "is_correct 字段缺失"
    if with_grading == 0:
        print(f"  ⚠️ is_correct 全为 null（可能 Qwen 未识别出题目内容），字段存在但判对错跳过")
    print(f"  ✅ 判对错字段存在")

    # ── 5. 分步解析 (action=step) ──
    r = await client.post(
        f"{BASE}/api/questions/{qid}/tutor",
        json={"mode": "initial", "message": "请分步讲解", "action": "step"},
        headers=auth_headers,
    )
    tr = r.json()
    step_ok = tr.get("ok") and len(tr.get("data", {}).get("reply_text", "")) > 0
    print(f"  {'✅' if step_ok else '❌'} 分步解析: reply_text={'非空' if step_ok else '空/失败'}")
    if not step_ok:
        print(f"     detail: {tr}")

    # ── 6. 完整解析 (action=solve) ──
    r = await client.post(
        f"{BASE}/api/questions/{qid}/tutor",
        json={"mode": "followup", "message": "给完整解答", "action": "solve"},
        headers=auth_headers,
    )
    tr2 = r.json()
    full_ok = tr2.get("ok") and len(tr2.get("data", {}).get("reply_text", "")) > 0
    print(f"  {'✅' if full_ok else '❌'} 完整解析: reply_text={'非空' if full_ok else '空/失败'}")
    if not full_ok:
        print(f"     detail: {tr2}")

    # ── 7. 摘要 ──
    print(f"\n  ┌──────────────────────────────────┐")
    print(f"  │ job_id:      {job_id}")
    print(f"  │ status:      completed")
    print(f"  │ qcount:      {qcount}")
    print(f"  │ with_answer: {with_child}")
    print(f"  │ with_grading:{with_grading}")
    print(f"  │ step_ok:     {step_ok}")
    print(f"  │ full_ok:     {full_ok}")
    print(f"  │ recovered:   {recovered}")
    print(f"  └──────────────────────────────────┘")

    # 不强制 grading/tutor 全过（受 Qwen 识别质量影响），但要跑完
    assert step_ok or full_ok, "分步和完整解析均失败"
