"""
管线核心函数。
从 main.py 抽出，不改行为。
"""
import asyncio
import json
import re
import time

import db as _db
from models import ParseJob, Question, JobStatus, QuestionStatus
from job_store import _jobs, _model_calls, _deferred_vision_tasks, _ts
from logger import info, warning, error, debug
from logger import info as _log_info, warning as _log_warn, error as _log_error
from model_logger import make_log_entry
from db import get_active_pricing
from deepseek_client import DeepSeekClient
from vision_client import QwenVLClient
from ocr_client import AliyunOCRClient
from question_cutter import cut_to_questions, cut_block_to_questions
from block_detector import detect_blocks
from answer_detector import detect_all_answer_bboxes
import oss_client as _oss


# ═══════════════════════════════════════════════════════════════

def enqueue_parse_job(jid: str, job_entry: dict):
    """将任务注册到内存队列 + SQLite 持久化。"""
    _jobs[jid] = job_entry
    _db.save_job(jid, job_entry)


def save_result(jid: str, questions: list, now: str, file, status: JobStatus):
    """保存任务最终结果 — SQLite 持久化。先写 questions 再标 completed，防 poll 读到 qcount=0。"""
    # ⚠️ 顺序：先写 questions 再设 status，防止 completed 和 questions 之间的竞态窗口
    _jobs[jid]["questions"] = questions
    _jobs[jid]["job"] = ParseJob(
        job_id=jid, status=status,
        questions_count=len(questions),
        created_at=now, updated_at=_ts(), file_name=file.filename if file else "",
    )
    # 确保 poll_count 存在（旧 save_result 调用后可能丢失）
    if "poll_count" not in _jobs[jid]:
        _jobs[jid]["poll_count"] = 0
    # 持久化：把 Pydantic 对象序列化
    _db.save_job(jid, {
        "job_id": jid,
        "status": status.value,
        "questions_count": len(questions),
        "created_at": now,
        "updated_at": _ts(),
        "file_name": file.filename if file else "",
        "questions": [q.model_dump() for q in questions],
        "poll_count": _jobs[jid].get("poll_count", 0),
        "child_id": _jobs[jid].get("child_id", ""),
        "parent_id": _jobs[jid].get("parent_id", ""),
        "client_task_id": _jobs[jid].get("client_task_id", ""),
        "progress": _jobs[jid].get("progress", ""),
        "error_code": _jobs[jid].get("error_code", ""),
        "retry_count": _jobs[jid].get("retry_count", 0),
        "completed_at": _ts() if status.value in ("completed", "completed_with_failures") else "",
    })
    # 同步更新 parse_jobs 表（跨重启查询用）
    child_id = _jobs[jid].get("child_id", "")
    parent_id = _jobs[jid].get("parent_id", "")
    file_name = file.filename if file else ""
    progress = _jobs[jid].get("progress", "")
    error_code = _jobs[jid].get("error_code", "")
    retry_count = _jobs[jid].get("retry_count", 0)
    completed_at = _ts() if status.value in ("completed", "completed_with_failures", "failed") else ""
    parse_mode = _jobs[jid].get("parse_mode", "")
    parser_provider = _jobs[jid].get("parser_provider", "")
    parser_model = _jobs[jid].get("parser_model", "")
    qwen_parse_call_id = _jobs[jid].get("qwen_parse_call_id", "")
    total_parse_cost_cny = _jobs[jid].get("total_parse_cost_cny", 0.0)
    _db.save_parse_job(jid, child_id, parent_id, file_name, len(questions), status, now,
                        progress=progress, error_code=error_code, retry_count=retry_count, completed_at=completed_at,
                        parse_mode=parse_mode, parser_provider=parser_provider, parser_model=parser_model,
                        qwen_parse_call_id=qwen_parse_call_id, total_parse_cost_cny=total_parse_cost_cny,
                        data_json=json.dumps({
                            "job_id": jid, "status": status.value, "questions_count": len(questions),
                            "created_at": now, "updated_at": _ts(), "file_name": file.filename if file else "",
                            "questions": [q.model_dump() for q in questions],
                            "poll_count": _jobs[jid].get("poll_count", 0),
                            "child_id": child_id, "parent_id": parent_id,
                            "client_task_id": _jobs[jid].get("client_task_id", ""),
                            "progress": progress, "error_code": error_code,
                            "retry_count": retry_count, "completed_at": completed_at,
                        }, default=str),
                        client_upload_id=_jobs[jid].get("client_upload_id", _jobs[jid].get("client_task_id", "")))


async def grade_answers(jid: str, questions: list, trace_id: str, parent_id: str, child_id: str) -> float:
    """DeepSeek 批量判对错。返回 grading 总成本 CNY。
    失败时所有题 is_correct=null, grading_explanation='暂未判定'。
    数学口算/加减乘除/比大小/填空优先规则判题，减少 DeepSeek 耗时。
    """
    # ── Phase A: 三科规则快速判题 ──
    from math_grader import _try_math_rule_grading
    from chinese_grader import _try_chinese_rule_grading
    from english_grader import _try_english_rule_grading
    _rule_graded = 0
    for q in questions:
        _sa = q.student_answer if hasattr(q, "student_answer") else q.get("student_answer") if isinstance(q, dict) else None
        _qt = q.question_text if hasattr(q, "question_text") else q.get("question_text", "") if isinstance(q, dict) else ""
        if _sa and _qt:
            _result = _try_math_rule_grading(_qt, str(_sa))
            if _result is not None:
                if hasattr(q, "is_correct"):
                    q.is_correct = _result["is_correct"]
                    q.grading_explanation = _result["explanation"]
                else:
                    q["is_correct"] = _result["is_correct"]
                    q["grading_explanation"] = _result["explanation"]
                _rule_graded += 1
    if _rule_graded > 0:
        info(f"[BG] Math rule graded {_rule_graded}/{len(questions)} questions for {jid}")

    # ── Phase B: 语文 + 英语规则判题 ──
    _ch_en_graded = 0
    for q in questions:
        _sa = q.student_answer if hasattr(q, "student_answer") else q.get("student_answer") if isinstance(q, dict) else None
        _qt = q.question_text if hasattr(q, "question_text") else q.get("question_text", "") if isinstance(q, dict) else ""
        _qtype = q.question_type if hasattr(q, "question_type") else q.get("question_type", "") if isinstance(q, dict) else ""
        _sa_std = q.standard_answer if hasattr(q, "standard_answer") else q.get("standard_answer") if isinstance(q, dict) else ""
        if _sa and _qt:
            _result = None
            if _qtype in ("填空", "选择", "看拼音"):
                _result = _try_chinese_rule_grading(_qt, str(_sa), _qtype, str(_sa_std or ""))
            elif _qtype in ("选择", "填空", "单词"):
                _result = _try_english_rule_grading(_qt, str(_sa), _qtype, str(_sa_std or ""))
            if _result is not None:
                if hasattr(q, "is_correct"):
                    q.is_correct = _result["is_correct"]
                    q.grading_explanation = _result["explanation"]
                else:
                    q["is_correct"] = _result["is_correct"]
                    q["grading_explanation"] = _result["explanation"]
                _ch_en_graded += 1
    if _ch_en_graded > 0:
        info(f"[BG] CN/EN rule graded {_ch_en_graded}/{len(questions)} questions for {jid}")

    # 只对有 student_answer 且未被规则判题的题走 DeepSeek
    gradable = [(i, q) for i, q in enumerate(questions)
                 if (hasattr(q, "student_answer") and q.student_answer or
                     isinstance(q, dict) and q.get("student_answer"))
                 and (getattr(q, "is_correct", None) is None if hasattr(q, "is_correct") else
                      (q.get("is_correct") is None if isinstance(q, dict) else True))]
    with open("/tmp/grade_diag.log", "a") as _f:
        _f.write(f"{_ts()} | grade_answers START jid={jid} total_q={len(questions)} gradable={len(gradable)}\n")
    if not gradable:
        return 0.0

    # 构建批量判题 prompt
    items = []
    for idx, q in gradable:
        _qt = q.question_text if hasattr(q, "question_text") else q.get("question_text", "")
        _sa = q.student_answer if hasattr(q, "student_answer") else q.get("student_answer", "")
        _typ = q.get("type", "") if isinstance(q, dict) else getattr(q, "_type", "")
        items.append({
            "index": idx,
            "number": q.question_number if hasattr(q, "question_number") else q.get("question_number", 0),
            "type": _typ,
            "content": _qt[:200],
            "student_answer": str(_sa)[:200],
        })

    prompt = (
        "你是小学/初中作业批改老师。下面是一个作业的题目和孩子写的答案，请逐题判对错。\n"
        "输出 JSON 数组格式：[{\"index\":序号,\"number\":题号,\"is_correct\":true|false|null,\"grading_explanation\":\"简短解释\"}]\n"
        "如果无法确定对错（答案模糊/不完整），is_correct 填 null。解释要短，面向家长和孩子。\n"
        f"题目列表：\n{json.dumps(items, ensure_ascii=False, default=str)}\n"
        "只输出 JSON 数组，不要其他文字。"
    )

    ds = DeepSeekClient()
    grading_cost = 0.0
    try:
        _log_info(f"grading_start jid={jid} count={len(gradable)}", trace_id=trace_id, parent_id=parent_id, child_id=child_id)
        t_start = time.time()
        result = ds.tutor([
            {"role": "system", "content": "你是作业批改老师，输出纯 JSON 数组。"},
            {"role": "user", "content": prompt},
        ], max_tokens=min(4096, max(1024, 80 * len(gradable))))
        latency_ms = int((time.time() - t_start) * 1000)
        with open("/tmp/grade_diag.log", "a") as _f:
            _f.write(f"{_ts()} | grade_answers API result success={result.get('success')} reply_len={len(result.get('reply_text',''))} latency_ms={latency_ms}\n")
            _f.write(f"{_ts()} | grade_answers REPLY content={result.get('reply_text','')[:500]}\n")

        # 记录模型调用
        pricing = get_active_pricing("deepseek", "deepseek-v4-flash")
        usage = result.get("usage", {}) or {}
        glog = make_log_entry(
            task_id=jid, provider_name="deepseek", model_name="deepseek-v4-flash",
            feature_code="deepseek_grade_answers", trace_id=trace_id,
            sub_stage="grading", latency_ms=latency_ms,
            success=result["success"],
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_hit_tokens=usage.get("cache_hit_tokens", 0),
            cache_miss_tokens=usage.get("cache_miss_tokens", 0),
            parent_user_id=parent_id, child_id=child_id,
            error_code=result.get("error"),
            billing_status="free_tier" if result["success"] else "failed",
            pricing=pricing, question_count=len(gradable),
        )
        grading_cost = glog.get("cost_cny", 0.0)
        _model_calls.append(glog)
        _db.save_model_call(glog)

        if not result["success"]:
            error(f"[BG] Grading failed for {jid}: {result.get('error', 'unknown')}")
            return grading_cost

        # 解析 DeepSeek 返回的 JSON
        content = result["reply_text"]
        # 写入完整回复供排查
        with open(f"/tmp/grade_reply_{jid}.txt", "w") as _f:
            _f.write(content)
        # 直接解析整个回复为 JSON
        parse_method = "direct_json"
        try:
            grades = json.loads(content)
        except json.JSONDecodeError:
            import re as _re
            json_match = _re.search(r'\[.*\]', content, _re.DOTALL)
            if json_match:
                grades = json.loads(json_match.group())
                parse_method = "regex"
            else:
                # 截断兜底：切到最后一个完整 JSON 对象并补 ]（常见于 max_tokens 截断）
                try:
                    _trimmed = content.rstrip()
                    _last_brace = _trimmed.rfind("}")
                    if _last_brace > 0:
                        grades = json.loads(_trimmed[:_last_brace + 1] + "]")
                        parse_method = "truncation_fix"
                    else:
                        grades = []
                        parse_method = "regex"
                except json.JSONDecodeError:
                    grades = []
                    parse_method = "regex"
        if not isinstance(grades, list):
            grades = []
        with open("/tmp/grade_diag.log", "a") as _f:
            _f.write(f"{_ts()} | grade_answers PARSE method={parse_method} grades_len={len(grades)}\n")

        # 回填 by index
        grade_map = {}
        for g in grades:
            if isinstance(g, dict) and "index" in g:
                grade_map[g["index"]] = g

        for idx, q in enumerate(questions):
            g = grade_map.get(idx)
            if g:
                _is_correct = g.get("is_correct")
                if _is_correct not in (True, False):
                    _is_correct = None
                _expl = g.get("grading_explanation", "暂未判定") if g.get("grading_explanation") else "暂未判定"
            else:
                # 无 student_answer 或 grading 未覆盖
                _sa2 = q.student_answer if hasattr(q, "student_answer") else q.get("student_answer") if isinstance(q, dict) else None
                if _sa2:
                    _is_correct = None
                    _expl = "暂未判定"
                else:
                    _is_correct = None
                    _expl = ""
            if hasattr(q, "is_correct"):
                q.is_correct = _is_correct
                q.grading_explanation = _expl
            else:
                q["is_correct"] = _is_correct
                q["grading_explanation"] = _expl

        # 合规校验：翻转数学误判
        from grade_compliance import check_compliance
        cr = check_compliance(questions)
        if cr["flipped"] > 0:
            with open("/tmp/grade_diag.log", "a") as _f:
                _f.write(f"{_ts()} | grade_answers COMPLIANCE flipped={cr['flipped']} details={cr['details']}\n")

        info(f"[BG] Grading OK for {jid}: {len(grades)}/{len(gradable)} graded")
        with open("/tmp/grade_diag.log", "a") as _f:
            _f.write(f"{_ts()} | grade_answers BACKFILL grade_map_keys={sorted(grade_map.keys())[:10]} backfill_done\n")
        # 诊断：判对错统计
        _correct = sum(1 for g in grade_map.values() if g.get("is_correct") is True)
        _wrong = sum(1 for g in grade_map.values() if g.get("is_correct") is False)
        _sa_count = len(gradable)
        debug("[diag] grading_completed jid={jid} total={len(questions)} graded={len(grades)} correct={_correct} wrong={_wrong} has_child_answer={_sa_count}")
    except Exception as e:
        error(f"[BG] Grading exception for {jid}: {e}")
        _log_error(f"grading_failed jid={jid}: {e}", trace_id=trace_id)
        with open("/tmp/grade_diag.log", "a") as _f:
            import traceback
            _f.write(f"{_ts()} | grade_answers EXCEPTION jid={jid}: {e}\n{traceback.format_exc()}\n")
    return grading_cost


# ─── OCR blocks 作业图预筛（Repair-4 负样本收口）────────────

_HW_KEYWORDS = re.compile(r'计算|口算|填一填|比大小|竖式|直接写出|列竖式|乘法口诀|不退位|进位|退位|'
                           r'得数|算式|写出|算一算|练一练|课时|任务[一二三四五]')
_NON_HW_KEYWORDS = re.compile(r'登录|密码|验证码|隐私|协议|注册|设置中心|个人中心|退出登录|'
                               r'欢迎回来|营养成分|配料|保质期|生产日期|食品|克[)\s]|毫升|净含量|'
                               r'¥|元[)\s]|小票|收银|快递|面单|条形码')
_ARITH_PAT = re.compile(r'\d+\s*[+\-×÷]\s*\d+')
_QNUM_PAT = re.compile(r'^\s*\d+[.、．)]')


def _is_homework_image(ocr_blocks: list) -> bool:
    """保守判断 OCR blocks 是否像数学作业图。
    宁可漏判（→needs_review）不可误判（→low_confidence）。
    """
    texts = [b.get("text", "") for b in ocr_blocks]
    all_text = " ".join(texts)

    # 1. 显式非作业关键词 → 直接否定
    if _NON_HW_KEYWORDS.search(all_text):
        return False

    # 2. 作业关键词 → 强信号
    if _HW_KEYWORDS.search(all_text):
        return True

    # 3. 计数算术表达式（含等号）
    arith_count = sum(1 for t in texts if _ARITH_PAT.search(t))
    eq_count = sum(1 for t in texts if "=" in t)

    # 4. 计题号
    qnum_count = sum(1 for t in texts if _QNUM_PAT.match(t))

    # 5. 综合判断
    if arith_count >= 5 and eq_count >= 3:
        return True
    if qnum_count >= 5 and arith_count >= 2:
        return True
    if arith_count >= 8:
        return True
    if qnum_count >= 8:
        return True

    # 边缘：有少量算数但无结构 → 可能是 UI 中的数字
    return False


async def worker_process_job(jid: str, contents: bytes, file, now: str, parent_id: str, child_id: str):
    """后台异步执行：Qwen-VL 全图识题优先 → OCR+切题回落 → Schema 校验 → 保存"""
    import os as _os_env
    SLA_ENABLED = _os_env.getenv("YOMI_RECOGNITION_10S_SLA", "true").lower() in ("1", "true", "yes")
    SLA_DEADLINE_S = int(_os_env.getenv("YOMI_SYNC_JOB_TIMEOUT_SECONDS", "10"))
    QWEN_FULL_TIMEOUT_S = int(_os_env.getenv("YOMI_QWEN_FULL_TIMEOUT_SECONDS",
                              _os_env.getenv("YOMI_QWEN_FULL_TIMEOUT_SECONDS", "9")))
    OCR_TIMEOUT_S = int(_os_env.getenv("YOMI_GENERAL_OCR_TIMEOUT_SECONDS",
                        _os_env.getenv("YOMI_GENERAL_OCR_TIMEOUT_SECONDS", "3")))
    DISABLE_PER_Q_VISION = _os_env.getenv("YOMI_DISABLE_SYNC_PER_QUESTION_VISION", "true").lower() in ("1", "true", "yes")
    PER_Q_VISION_LIMIT = int(_os_env.getenv("YOMI_PER_QUESTION_VISION_SYNC_LIMIT", "0"))
    BPLUS_ENABLED = _os_env.getenv("YOMI_BPLUS_RECOGNITION", "false").lower() in ("1", "true", "yes")
    BPLUSPLUS_ENABLED = _os_env.getenv("YOMI_BPLUSPLUS_RECOGNITION", "false").lower() in ("1", "true", "yes")
    import uuid as _uuid
    trace_id = _uuid.uuid4().hex
    _log_info(f"job_start jid={jid}", trace_id=trace_id, parent_id=parent_id, child_id=child_id)
    t_start = time.time()

    # ── Phase 0: 图片质量检查（过暗/模糊/非作业 → 快速拒绝）──
    try:
        from PIL import Image as _QImg, ImageStat as _QStat, ImageFilter as _QFlt
        import io as _QIo
        _qimg = _QImg.open(_QIo.BytesIO(contents)).convert("L")
        _qstat = _QStat.Stat(_qimg)
        _bright = _qstat.mean[0]
        _lap = _qimg.filter(_QFlt.Kernel((3, 3), [-1, -1, -1, -1, 8, -1, -1, -1, -1], 1, 128))
        _lapvar = _QStat.Stat(_lap).var[0]
        _iw, _ih = _qimg.size
        _qimg.close()
        if _bright < 80:
            _jobs[jid]["job"].status = JobStatus.rejected
            _jobs[jid]["job"].reason_code = "too_dark"
            _jobs[jid]["questions"] = []
            save_result(jid, [], now, file, JobStatus.rejected)
            info(f"[BG] Phase0 reject: too_dark (brightness={_bright:.0f}) for {jid}")
            return
        if _lapvar < 50:
            _jobs[jid]["job"].status = JobStatus.rejected
            _jobs[jid]["job"].reason_code = "too_blurry"
            _jobs[jid]["questions"] = []
            save_result(jid, [], now, file, JobStatus.rejected)
            info(f"[BG] Phase0 reject: too_blurry (laplace_var={_lapvar:.0f}) for {jid}")
            return
    except Exception as _qe:
        info(f"[BG] Phase0 quality check skipped: {_qe}")

    # 持久化原始图片供 vision 二次路由使用
    import os as _osp
    _osp.makedirs("/tmp/yomi", exist_ok=True)
    with open(f"/tmp/yomi/{jid}.jpg", "wb") as _pf:
        _pf.write(contents)

    try:
        # 上传到 OSS（异步，不阻塞主流程）
        oss_key = _oss.upload_image(contents, jid)
    except Exception as _e:
        error(f"[BG] OSS upload error: {_e}")
        oss_key = None

    # register_image 可能因 DB schema 问题失败，不阻塞主流程
    try:
        _db.register_image(jid, f"/tmp/yomi/{jid}.jpg", now, oss_key or "")
    except Exception as _e:
        error(f"[BG] register_image failed (non-blocking): {_e}")

    if oss_key:
        _jobs[jid]["oss_key"] = oss_key
        info(f"[BG] OSS upload OK: {oss_key}")
        # 生成 OSS 签名 URL（24h 有效），Qwen-VL 通过 URL 读取图片（不传 base64）
        oss_signed_url = _oss.get_signed_url(oss_key, expires=86400)
        _jobs[jid]["oss_signed_url"] = oss_signed_url or ""
    else:
        oss_signed_url = None
        info("[BG] OSS unavailable, using local only")

    # ── 创建 assignment + page（基准 Table 12）──
    import uuid as _uuid
    aid = _uuid.uuid4().hex[:12]
    try:
        _db.create_assignment(aid, parent_id, child_id, "web_upload", file.filename)
    except Exception as _e:
        error(f"[BG] create_assignment failed: {_e}")
    page_id = _uuid.uuid4().hex[:12]
    try:
        _db.create_assignment_page(page_id, aid, 1, oss_key or f"/tmp/yomi/{jid}.jpg")
    except Exception as _e:
        error(f"[BG] create_assignment_page failed: {_e}")
    _jobs[jid]["assignment_id"] = aid
    _jobs[jid]["page_id"] = page_id

    _jobs[jid]["trace_id"] = trace_id
    total_parse_cost = 0.0  # 累计 Qwen-VL 解析成本

    try:
        # ── Phase 0: 图片压缩预处理（加速 Qwen-VL）──
        _img_bytes = contents
        try:
            from PIL import Image
            import io as _io
            _img_pil = Image.open(_io.BytesIO(contents))
            _w, _h = _img_pil.size
            _max_dim = max(_w, _h)
            if _max_dim > 1024:
                _ratio = 1024 / _max_dim
                _new_size = (int(_w * _ratio), int(_h * _ratio))
                _img_pil = _img_pil.resize(_new_size, Image.LANCZOS)
                _buf = _io.BytesIO()
                _img_pil.save(_buf, format="JPEG", quality=85)
                _img_bytes = _buf.getvalue()
                info(f"[BG] Image compressed: {_w}x{_h} -> {_new_size[0]}x{_new_size[1]} ({len(contents)}->{len(_img_bytes)} bytes)")
            _img_pil.close()
        except Exception as _ce:
            info(f"[BG] Image compress skipped: {_ce}")

        # ── Phase 1: Qwen-VL 全图 + 通用 OCR 并行 ──
        _jobs[jid]["job"].status = JobStatus.ocr_running
        use_qwen_vl = False
        qwen_parse_call_id = ""
        questions = []
        ocr_blocks = []

        qwen_vl = QwenVLClient()
        qwen_available = qwen_vl._available()

        info(f"[BG] Parallel Qwen(avail={qwen_available},to={QWEN_FULL_TIMEOUT_S}s) + OCR(to={OCR_TIMEOUT_S}s) for {jid}...")

        async def _run_qwen():
            if BPLUSPLUS_ENABLED:
                info(f"[BG] B++ gate active, skipping full-image Qwen for {jid}")
                return {"success": False, "questions": [], "error": "bpp_skip", "latency_ms": 0, "usage": {}, "raw_content": ""}
            if not qwen_available:
                return {"success": False, "questions": [], "error": "qwen_unavailable", "latency_ms": 0, "usage": {}, "raw_content": ""}
            return await asyncio.to_thread(
                qwen_vl.extract_questions,
                image_bytes=_img_bytes if not oss_signed_url else None,
                image_url=oss_signed_url,
                timeout=QWEN_FULL_TIMEOUT_S,
            )

        async def _run_ocr():
            try:
                ocr = AliyunOCRClient()
                result = ocr.recognize(contents)
                return result
            except Exception as _e:
                with open("/tmp/ocr_error.log", "a") as f:
                    import traceback
                    f.write(f"{time.time()}: {_e}\n{traceback.format_exc()}\n")
                raise

        qwen_result = {"success": False, "questions": [], "error": "not_started", "latency_ms": 0, "usage": {}, "raw_content": ""}
        ocr_raw = {"success": False, "blocks": [], "text": "", "latency_ms": 0}

        try:
            gathered = await asyncio.wait_for(
                asyncio.gather(_run_qwen(), _run_ocr(), return_exceptions=True),
                timeout=SLA_DEADLINE_S,
            )
            qwen_result, ocr_raw = gathered
            if isinstance(qwen_result, Exception):
                error(f"[BG] Qwen-VL exception: {qwen_result}")
                qwen_result = {"success": False, "questions": [], "error": str(qwen_result)[:100], "latency_ms": 0, "usage": {}, "raw_content": ""}
            if isinstance(ocr_raw, Exception):
                error(f"[BG] OCR exception: {ocr_raw}")
                ocr_raw = {"success": False, "blocks": [], "text": "", "latency_ms": 0}
        except asyncio.TimeoutError:
            elapsed = int((time.time() - t_start) * 1000)
            error(f"[BG] DEADLINE exceeded after {elapsed}ms for {jid}")
            qwen_result = {"success": False, "questions": [], "error": "deadline", "latency_ms": elapsed, "usage": {}, "raw_content": ""}
            ocr_raw = {"success": False, "blocks": [], "text": "", "latency_ms": elapsed}

        qwen_latency = qwen_result.get("latency_ms", 0)
        ocr_latency = ocr_raw.get("latency_ms", 0)
        ocr_blocks = ocr_raw.get("blocks", [])
        info(f"[BG] Parallel done: Qwen={'OK' if qwen_result.get('success') else 'FAIL'}({qwen_latency}ms) OCR={len(ocr_blocks)}blocks({ocr_latency}ms)")
        usage = qwen_result.get("usage", {}) if qwen_result.get("success") else {}
        input_tokens = usage.get("prompt_tokens", 0) if isinstance(usage, dict) else 0
        output_tokens = usage.get("completion_tokens", 0) if isinstance(usage, dict) else 0
        image_tokens = usage.get("prompt_tokens_details", {}).get("image_tokens", 0) if isinstance(usage, dict) else 0

        pricing = get_active_pricing("aliyun_dashscope", "qwen-vl-max")

        _log = make_log_entry(
            task_id=jid,
            provider_name="aliyun_dashscope",
            model_name="qwen-vl-max",
            feature_code="qwen_vl_parse_homework",
            trace_id=trace_id,
            sub_stage="fullpage_extract",
            latency_ms=qwen_latency,
            success=qwen_result["success"],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            image_count=1,
            image_total_bytes=len(contents),
            parent_user_id=parent_id,
            child_id=child_id,
            billing_status="free_tier" if qwen_result["success"] else "failed",
            pricing=pricing,
            blocks_count=len(qwen_result.get("questions", [])),
        )
        qwen_parse_call_id = _log["id"]
        total_parse_cost += _log.get("cost_cny", 0.0)
        _model_calls.append(_log)
        _db.save_model_call(_log)

        if qwen_result["success"] and qwen_result["questions"]:
            raw_questions = qwen_result["questions"]
            question_count = len(raw_questions)
            if question_count >= 5:
                use_qwen_vl = True
            info(f"[BG] Qwen-VL extracted {question_count} questions in {qwen_latency}ms")

            shared_vd = qwen_result.get("raw_content", f"Qwen-VL 全图识别，共 {question_count} 题")
            parse_cost_per_q = total_parse_cost / question_count if question_count > 0 else 0.0

            for i, rq in enumerate(raw_questions):
                qid = f"{jid}-{rq.get('number', i+1)}-{i}"
                q_text = rq.get("content", f"第{rq.get('number', i+1)}题")
                student_ans = rq.get("student_answer")
                if student_ans is None or (isinstance(student_ans, str) and not student_ans.strip()):
                    student_ans = None
                section_title = rq.get("section_title")
                if section_title and isinstance(section_title, str) and not section_title.strip():
                    section_title = None
                section_index = rq.get("section_index")
                try:
                    section_index = int(section_index) if section_index is not None else None
                except (ValueError, TypeError):
                    section_index = None
                sub_index = rq.get("sub_index")
                try:
                    sub_index = int(sub_index) if sub_index is not None else None
                except (ValueError, TypeError):
                    sub_index = None
                raw_no = rq.get("number", i+1)
                try:
                    q_no = int(raw_no)
                except (ValueError, TypeError):
                    q_no = i + 1
                # 解析 bbox 和 answer_bbox
                _bbox = rq.get("bbox")
                if isinstance(_bbox, list) and len(_bbox) == 4:
                    _bbox = [float(v) for v in _bbox]
                else:
                    _bbox = [0, 0, 0, 0]
                _ans_bbox = rq.get("answer_bbox")
                if isinstance(_ans_bbox, list) and len(_ans_bbox) == 4:
                    _ans_bbox = [float(v) for v in _ans_bbox]
                else:
                    _ans_bbox = None
                questions.append(Question(
                    question_id=qid, question_number=q_no, question_text=q_text,
                    bbox=_bbox, visual_description=shared_vd,
                    status=QuestionStatus.completed, student_answer=student_ans,
                    section_title=section_title, section_index=section_index, sub_index=sub_index,
                ))
                _db.create_question_item(qid, aid, page_id, q_no, q_text, _bbox, shared_vd[:200],
                                          source_call_id=qwen_parse_call_id, parse_cost_allocated_cny=parse_cost_per_q)
        else:
            error(f"[BG] Qwen-VL failed: {qwen_result.get('error', 'no questions')}")



        # ── B++ 灰度链路：答案区优先识别 ──
        if BPLUSPLUS_ENABLED and not use_qwen_vl and ocr_blocks:
            info(f"[BG] B++ route: {len(ocr_blocks)} blocks")
            # 质量检查: 过暗/模糊/半页
            try:
                from PIL import Image as _QImg, ImageStat as _QStat, ImageFilter as _QFlt
                import io as _QIo
                _qimg = _QImg.open(_QIo.BytesIO(contents)).convert("L")
                _qstat = _QStat.Stat(_qimg)
                _bright = _qstat.mean[0]
                _qlap = _qimg.filter(_QFlt.Kernel((3,3),[-1,-1,-1,-1,8,-1,-1,-1,-1],1,128))
                _qvar = _QStat.Stat(_qlap).var[0]
                _qimg.close()
                _qfail = ""
                if _bright < 80: _qfail = "too_dark"
                elif _qvar < 50: _qfail = "too_blurry"
                if _qfail:
                    info(f"[BG] B++ quality reject: {_qfail}")
                    _jobs[jid]["job"].status = JobStatus.needs_review
                    _jobs[jid]["job"].questions_count = 0
                    _jobs[jid]["questions"] = []
                    return
            except Exception as _qe:
                info(f"[BG] B++ quality check skipped: {_qe}")

            # OCR质量: 乱码>50% -> needs_review
            _garbled = sum(1 for b in ocr_blocks if len(b["text"].strip()) < 2 and not b["text"].strip().isdigit())
            if _garbled > len(ocr_blocks) * 0.5:
                info(f"[BG] B++ OCR garbled: {_garbled}/{len(ocr_blocks)} -> needs_review")
                _jobs[jid]["job"].status = JobStatus.needs_review
                _jobs[jid]["job"].questions_count = 0
                _jobs[jid]["questions"] = []
                return

            if _is_homework_image(ocr_blocks):
                # 答案区定位
                _qpat = __import__("re").compile(r'^\s*(\d{1,3})[.、．)）\s]')
                _bs = sorted(ocr_blocks, key=lambda b: (b["y"], b["x"]))
                _markers = []
                for _bi, _b in enumerate(_bs):
                    _m = _qpat.match(_b["text"].strip())
                    if _m:
                        _markers.append({"num":int(_m.group(1)),"y":_b["y"],"x":_b["x"],"h":_b["h"]})
                try:
                    from PIL import Image as _PImg
                    _pi = _PImg.open(__import__("io").BytesIO(_img_bytes))
                    _iw, _ih = _pi.size; _pi.close()
                except:
                    _iw, _ih = 800, 1200
                # 题组3-5题/块
                _GSIZE = 4; _regions = []
                if len(_markers) >= 2:
                    for _gs in range(0, len(_markers), _GSIZE):
                        _ge = min(_gs+_GSIZE, len(_markers))
                        _gm = _markers[_gs:_ge]
                        _yt = max(0, _gm[0]["y"]-6)
                        _yb = min(_ih, _markers[_ge]["y"]-6) if _ge < len(_markers) else min(_ih, _gm[-1]["y"]+_gm[-1]["h"]+100)
                        _xl = max(0, min(b["x"] for b in _bs)-8)
                        _xr = min(_iw, max(b["x"]+b.get("w",50) for b in _bs)+8)
                        if _yb-_yt > 20 and _xr-_xl > 30:
                            _regions.append((_xl, _yt, _xr-_xl, _yb-_yt))
                if not _regions:
                    _regions = [(0, 0, _iw, _ih)]
                _regions = _regions[:5]  # 最多5块

                # 并发Qwen
                async def _bpp_qwen(_region):
                    _rx, _ry, _rw, _rh = _region
                    try:
                        from PIL import Image as _P2
                        _pi2 = _P2.open(__import__("io").BytesIO(_img_bytes))
                        _crop = _pi2.crop((max(0,_rx),max(0,_ry),min(_iw,_rx+_rw),min(_ih,_ry+_rh)))
                        _cb = __import__("io").BytesIO(); _crop.save(_cb,format="JPEG",quality=70)
                        _pi2.close(); _crop_b = _cb.getvalue()
                    except:
                        _crop_b = _img_bytes
                    _qp = ("图中每题一行: 题号|题目内容|孩子手写答案|"
                           "题型(口算/竖式/选择/填空/连线/画图/其他)|置信度(0-1)。"
                           "无答案填无。不要标题日期。不要解释。")
                    return await asyncio.to_thread(
                        lambda: qwen_vl._call(image_bytes=_crop_b, prompt=_qp, max_tokens=500, timeout=8))
                _bppres = await asyncio.gather(*[_bpp_qwen(r) for r in _regions], return_exceptions=True)
                _all_qs = []
                for _br in _bppres:
                    if isinstance(_br, Exception) or not _br.get("success"):
                        continue
                    for _l in _br.get("content","").strip().split("\n"):
                        _parts = _l.split("|")
                        if len(_parts) >= 3:
                            _ans = _parts[2].strip()
                            if _ans in ("无","null","none","","孩子未写","未写"):
                                _ans = None
                            _all_qs.append({"number":_parts[0].strip(),"content":_parts[1].strip(),
                                             "student_answer":_ans,
                                             "question_type":_parts[3].strip() if len(_parts)>3 else "其他"})
                _ans_count = sum(1 for q in _all_qs if q.get("student_answer"))
                if _all_qs and _ans_count > 0:
                    use_qwen_vl = True
                    raw_questions = _all_qs
                    question_count = len(raw_questions)
                    qwen_parse_call_id = f"bpp_{jid}"
                    info(f"[BG] B++ extracted {question_count}q/{_ans_count}ans")
                else:
                    info(f"[BG] B++ quality gate: {len(_all_qs)}q/{_ans_count}ans -> rejected")
            else:
                info(f"[BG] B++ non-homework -> needs_review")
                _jobs[jid]["job"].status = JobStatus.needs_review
                _jobs[jid]["job"].questions_count = 0
                _jobs[jid]["questions"] = []
                return

        # ── OCR fallback: 新模块化流程（大块检测 → 块内切题 → 答案定位）──
        if not use_qwen_vl:
            _jobs[jid]["job"].status = JobStatus.cutting
            # Log OCR call
            _ocr_log = make_log_entry(
                task_id=jid, provider_name="aliyun_ocr", model_name="ocr_general",
                feature_code="ocr_general",
                trace_id=trace_id, latency_ms=ocr_latency,
                success=len(ocr_blocks) > 0,
                parent_user_id=parent_id, child_id=child_id,
                billing_status="free_tier", blocks_count=len(ocr_blocks),
            )
            _model_calls.append(_ocr_log)
            _db.save_model_call(_ocr_log)

            if ocr_blocks and _is_homework_image(ocr_blocks):
                _blocks = detect_blocks(ocr_blocks, _iw, _ih)
                info(f"[BG] BlockDetect: {len(ocr_blocks)} OCR blocks → {len(_blocks)} blocks")
                _jobs[jid]["blocks"] = [b.model_dump() for b in _blocks]
                questions = []
                for _blk in _blocks:
                    _bq = cut_block_to_questions(
                        ocr_blocks, _blk.bbox, _blk.block_id,
                        _blk.title, _blk.subject
                    )
                    _bq = detect_all_answer_bboxes(_bq, ocr_blocks, _ih)
                    for _i, _cq in enumerate(_bq):
                        qid = f"{jid}-{_cq['question_number']}-{len(questions)}"
                        _blk.question_ids.append(qid)
                        _ans_bbox = _cq.get("answer_bbox")
                        questions.append(Question(
                            question_id=qid,
                            block_id=_blk.block_id,
                            question_number=_cq["question_number"],
                            question_text=_cq["question_text"],
                            bbox=_cq["bbox"],
                            answer_bbox=_ans_bbox if _ans_bbox else None,
                            subject=_cq.get("subject", _blk.subject),
                            question_type=_blk.question_type,
                            confidence=0.3,
                            source="ocr",
                            visual_description=None,
                            status=QuestionStatus.completed,
                        ))
                        _db.create_question_item(qid, aid, page_id,
                            _cq["question_number"], _cq["question_text"], _cq["bbox"])
                info(f"[BG] OCR+Blocks: {len(_blocks)} blocks → {len(questions)} questions")
            elif ocr_blocks:
                # 非作业图片 → 用旧 cut_to_questions 兜底
                _pos_blocks = [{"text": b["text"], "pos": [b.get("x", 0), b.get("y", 0), b.get("w", 0), b.get("h", 0)]} for b in ocr_blocks]
                cut_results = cut_to_questions(_pos_blocks)
                questions = []
                for i, cq in enumerate(cut_results):
                    qid = f"{jid}-{cq['question_number']}-{i}"
                    questions.append(Question(
                        question_id=qid, question_number=cq["question_number"],
                        question_text=cq["question_text"], bbox=cq["bbox"],
                        visual_description=None, status=QuestionStatus.completed,
                    ))
                    _db.create_question_item(qid, aid, page_id, cq["question_number"], cq["question_text"], cq["bbox"])
                info(f"[BG] OCR+Cut(fallback): {len(ocr_blocks)} blocks → {len(questions)} questions")
            else:
                info(f"[BG] OCR returned 0 blocks, no questions produced")
                questions = []

        # Stage: vision_reviewing - Qwen-VL 逐题复审（默认禁用，env 控制）
        if not use_qwen_vl:
            if DISABLE_PER_Q_VISION:
                info(f"[BG] Per-question Vision DISABLED (10s SLA), OCR as-is for {jid}")
            else:
                _jobs[jid]["job"].status = JobStatus.vision_reviewing
                MAX_SCHEMA_RETRIES = 2
                MAX_NETWORK_RETRIES = 3
                qwen_vl = QwenVLClient()
                for qi, q in enumerate(questions):
                    schema_retries = 0
                    network_retries = 0
                    while True:
                        try:
                            t_vs = time.time()
                            vl_result = qwen_vl.analyze_question(
                                image_bytes=contents if not oss_signed_url else None,
                                image_url=oss_signed_url,
                                bbox=q.bbox or [0, 0, 0, 0],
                                question_text=q.question_text,
                            )
                            vl_ms = int((time.time() - t_vs) * 1000)
                            q.visual_description = vl_result["visual_description"]
                            if vl_result.get("student_answer"):
                                q.student_answer = vl_result["student_answer"]
                            q.status = QuestionStatus.completed
                            _db.create_question_item(q.question_id, aid, page_id, q.question_number, q.question_text, q.bbox or [], q.visual_description or "")

                            _vlog = make_log_entry(
                                task_id=jid,
                                question_id=q.question_id,
                                job_id=jid,
                                provider_name="aliyun_dashscope",
                                model_name="qwen-vl-max",
                                feature_code="qwen_vl_parse_homework",
                                trace_id=trace_id,
                                sub_stage="question_cutting",
                                latency_ms=vl_ms,
                                success=vl_result["success"],
                                parent_user_id=parent_id,
                                child_id=child_id,
                                error_code=vl_result.get("error"),
                                billing_status="free_tier" if vl_result["success"] else "failed",
                                prompt_name="qwen_vl_analyze",
                                retry_count=schema_retries + network_retries,
                            )
                            _model_calls.append(_vlog)
                            _db.save_model_call(_vlog)
                            info(f"[BG] Vision #{qi}: {vl_ms}ms success={vl_result['success']} retries={schema_retries + network_retries}")
                            break
                        except Exception as ve:
                            err_str = str(ve).lower()
                            if "429" in err_str or "rate_limit" in err_str or "too many requests" in err_str:
                                network_retries += 1
                                if network_retries > MAX_NETWORK_RETRIES:
                                    warning(f"[BG] Vision #{qi} RATE LIMITED after {MAX_NETWORK_RETRIES} retries: {ve}")
                                    q.status = QuestionStatus.failed
                                    break
                                delay = 2 ** network_retries
                                _deferred_vision_tasks.append((qi, q, contents, jid, parent_id, child_id, aid, page_id, network_retries, delay))
                                warning(f"[BG] Vision #{qi} RATE LIMITED, deferred for {delay}s: {ve}")
                                break
                            elif "timeout" in err_str or "connection" in err_str or "timed out" in err_str:
                                network_retries += 1
                                if network_retries > MAX_NETWORK_RETRIES:
                                    error(f"[BG] Vision #{qi} NETWORK FAILED after {MAX_NETWORK_RETRIES} retries: {ve}")
                                    q.status = QuestionStatus.failed
                                    break
                                wait_s = 2 ** network_retries
                                info(f"[BG] Vision #{qi} network retry {network_retries}/{MAX_NETWORK_RETRIES}, waiting {wait_s}s: {ve}")
                                await asyncio.sleep(wait_s)
                            else:
                                schema_retries += 1
                                if schema_retries > MAX_SCHEMA_RETRIES:
                                    error(f"[BG] Vision #{qi} SCHEMA FAILED after {MAX_SCHEMA_RETRIES} retries: {ve}")
                                    q.status = QuestionStatus.failed
                                    break
                                info(f"[BG] Vision #{qi} schema retry {schema_retries}/{MAX_SCHEMA_RETRIES}: {ve}")
                                await asyncio.sleep(1)

                # ── 延后队列处理 ──
                if _deferred_vision_tasks:
                    info(f"[BG] Processing {len(_deferred_vision_tasks)} deferred vision tasks...")
                    MAX_DEFERRED_RETRIES = 3
                    deferred_retry = 0
                    while _deferred_vision_tasks and deferred_retry < MAX_DEFERRED_RETRIES:
                        deferred_retry += 1
                        pending = _deferred_vision_tasks[:]
                        _deferred_vision_tasks.clear()
                        for qi, q, contents, jid, parent_id, child_id, aid, page_id, n_retries, delay in pending:
                            await asyncio.sleep(delay)
                            try:
                                t_vs = time.time()
                                vl_result = qwen_vl.analyze_question(
                                    image_bytes=contents if not oss_signed_url else None,
                                    image_url=oss_signed_url,
                                    bbox=q.bbox or [0, 0, 0, 0],
                                    question_text=q.question_text,
                                )
                                vl_ms = int((time.time() - t_vs) * 1000)
                                q.visual_description = vl_result["visual_description"]
                                if vl_result.get("student_answer"):
                                    q.student_answer = vl_result["student_answer"]
                                q.status = QuestionStatus.completed
                                _db.create_question_item(q.question_id, aid, page_id, q.question_number, q.question_text, q.bbox or [], q.visual_description or "")
                                _vlog = make_log_entry(
                                    task_id=jid, question_id=q.question_id, job_id=jid,
                                    provider_name="aliyun_dashscope", model_name="qwen-vl-max",
                                    feature_code="qwen_vl_parse_homework",
                                    trace_id=trace_id, sub_stage="question_cutting",
                                    latency_ms=vl_ms,
                                    success=vl_result["success"], parent_user_id=parent_id,
                                    child_id=child_id, error_code=vl_result.get("error"),
                                    billing_status="free_tier" if vl_result["success"] else "failed",
                                    prompt_name="qwen_vl_analyze", retry_count=n_retries,
                                )
                                _model_calls.append(_vlog)
                                _db.save_model_call(_vlog)
                                info(f"[BG] Deferred Vision #{qi} succeeded after {n_retries} retries: {vl_ms}ms")
                            except Exception as ve:
                                err_str = str(ve).lower()
                                next_retries = n_retries + 1
                                if next_retries > MAX_NETWORK_RETRIES:
                                    error(f"[BG] Deferred Vision #{qi} FAILED after {n_retries} retries: {ve}")
                                    q.status = QuestionStatus.failed
                                elif "429" in err_str or "rate_limit" in err_str:
                                    next_delay = 2 ** next_retries
                                    _deferred_vision_tasks.append((qi, q, contents, jid, parent_id, child_id, aid, page_id, next_retries, next_delay))
                                    info(f"[BG] Deferred Vision #{qi} RE-RATE-LIMITED, re-deferred {next_delay}s")
                                else:
                                    error(f"[BG] Deferred Vision #{qi} non-429 error: {ve}")
                                    q.status = QuestionStatus.failed
                    if _deferred_vision_tasks:
                        error(f"[BG] {len(_deferred_vision_tasks)} deferred tasks abandoned after {MAX_DEFERRED_RETRIES} batch retries")
        else:
            info("[BG] Qwen-VL full-page mode: skipping per-question vision review")

        # Stage: schema_validating — 基准 Table 21
        _jobs[jid]["job"].status = JobStatus.schema_validating
        _ocr_only = not use_qwen_vl and DISABLE_PER_Q_VISION
        has_failures = False
        for q in questions:
            if q.status == QuestionStatus.failed:
                has_failures = True
                continue
            # Phase 0 轻量校验：题目文本完整性（OCR-only 不要求 visual_description）
            if not q.question_text or len(q.question_text.strip()) < 2:
                q.status = QuestionStatus.failed
                has_failures = True
            elif not _ocr_only and q.visual_description is None:
                q.status = QuestionStatus.failed
                has_failures = True

        if has_failures or len(questions) == 0:
            # 有真正失败或零题 → needs_review
            final_status = JobStatus.needs_review
        # ── Quality gate: OCR-only 路径缺少答案识别 → needs_review ──
        if _ocr_only:
            # OCR 成功切出题目 → 先过作业图预筛
            if _is_homework_image(ocr_blocks):
                final_status = JobStatus.needs_review
                info(f"[BG] OCR-only homework image (Qwen failed) → needs_review for {jid}")
            else:
                final_status = JobStatus.needs_review
                info(f"[BG] OCR-only non-homework image → needs_review for {jid}")
        else:
            final_status = JobStatus.completed
        # 存储解析元数据供 save_result 写入 parse_jobs
        _jobs[jid]["parse_mode"] = "qwen_vl"
        _jobs[jid]["parser_provider"] = "aliyun_dashscope"
        _jobs[jid]["parser_model"] = "qwen-vl-max"
        _jobs[jid]["qwen_parse_call_id"] = qwen_parse_call_id
        _jobs[jid]["total_parse_cost_cny"] = total_parse_cost
        # ── Stage: grading ── DeepSeek 批量判对错 ──
        grading_cost = 0.0
        try:
            grading_cost = await grade_answers(jid, questions, trace_id, parent_id, child_id)
        except Exception as _ge:
            error(f"[BG] Grading stage failed (non-blocking): {_ge}")
        total_parse_cost += grading_cost
        # ── Post-grading quality gate with reason_code ──
        _with_g = sum(1 for q in questions if (getattr(q, "is_correct", None) is not None) or (isinstance(q, dict) and q.get("is_correct") is not None))
        _with_sa = sum(1 for q in questions if (getattr(q, "student_answer", None)) or (isinstance(q, dict) and q.get("student_answer")))
        _with_child_a = sum(1 for q in questions if (getattr(q, "child_answer", None)) or (isinstance(q, dict) and q.get("child_answer")))
        _ans_count = _with_sa + _with_child_a
        _blocks_data = _jobs[jid].get("blocks", [])
        
        # 填充 ParseJob 扩展字段
        if hasattr(_jobs[jid]["job"], "subject"):
            _jobs[jid]["job"].subject = _blocks_data[0].get("subject", "") if _blocks_data else ""
        if hasattr(_jobs[jid]["job"], "blocks_count"):
            _jobs[jid]["job"].blocks_count = len(_blocks_data)
        if hasattr(_jobs[jid]["job"], "answer_count"):
            _jobs[jid]["job"].answer_count = _ans_count
        if hasattr(_jobs[jid]["job"], "graded_count"):
            _jobs[jid]["job"].graded_count = _with_g
        
        # 质量门判断
        if len(questions) > 0 and _ans_count > 0:
            final_status = JobStatus.completed
        elif len(questions) > 0 and _ans_count == 0:
            final_status = JobStatus.no_answers
            if hasattr(_jobs[jid]["job"], "reason_code"):
                _jobs[jid]["job"].reason_code = "no_child_answers"
            info(f"[BG] Quality gate: {len(questions)}q but 0 answers -> no_answers for {jid}")
        elif len(questions) == 0:
            final_status = JobStatus.rejected
            if hasattr(_jobs[jid]["job"], "reason_code") and not _jobs[jid]["job"].reason_code:
                _jobs[jid]["job"].reason_code = "empty_page"
            info(f"[BG] Quality gate: 0 questions -> rejected for {jid}")
        
        save_result(jid, questions, now, file, final_status)
        # 诊断：判对错保存统计
        _with_g = sum(1 for q in questions if (getattr(q, "is_correct", None) is not None) or (isinstance(q, dict) and q.get("is_correct") is not None))
        _with_sa = sum(1 for q in questions if (getattr(q, "student_answer", None)) or (isinstance(q, dict) and q.get("student_answer")))
        debug("[diag] grading_saved jid={jid} questions={len(questions)} with_grading={_with_g} with_child_answer={_with_sa}")
        debug("[diag] worker_completed jid={jid} status={final_status.value} qcount={len(questions)} cost_cny={total_parse_cost:.4f}")

    except Exception as e:
        latency_ms = int((time.time() - t_start) * 1000)
        _flog = make_log_entry(
            task_id=jid,
            provider_name="aliyun_ocr",
            model_name="ocr_api20210707",
            feature_code="ocr",
            latency_ms=latency_ms,
            success=False,
            parent_user_id=parent_id,
            child_id=child_id,
            error_code=str(e)[:100],
            billing_status="failed",
        )
        _model_calls.append(_flog)
        _db.save_model_call(_flog)
        _log_error(f"worker_failed jid={jid}: {e}", trace_id=trace_id)
        # 保存错误到 job entry，让 status API 返回可见
        _jobs[jid]["error_code"] = str(e)[:200]
        _jobs[jid]["progress"] = f"worker_crash: {str(e)[:100]}"
        # 防御：job entry 可能被破坏
        try:
            if "job" in _jobs[jid] and hasattr(_jobs[jid]["job"], "status"):
                _jobs[jid]["job"].status = JobStatus.failed
        except Exception:
            pass
        # 尝试保存失败状态，让前端能看到
        try:
            save_result(jid, [], now, file, JobStatus.failed)
        except Exception:
            pass
