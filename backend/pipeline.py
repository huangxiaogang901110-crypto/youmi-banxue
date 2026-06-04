"""
管线核心函数。
从 main.py 抽出，不改行为。
"""
import asyncio
import json
import os as _os
import math
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
from ocr_block_export import export_ocr_blocks_if_enabled
from grading_unit import (
    apply_grading_unit_metadata,
    build_grading_units,
    build_group_boxes,
    run_grading_unit_review,
)
from math_ocr_first import math_ocr_first_extract
from question_cutter import cut_to_questions
from document_classifier import (
    DocumentClassification,
    assign_question_sections,
    classify_document,
    clean_question_text,
    extract_structured_questions_from_ocr,
    filter_ocr_blocks_for_question_region,
    is_meta_instruction_or_footer_text,
    is_pseudo_or_garbled_question,
    should_drop_candidate_question,
    should_extract_questions,
    should_extract_structural_questions,
    summarize_question_alignment,
)
from schemas.recognition import (
    RecognitionImage,
    RecognitionQuestionContract,
    build_overlay_mark,
    _get_question_field,
    build_recognition_document,
    normalize_bbox as _normalize_question_bbox,
    normalize_confidence,
    should_drop_question_payload as _should_drop_raw_question,
)
import oss_client as _oss


# ═══════════════════════════════════════════════════════════════

def _get_qwen_full_timeout_seconds(default: int = 30) -> int:
    for key in (
        "QWENVLTIMEOUT",
        "YOMIQWENVLTIMEOUTSECONDS",
        "YOMIQWENFULLTIMEOUTSECONDS",
        "YOMIQWENFULLTIMEOUT_SECONDS",
    ):
        value = _os.getenv(key)
        if not value:
            continue
        try:
            return int(value)
        except ValueError:
            continue
    return default


def _get_call_source() -> str:
    """Read YOMICALL_SOURCE; fall back to legacy YOMICALLSOURCE (no underscore)."""
    v = _os.environ.get("YOMICALL_SOURCE") or _os.environ.get("YOMICALLSOURCE")
    return v.strip() if v and v.strip() else "dev_ocrfirst_real_test"


def enqueue_parse_job(jid: str, job_entry: dict):
    """将任务注册到内存队列 + SQLite 持久化。"""
    _jobs[jid] = job_entry
    _db.save_job(jid, job_entry)


def _build_recognition_snapshot(jid: str, questions: list, file, status: JobStatus):
    result_image_url = _jobs[jid].get("image_url") or None
    image_payload = _jobs[jid].get("recognition_image")
    document_classification = _jobs[jid].get("document_classification") or {}
    if not image_payload:
        image_payload = RecognitionImage(
            id=jid,
            text=file.filename if file else jid,
            source="upload",
            kind="image",
            status="completed",
            file_path=result_image_url or f"/tmp/yomi/{jid}.jpg",
        ).model_dump()
    meta = {
        "job_id": jid,
        "status": status.value,
        "image_url": result_image_url,
        "parse_mode": _jobs[jid].get("parse_mode", ""),
        "parser_provider": _jobs[jid].get("parser_provider", ""),
        "parser_model": _jobs[jid].get("parser_model", ""),
        "qwen_parse_call_id": _jobs[jid].get("qwen_parse_call_id", ""),
        "total_parse_cost_cny": _jobs[jid].get("total_parse_cost_cny", 0.0),
        "progress": _jobs[jid].get("progress", ""),
        "error_code": _jobs[jid].get("error_code", ""),
        "document_classification": document_classification,
        "page_type": document_classification.get("page_type", ""),
        "document_family": document_classification.get("doc_family", ""),
        "subject": document_classification.get("subject", ""),
        "support_level": document_classification.get("support_level", ""),
        "route_hint": document_classification.get("route_hint", ""),
        "confidence": document_classification.get("confidence", 0.0),
        "reason": document_classification.get("reason", ""),
    }
    return build_recognition_document(
        image=image_payload,
        raw_questions=questions,
        raw_blocks=_jobs[jid].get("ocr_blocks", []),
        block_source=_jobs[jid].get("ocr_block_source", "ocr_unknown"),
        layout_regions=document_classification.get("layout_regions", []),
        meta=meta,
    ).model_dump()


def save_result(jid: str, questions: list, now: str, file, status: JobStatus):
    """保存任务最终结果 — SQLite 持久化。先写 questions 再标 completed，防 poll 读到 qcount=0。"""
    # ⚠️ 顺序：先写 questions 再设 status，防止 completed 和 questions 之间的竞态窗口
    _jobs[jid]["questions"] = questions
    result_image_url = _jobs[jid].get("image_url") or None
    document_classification = _jobs[jid].get("document_classification") or {}
    recognition_snapshot = _build_recognition_snapshot(jid, questions, file, status)
    _jobs[jid]["recognition"] = recognition_snapshot
    _jobs[jid]["job"] = ParseJob(
        job_id=jid, status=status,
        questions_count=len(questions),
        created_at=now, updated_at=_ts(), file_name=file.filename if file else "",
        image_url=result_image_url,
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
        "image_url": result_image_url,
        "questions": [q.model_dump() for q in questions],
        "recognition": recognition_snapshot,
        "document_classification": document_classification,
        "overlay": _jobs[jid].get("overlay", []),
        "group_boxes": _jobs[jid].get("group_boxes", []),
        "grading_units": _jobs[jid].get("grading_units", []),
        "roi_result": _jobs[jid].get("roi_result"),
        "preprocess_versions": _jobs[jid].get("preprocess_versions", []),
        "ocr_blocks": _jobs[jid].get("ocr_blocks", []),
        "ocr_block_source": _jobs[jid].get("ocr_block_source", "ocr_unknown"),
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
                            "image_url": result_image_url,
                            "questions": [q.model_dump() for q in questions],
                            "recognition": recognition_snapshot,
                            "document_classification": document_classification,
                            "overlay": _jobs[jid].get("overlay", []),
                            "group_boxes": _jobs[jid].get("group_boxes", []),
                            "grading_units": _jobs[jid].get("grading_units", []),
                            "roi_result": _jobs[jid].get("roi_result"),
                            "preprocess_versions": _jobs[jid].get("preprocess_versions", []),
                            "ocr_blocks": _jobs[jid].get("ocr_blocks", []),
                            "ocr_block_source": _jobs[jid].get("ocr_block_source", "ocr_unknown"),
                            "poll_count": _jobs[jid].get("poll_count", 0),
                            "child_id": child_id, "parent_id": parent_id,
                            "client_task_id": _jobs[jid].get("client_task_id", ""),
                            "progress": progress, "error_code": error_code,
                            "retry_count": retry_count, "completed_at": completed_at,
                        }, default=str),
                        client_upload_id=_jobs[jid].get("client_upload_id", _jobs[jid].get("client_task_id", "")))


async def grade_answers(
    jid: str,
    questions: list,
    grading_units: list[dict],
    image_bytes: bytes,
    trace_id: str,
    parent_id: str,
    child_id: str,
) -> float:
    """DeepSeek 批量判对错。返回 grading 总成本 CNY。
    失败时所有题 is_correct=null, grading_explanation='暂未判定'。
    数学口算/加减乘除/比大小/填空优先规则判题，减少 DeepSeek 耗时。
    """
    # ── Phase A: 数学规则快速判题 ──
    from math_grader import _try_math_rule_grading
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

    grading_cost = await run_grading_unit_review(
        jid=jid,
        questions=questions,
        units=grading_units,
        image_bytes=image_bytes,
        trace_id=trace_id,
        parent_id=parent_id,
        child_id=child_id,
    )

    # Qwen 补判后再跑一轮规则判题，吃掉新识别出的答案。
    _rule_graded_after_unit = 0
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
                _rule_graded_after_unit += 1
    if _rule_graded_after_unit > _rule_graded:
        info(f"[BG] Grading-unit rule graded {_rule_graded_after_unit}/{len(questions)} questions for {jid}")

    # 只对有 student_answer 且未被规则判题的题走 DeepSeek
    gradable = [(i, q) for i, q in enumerate(questions)
                 if (hasattr(q, "student_answer") and q.student_answer or
                     isinstance(q, dict) and q.get("student_answer"))
                 and (getattr(q, "is_correct", None) is None if hasattr(q, "is_correct") else
                      (q.get("is_correct") is None if isinstance(q, dict) else True))]
    with open("/tmp/grade_diag.log", "a") as _f:
        _f.write(f"{_ts()} | grade_answers START jid={jid} total_q={len(questions)} gradable={len(gradable)}\n")
    if not gradable:
        return grading_cost

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
            billing_status="billed" if result["success"] else "failed",
            pricing=pricing, question_count=len(gradable),
            call_source=_get_call_source(),
        )
        grading_cost += glog.get("cost_cny", 0.0)
        _model_calls.append(glog)
        _db.save_model_call(glog)

        if not result["success"]:
            error(f"[BG] Grading failed for {jid}: {result.get('error', 'unknown')}")
            return grading_cost

        # 解析 DeepSeek 返回的 JSON — 多级鲁棒提取
        content = result["reply_text"]
        with open(f"/tmp/grade_reply_{jid}.txt", "w") as _f:
            _f.write(content)
        import re as _re2
        parse_method = "direct_json"
        grades = []
        _clean = content.strip()
        # 策略 1：strip markdown code fences
        _fence = _re2.search(r'```(?:json)?\s*\n?(.*?)\n?```', _clean, _re2.DOTALL)
        if _fence:
            _clean = _fence.group(1).strip()
            parse_method = "markdown_strip"
        # 策略 2：direct JSON parse
        try:
            grades = json.loads(_clean)
            if parse_method == "markdown_strip": pass
            else: parse_method = "direct_json"
        except json.JSONDecodeError:
            # 策略 3：anchored bracket — 从第一个 [{ 开始匹配
            _anchor = _re2.search(r'\[\s*\{', _clean)
            if _anchor:
                _start = _anchor.start()
                _subset = _clean[_start:]
                # 策略 3a：完整 JSON
                try:
                    grades = json.loads(_subset)
                    parse_method = "anchored_bracket"
                except json.JSONDecodeError:
                    # 策略 3b：truncation fix — 取到最后一个完整 }，补 ]
                    _last_brace = _subset.rfind("}")
                    if _last_brace > 0:
                        try:
                            grades = json.loads(_subset[:_last_brace + 1] + "]")
                            parse_method = "truncation_fix"
                        except json.JSONDecodeError:
                            pass
            # 策略 4：greedy regex fallback (原逻辑)
            if not grades:
                _json_match = _re2.search(r'\[.*\]', _clean, _re2.DOTALL)
                if _json_match:
                    try:
                        grades = json.loads(_json_match.group())
                        parse_method = "greedy_regex"
                    except json.JSONDecodeError:
                        pass
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
                # DeepSeek 未覆盖该题 → 先检查是否已有规则判题结果
                if isinstance(q, dict):
                    _existing = q.get("is_correct")
                    if _existing is None:
                        _existing = q.get("iscorrect")
                else:
                    _existing = getattr(q, "is_correct", None)
                    if _existing is None:
                        _existing = getattr(q, "iscorrect", None)
                if _existing in (True, False):
                    continue  # 规则已判题，保留结果，不覆盖
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


def _extract_question_texts(questions: list) -> list[str]:
    question_texts: list[str] = []
    for question in questions:
        if hasattr(question, "question_text"):
            text = question.question_text
        elif isinstance(question, dict):
            text = question.get("question_text", "")
        else:
            text = ""
        text = str(text or "").strip()
        if text:
            question_texts.append(text)
    return question_texts


def _build_document_classification(
    ocr_blocks: list,
    questions: list,
    *,
    image_width: int | None = None,
    image_height: int | None = None,
) -> dict:
    raw_text = "\n".join(
        str(block.get("text", "")).strip()
        for block in ocr_blocks
        if isinstance(block, dict) and str(block.get("text", "")).strip()
    )
    return classify_document(
        raw_text=raw_text,
        question_texts=_extract_question_texts(questions),
        ocr_blocks=ocr_blocks,
        image_width=image_width,
        image_height=image_height,
    ).model_dump()


def _update_document_classification_stats(
    document_classification: dict | None,
    questions: list,
    *,
    image_width: int | None = None,
    image_height: int | None = None,
) -> dict:
    if not document_classification:
        return {}
    stats = summarize_question_alignment(
        questions,
        document_classification,
        image_width=image_width,
        image_height=image_height,
    )
    merged = dict(document_classification)
    existing_stats = dict(merged.get("stats") or {})
    existing_stats.update(stats)
    merged["stats"] = existing_stats
    if stats.get("meta_like_question_count", 0) > 0 and not merged.get("major_failure_reason"):
        merged["major_failure_reason"] = "meta_like_question_leak"
    return merged


def _apply_document_support_gate(
    final_status: JobStatus,
    questions: list,
    document_classification: dict | None,
    grading_count: int,
    student_answer_count: int,
) -> JobStatus:
    if final_status in (JobStatus.failed, JobStatus.needs_review, JobStatus.low_confidence):
        return final_status
    if not questions or not document_classification:
        return final_status

    support_level = str(document_classification.get("support_level", "partial")).strip().lower()
    if support_level == "unsupported":
        return JobStatus.low_confidence
    if support_level != "full" and (grading_count == 0 or student_answer_count == 0):
        return JobStatus.low_confidence
    return final_status


def _drop_questions_for_conservative_page_type(
    questions: list,
    document_classification: dict | None,
) -> list:
    if not questions or not document_classification:
        return questions
    page_type = str(document_classification.get("page_type", "unknown")).strip().lower()
    if page_type not in {"cover_or_instruction_page", "non_homework", "unknown"}:
        return questions
    if not document_classification.get("major_failure_reason"):
        document_classification["major_failure_reason"] = f"{page_type}_suppressed"
    return []


def _decide_final_status(questions: list, has_failures: bool, ocr_only: bool) -> JobStatus:
    if has_failures or len(questions) == 0:
        return JobStatus.needs_review
    if ocr_only:
        return JobStatus.needs_review
    return JobStatus.completed


def _validate_answer_bbox(
    bbox,
    question_bbox=None,
    image_width=None,
    image_height=None,
):
    """answer_bbox 安全门：不可靠→返回None"""
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        x, y, w, h = [float(v) for v in bbox]
    except (TypeError, ValueError):
        return None
    # [0,0,0,0]
    if x == 0 and y == 0 and w == 0 and h == 0:
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        return None
    if w <= 0 or h <= 0:
        return None
    bbox_area = w * h
    # 坐标必须在图片内
    if image_width and image_height:
        if x < 0 or y < 0 or x + w > image_width or y + h > image_height:
            return None
        # 面积不得超过图片面积 30%
        bbox_area = w * h
        img_area = image_width * image_height
        if img_area > 0 and bbox_area > img_area * 0.3:
            return None
    # 不得等于整题bbox
    if question_bbox and isinstance(question_bbox, list) and len(question_bbox) == 4:
        try:
            qx, qy, qw, qh = [float(v) for v in question_bbox]
            if abs(x - qx) < 3 and abs(y - qy) < 3 and abs(w - qw) < 3 and abs(h - qh) < 3:
                return None
            # 面积不得异常接近 question_bbox（>90% overlap）
            q_area = qw * qh
            if q_area > 0 and bbox_area > q_area * 0.85:
                return None
        except (TypeError, ValueError):
            pass
    return [x, y, w, h]


def _infer_answer_bbox_from_ocr(
    question_bbox,
    ocr_blocks,
    image_width=None,
    image_height=None,
):
    """从OCR blocks推断学生答案区域（题区下方/右侧的文字块）"""
    if not question_bbox or not ocr_blocks:
        return None
    try:
        qx, qy, qw, qh = [float(v) for v in question_bbox]
    except (TypeError, ValueError):
        return None

    candidates = []
    for block in ocr_blocks:
        bx = block.get("x", 0)
        by = block.get("y", 0)
        bw = block.get("w", 0)
        bh = block.get("h", 0)
        text = block.get("text", "").strip()
        if bw <= 0 or bh <= 0 or not text:
            continue
        # 在题区的下方或右侧
        below = (by >= qy + qh * 0.3) and (by <= qy + qh * 4) and abs(bx - qx) < qw * 2
        right = (bx >= qx + qw * 0.3) and (bx <= qx + qw * 3) and abs(by - qy) < qh * 1.5
        if below or right:
            candidates.append([bx, by, bw, bh])

    if not candidates:
        return None

    min_x = min(c[0] for c in candidates)
    min_y = min(c[1] for c in candidates)
    max_x = max(c[0] + c[2] for c in candidates)
    max_y = max(c[1] + c[3] for c in candidates)
    return [min_x, min_y, max_x - min_x, max_y - min_y]


def _assess_ocr_confidence(
    question_text,
    student_answer,
    bbox,
    answer_bbox,
    confidence=None,
):
    """评估OCR识别置信度。返回 (needs_boost, reason_str)"""
    reasons = []
    if not question_text or len(str(question_text).strip()) < 6:
        reasons.append("text_short")
    if not answer_bbox:
        reasons.append("no_answer_bbox")
    if not student_answer:
        reasons.append("no_student_answer")
    if confidence is not None and float(confidence) < 0.5:
        reasons.append("low_confidence")
    needs_boost = len(reasons) > 0
    return needs_boost, "|".join(reasons) if reasons else "ok"


def _ocr_first_extract_questions(
    jid,
    ocr_blocks,
    document_classification,
    image_width,
    image_height,
    result_image_url,
    aid,
    page_id,
):
    """OCR主链路：blocks → 结构提取 → 置信度评估。
    返回 (questions, boost_candidates)
    """
    questions = []
    boost_candidates = []

    if not ocr_blocks:
        return [], []

    if should_extract_structural_questions(document_classification):
        structured_questions = extract_structured_questions_from_ocr(
            ocr_blocks,
            document_classification,
            image_width=image_width,
            image_height=image_height,
        )
        for raw_index, rq in enumerate(structured_questions):
            qid = f"{jid}-ocrstruct-{rq.get('question_number', raw_index + 1)}-{raw_index}"
            question_text = clean_question_text(rq.get("question_text", ""), document_classification)
            q_bbox = _normalize_question_bbox(rq.get("bbox"))

            if should_drop_candidate_question(question_text, q_bbox, document_classification):
                continue

            inferred_ab = None
            if q_bbox:
                inferred_ab = _infer_answer_bbox_from_ocr(q_bbox, ocr_blocks, image_width, image_height)
                inferred_ab = _validate_answer_bbox(inferred_ab, q_bbox, image_width, image_height)

            payload = RecognitionQuestionContract(
                question_id=qid,
                question_number=int(rq.get("question_number") or (raw_index + 1)),
                question_text=question_text,
                kind=rq.get("kind", "question"),
                question_role=rq.get("question_role"),
                context_text=rq.get("context_text"),
                options=rq.get("options"),
                blank_count=rq.get("blank_count"),
                bbox=q_bbox,
                answer_bbox=inferred_ab,
                image_url=result_image_url or None,
                status=QuestionStatus.completed.value,
                student_answer=None,
                source="ocr_main",
                confidence=None,
                error_code=None,
            )
            q = Question(**payload.model_dump())
            questions.append(q)
            _db.create_question_item(qid, aid, page_id, q.question_number, question_text, q_bbox or [])

            needs_boost, reason = _assess_ocr_confidence(
                question_text, None, q_bbox, inferred_ab
            )
            if needs_boost:
                boost_candidates.append((len(questions) - 1, q, reason))

    elif should_extract_questions(document_classification):
        filtered_blocks = filter_ocr_blocks_for_question_region(ocr_blocks, document_classification)
        pos_blocks = [
            {"text": b["text"], "pos": [b.get("x", 0), b.get("y", 0), b.get("w", 0), b.get("h", 0)]}
            for b in filtered_blocks
        ]
        cut_results = cut_to_questions(pos_blocks)

        for i, cq in enumerate(cut_results):
            cleaned_text = clean_question_text(cq["question_text"], document_classification)
            q_bbox = _normalize_question_bbox(cq.get("bbox"))

            if should_drop_candidate_question(cleaned_text, q_bbox, document_classification):
                continue

            inferred_ab = None
            if q_bbox:
                inferred_ab = _infer_answer_bbox_from_ocr(q_bbox, ocr_blocks, image_width, image_height)
                inferred_ab = _validate_answer_bbox(inferred_ab, q_bbox, image_width, image_height)

            qid = f"{jid}-ocrcut-{cq['question_number']}-{i}"
            payload = RecognitionQuestionContract(
                question_id=qid,
                question_number=cq["question_number"],
                question_text=cleaned_text,
                kind="question",
                bbox=q_bbox,
                answer_bbox=inferred_ab,
                image_url=result_image_url or None,
                visual_description=None,
                status=QuestionStatus.completed.value,
                student_answer=None,
                source="ocr_main",
                confidence=None,
                error_code=None,
            )
            q = Question(**payload.model_dump())
            questions.append(q)
            _db.create_question_item(qid, aid, page_id, cq["question_number"], cleaned_text, q_bbox or [])

            needs_boost, reason = _assess_ocr_confidence(
                cleaned_text, None, q_bbox, inferred_ab
            )
            if needs_boost:
                boost_candidates.append((len(questions) - 1, q, reason))

    questions = assign_question_sections(questions, document_classification)
    return questions, boost_candidates


async def _qwen_boost_group(
    jid,
    questions,
    boost_candidates,
    image_bytes,
    oss_signed_url,
    document_classification,
    trace_id,
    parent_id,
    child_id,
    image_width=None,
    image_height=None,
):
    """Qwen-VL 按题组增强，每图最多3次调用。返回更新后的questions"""
    MAX_BOOST_CALLS = 3

    if not boost_candidates:
        return questions

    # 按空间位置聚类boost candidates
    groups = []
    current_group = []

    for item in boost_candidates:
        idx, q, reason = item
        bbox = list(q.bbox) if q.bbox else [0, 0, 0, 0]
        if not current_group:
            current_group = [item]
        else:
            last_bbox = list(current_group[-1][1].bbox) if current_group[-1][1].bbox else [0, 0, 0, 0]
            if abs(bbox[1] - last_bbox[1]) < 250:
                current_group.append(item)
            else:
                groups.append(current_group)
                current_group = [item]
    if current_group:
        groups.append(current_group)

    groups = groups[:MAX_BOOST_CALLS]

    qwen_vl = QwenVLClient()
    boosted_indices = set()

    for gi, group in enumerate(groups):
        q_texts = []
        for idx, q, reason in group:
            q_texts.append(f"题{q.question_number}: {str(q.question_text)[:60]}")

        prompt = (
            f"图中{len(group)}道题需要补充孩子手写答案信息：\n" +
            "\n".join(q_texts) +
            "\n\n请识别每道题的孩子手写答案内容（student_answer）及答案区域坐标。"
            "如果看不清填null。"
            "answer_bbox 必须是对象格式：{\"x\":数字,\"y\":数字,\"width\":数字,\"height\":数字}  "
            "或附带bbox_format标识：{\"bbox\":[x,y,w,h],\"bbox_format\":\"xywh\"} "
            "或 {\"bbox\":[x1,y1,x2,y2],\"bbox_format\":\"xyxy\"}。禁止只返回纯数字数组。"
            "输出JSON数组：[{\"question_number\":题号,\"student_answer\":\"答案或null\",\"answer_bbox\":{\"x\":...,\"y\":...,\"width\":...,\"height\":...}}]"
        )

        _t0 = time.time()
        _boost_exc: str | None = None
        result = None
        try:
            result = await asyncio.to_thread(
                qwen_vl._call,
                image_bytes=image_bytes if not oss_signed_url else None,
                image_url=oss_signed_url,
                prompt=prompt,
                max_tokens=500,
                timeout=15,
            )
        except Exception as e:
            _boost_exc = str(e)[:200]

        _boost_latency_ms = int((time.time() - _t0) * 1000)
        _call_source = _get_call_source()

        # Write failed model_call on exception or API failure (observable failure path)
        if _boost_exc is not None or not result or not result.get("success"):
            _err_msg = _boost_exc or str((result or {}).get("error", "api_failed"))[:200]
            try:
                pricing = get_active_pricing("aliyun_dashscope", "qwen-vl-max")
                fail_log = make_log_entry(
                    task_id=jid, provider_name="aliyun_dashscope", model_name="qwen-vl-max",
                    feature_code="qwen_boost_group", trace_id=trace_id,
                    sub_stage="answer_extraction",
                    latency_ms=_boost_latency_ms, success=False,
                    parent_user_id=parent_id, child_id=child_id,
                    billing_status="failed", pricing=pricing,
                    call_source=_call_source,
                    error_message=_err_msg,
                )
                _model_calls.append(fail_log)
                _db.save_model_call(fail_log)
            except Exception:
                pass
            info(f"[BG] Qwen boost group {gi} failed: {_err_msg}")
            continue

        # Parse JSON; model_call written after outcome known — no premature success=True
        _parse_error: str | None = None
        try:
            content = result.get("content", "")
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if not json_match:
                _parse_error = "no_json"
            else:
                try:
                    boost_data = json.loads(json_match.group())
                except json.JSONDecodeError:
                    _parse_error = "bad_json"
                    boost_data = None
                if _parse_error is not None:
                    pass
                elif not isinstance(boost_data, list):
                    _parse_error = "json_not_array"
                elif not boost_data:
                    _parse_error = "empty_json_array"
                else:
                    _matched_qnums: set = set()
                    _usable_bbox_written_count = 0
                    _missing_question_number = False
                    _bbox_rejected = False
                    pending_updates = []
                    for item in boost_data:
                        if not isinstance(item, dict):
                            _missing_question_number = True
                            continue
                        qn = item.get("question_number")
                        if qn is None:
                            _missing_question_number = True
                            continue
                        sa = item.get("student_answer")
                        # Contract-first bbox parsing: object format > bbox_format > pure list
                        ab = item.get("answer_bbox") or item.get("answerbbox")
                        _ab_raw = None
                        _ab_format = None  # "xywh" | "xyxy" | None (pure list, default xywh)
                        if isinstance(ab, dict):
                            # Object format: {"x":..., "y":..., "width":..., "height":...}
                            if all(k in ab for k in ("x", "y", "width", "height")):
                                try:
                                    _ab_raw = [float(ab["x"]), float(ab["y"]), float(ab["width"]), float(ab["height"])]
                                    _ab_format = "xywh"
                                except (TypeError, ValueError):
                                    pass
                            elif "bbox" in ab and isinstance(ab["bbox"], list) and len(ab["bbox"]) == 4:
                                try:
                                    _ab_raw = [float(v) for v in ab["bbox"]]
                                    fmt = str(ab.get("bbox_format", "")).lower()
                                    _ab_format = fmt if fmt in ("xywh", "xyxy") else None
                                except (TypeError, ValueError):
                                    pass
                        elif ab and isinstance(ab, list) and len(ab) == 4:
                            try:
                                _ab_raw = [float(v) for v in ab]
                                # Pure list: default xywh, no numeric guessing
                            except (TypeError, ValueError):
                                pass

                        for idx, q, reason in group:
                            if q.question_number == qn:
                                _matched_qnums.add(qn)
                                _candidate_student_answer = None
                                if sa and str(sa).lower() not in ("null", "无", "none", ""):
                                    _candidate_student_answer = str(sa)
                                if ab is None:
                                    debug(f"[diag] qwen_no_bbox jid={jid} q={qn}")
                                    _bbox_rejected = True
                                elif _ab_raw is None:
                                    debug(f"[diag] qwen_bbox_invalid jid={jid} q={qn}")
                                    _bbox_rejected = True
                                else:
                                    ax1, ay1, ax2, ay2 = _ab_raw
                                    _q_bbox_list = list(q.bbox) if q.bbox else None
                                    _validated_bbox = None
                                    if _ab_format == "xyxy":
                                        # Explicit xyxy: convert to [x,y,w,h]
                                        w = ax2 - ax1
                                        h = ay2 - ay1
                                        if w > 0 and h > 0:
                                            _validated_bbox = _validate_answer_bbox(
                                                [ax1, ay1, w, h], _q_bbox_list, image_width, image_height
                                            )
                                    else:
                                        # xywh (explicit or pure-list default): validate as-is
                                        _validated_bbox = _validate_answer_bbox(
                                            [ax1, ay1, ax2, ay2], _q_bbox_list, image_width, image_height
                                        )
                                        if _validated_bbox is None and _ab_format is None and ax2 > ax1 and ay2 > ay1:
                                            # Legacy rescue: old Qwen xyxy pure-list when raw xywh fails validation
                                            _validated_bbox = _validate_answer_bbox(
                                                [ax1, ay1, ax2 - ax1, ay2 - ay1],
                                                _q_bbox_list, image_width, image_height,
                                            )
                                    if _validated_bbox:
                                        pending_updates.append((idx, q, _candidate_student_answer, _validated_bbox))
                                        _usable_bbox_written_count += 1
                                    else:
                                        debug(f"[diag] validate_rejected jid={jid} q={qn}")
                                        _bbox_rejected = True
                                break

                    if _parse_error is None:
                        if _missing_question_number:
                            _parse_error = "missing_question_number"
                        elif not _matched_qnums:
                            _parse_error = "no_matching_questions"
                        elif _bbox_rejected:
                            _parse_error = "invalid_answer_bbox"
                        elif _usable_bbox_written_count == 0:
                            _parse_error = "no_usable_boost_result"
                    if _parse_error is None and _usable_bbox_written_count > 0:
                        for idx, q, _candidate_student_answer, _validated_bbox in pending_updates:
                            if _candidate_student_answer is not None:
                                q.student_answer = _candidate_student_answer
                            q.answer_bbox = _validated_bbox
                            q.source = "qwen_boost"
                            boosted_indices.add(idx)
                    # Log questions in group with no matching item in Qwen response
                    for idx, q, reason in group:
                        if q.question_number not in _matched_qnums:
                            debug(f"[diag] parsed_no_answer_area jid={jid} q={q.question_number}")
        except Exception as e:
            _parse_error = str(e)[:200]
            info(f"[BG] Qwen boost parse error: {e}")

        # Write model_call with correct success semantics: True only if API+parse both succeeded
        try:
            pricing = get_active_pricing("aliyun_dashscope", "qwen-vl-max")
            usage_data = result.get("usage", {}) or {}
            if _parse_error is None:
                boost_log = make_log_entry(
                    task_id=jid, provider_name="aliyun_dashscope", model_name="qwen-vl-max",
                    feature_code="qwen_boost_group", trace_id=trace_id,
                    sub_stage="answer_extraction",
                    latency_ms=result.get("latency_ms", 0) or _boost_latency_ms,
                    success=True,
                    input_tokens=usage_data.get("prompt_tokens", 0),
                    output_tokens=usage_data.get("completion_tokens", 0),
                    parent_user_id=parent_id, child_id=child_id,
                    billing_status="billed", pricing=pricing,
                    call_source=_call_source,
                )
            else:
                boost_log = make_log_entry(
                    task_id=jid, provider_name="aliyun_dashscope", model_name="qwen-vl-max",
                    feature_code="qwen_boost_group", trace_id=trace_id,
                    sub_stage="answer_extraction",
                    latency_ms=result.get("latency_ms", 0) or _boost_latency_ms,
                    success=False,
                    parent_user_id=parent_id, child_id=child_id,
                    billing_status="failed", pricing=pricing,
                    call_source=_call_source,
                    error_message=_parse_error,
                )
            _model_calls.append(boost_log)
            _db.save_model_call(boost_log)
        except Exception:
            pass

        if _parse_error is not None:
            continue

    # 标记未boost的为skipped
    for idx, q, reason in boost_candidates:
        if idx not in boosted_indices:
            q.source = "skipped"

    return questions


async def _qwen_bbox_only_retry(
    jid: str,
    questions: list,
    image_bytes: bytes,
    oss_signed_url,
    trace_id: str,
    parent_id: str,
    child_id: str,
    image_width=None,
    image_height=None,
) -> list:
    """Single bbox-only retry per image for questions that have student_answer but no answer_bbox.
    Max 1 Qwen call per image. Returns updated questions list."""
    retry_qs = [
        q for q in questions
        if getattr(q, "student_answer", None)
        and not getattr(q, "answer_bbox", None)
    ]
    if not retry_qs:
        return questions

    q_lines = [
        f"题{q.question_number}: {str(q.question_text)[:40]} | 已知答案: {str(q.student_answer)[:30]}"
        for q in retry_qs
    ]
    prompt = (
        "请定位下列各题的学生作答区域（孩子手写答案所在位置）。\n"
        "注意：只返回作答区域，禁止整题bbox、题干bbox、整页bbox。\n"
        + "\n".join(q_lines)
        + "\n\n输出JSON数组，每项格式：{\"question_number\":题号,\"answer_bbox\":{\"x\":数字,\"y\":数字,\"width\":数字,\"height\":数字}}"
    )

    qwen_vl = QwenVLClient()
    _t0 = time.time()
    result = None
    _exc_msg: str | None = None
    try:
        result = await asyncio.to_thread(
            qwen_vl._call,
            image_bytes=image_bytes if not oss_signed_url else None,
            image_url=oss_signed_url,
            prompt=prompt,
            max_tokens=400,
            timeout=15,
        )
    except Exception as e:
        _exc_msg = str(e)[:200]

    _latency_ms = int((time.time() - _t0) * 1000)
    _call_source = _get_call_source()
    _result_ok = _exc_msg is None and result is not None and bool(result.get("success"))
    _semantic_error: str | None = None
    _usable_bbox_written_count = 0

    if not _result_ok:
        _semantic_error = _exc_msg or str((result or {}).get("error", "api_failed"))[:200]
        debug(f"[diag] bbox_only_retry failed jid={jid} exc={_exc_msg}")
    else:
        try:
            content = result.get("content", "")
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if not json_match:
                _semantic_error = "no_json"
                debug(f"[diag] bbox_only_retry no_json jid={jid}")
            else:
                try:
                    retry_data = json.loads(json_match.group())
                except json.JSONDecodeError:
                    _semantic_error = "bad_json"
                    retry_data = None
                if _semantic_error is not None:
                    pass
                elif not isinstance(retry_data, list):
                    _semantic_error = "json_not_array"
                elif not retry_data:
                    _semantic_error = "empty_json_array"
                else:
                    retry_map: dict = {}
                    _missing_question_number = False
                    pending_bbox_updates = []
                    for item in retry_data:
                        if not isinstance(item, dict):
                            _missing_question_number = True
                            continue
                        qn = item.get("question_number")
                        if qn is None:
                            _missing_question_number = True
                            continue
                        retry_map[qn] = item.get("answer_bbox")

                    _matched_qnums: set = set()
                    _bbox_rejected = False
                    for q in retry_qs:
                        if q.question_number not in retry_map:
                            debug(f"[diag] bbox_only_retry no_bbox q={q.question_number} jid={jid}")
                            continue
                        _matched_qnums.add(q.question_number)
                        ab = retry_map.get(q.question_number)
                        if not ab:
                            debug(f"[diag] bbox_only_retry no_bbox q={q.question_number} jid={jid}")
                            _bbox_rejected = True
                            continue

                        _ab_raw = None
                        if isinstance(ab, dict) and all(k in ab for k in ("x", "y", "width", "height")):
                            try:
                                _ab_raw = [float(ab["x"]), float(ab["y"]), float(ab["width"]), float(ab["height"])]
                            except (TypeError, ValueError):
                                pass
                        elif isinstance(ab, list) and len(ab) == 4:
                            try:
                                _ab_raw = [float(v) for v in ab]
                            except (TypeError, ValueError):
                                pass

                        if _ab_raw is None:
                            debug(f"[diag] bbox_only_retry invalid_format q={q.question_number} jid={jid}")
                            _bbox_rejected = True
                            continue

                        _q_bbox_list = list(q.bbox) if getattr(q, "bbox", None) else None
                        validated = _validate_answer_bbox(_ab_raw, _q_bbox_list, image_width, image_height)
                        if validated:
                            pending_bbox_updates.append((q, validated))
                            _usable_bbox_written_count += 1
                            debug(f"[diag] bbox_only_retry placed q={q.question_number} jid={jid}")
                        else:
                            debug(f"[diag] bbox_only_retry validate_rejected q={q.question_number} jid={jid}")
                            _bbox_rejected = True

                    if _missing_question_number:
                        _semantic_error = "missing_question_number"
                    elif not _matched_qnums:
                        _semantic_error = "no_matching_questions"
                    elif _bbox_rejected:
                        _semantic_error = "invalid_answer_bbox"
                    elif _usable_bbox_written_count == 0:
                        _semantic_error = "no_usable_boost_result"
                    if _semantic_error is None and _usable_bbox_written_count > 0:
                        for q, validated in pending_bbox_updates:
                            q.answer_bbox = validated
        except Exception as e:
            _semantic_error = str(e)[:200]
            debug(f"[diag] bbox_only_retry parse_error jid={jid}: {e}")

    try:
        pricing = get_active_pricing("aliyun_dashscope", "qwen-vl-max")
        usage_data = (result or {}).get("usage", {}) or {}
        retry_log = make_log_entry(
            task_id=jid, provider_name="aliyun_dashscope", model_name="qwen-vl-max",
            feature_code="qwen_bbox_only_retry", trace_id=trace_id,
            sub_stage="bbox_retry",
            latency_ms=_latency_ms, success=_semantic_error is None,
            input_tokens=usage_data.get("prompt_tokens", 0),
            output_tokens=usage_data.get("completion_tokens", 0),
            parent_user_id=parent_id, child_id=child_id,
            billing_status="billed" if _semantic_error is None else "failed",
            pricing=pricing,
            call_source=_call_source,
            error_message=_semantic_error,
        )
        _model_calls.append(retry_log)
        _db.save_model_call(retry_log)
    except Exception:
        pass

    return questions


def _build_test_mode_questions(jid: str) -> list[Question]:
    return [
        Question(
            question_id=f"{jid}-1-0",
            question_number=1,
            question_text="12 + 34 = ?",
            kind="question",
            bbox=[10.0, 10.0, 120.0, 30.0],
            answer_bbox=[70.0, 10.0, 60.0, 30.0],
            visual_description="本地测试模式生成的算术题",
            status=QuestionStatus.completed,
            student_answer="46",
            source="test_fake",
            confidence=1.0,
        )
    ]


# ── OCR bbox enrichment for Qwen-VL questions ────────────────────────────────

LAYOUT_PARAMS = {
    "vertical":   {"conservative_depth": 250, "min_zone_height": 60, "margin": 4},
    "horizontal": {"conservative_depth": 120, "min_zone_height": 20, "margin": 2},
    "unknown":    {"conservative_depth": 180, "min_zone_height": 30, "margin": 2},
}
COL_TOLERANCE = 150  # px, center_x distance for same-column判定


def _extract_digits(text: str) -> str:
    return "".join(c for c in str(text) if c.isdigit())


def _fuzzy_match_digits(ocr_digits: str, answer_digits: str) -> bool:
    """Allow 1 OCR recognition error in digit matching.

    Single-digit answers must match exactly (no fuzzy) to avoid false positives.
    For 2+ digit values, allow length diff ≤ 1 and at most 1 mismatched digit.
    """
    if not ocr_digits or not answer_digits:
        return False
    if ocr_digits == answer_digits:
        return True
    # Only apply fuzzy for values with 2+ digits in both to avoid "3" matching "13"
    if min(len(ocr_digits), len(answer_digits)) < 2:
        return False
    if abs(len(ocr_digits) - len(answer_digits)) <= 1:
        common = sum(1 for a, b in zip(ocr_digits, answer_digits) if a == b)
        if common >= min(len(ocr_digits), len(answer_digits)) - 1:
            return True
    return False


def _classify_layout(q_block: dict) -> str:
    """Classify question layout based on OCR block aspect ratio."""
    w = q_block.get("w", 0)
    h = q_block.get("h", 0)
    if h == 0:
        return "unknown"
    if w > h * 1.5:
        return "horizontal"
    if h > w * 1.5:
        return "vertical"
    return "unknown"


def _is_expression_block(text: str) -> bool:
    """Anchor block must contain arithmetic operators, not be pure digits."""
    return any(c in text for c in "+-x*X/÷=")


def find_question_anchor(question_text: str, ocr_blocks: list) -> dict | None:
    """Find OCR block containing question_text (must contain operator, not pure digits)."""
    q_clean = question_text.replace(" ", "").rstrip("=")
    if not q_clean:
        return None
    for b in ocr_blocks:
        bt = b.get("text", "").replace(" ", "").rstrip("=")
        if q_clean in bt and _is_expression_block(bt):
            return b
    return None


def find_next_in_column(
    current_anchor: dict,
    all_questions: list,
    ocr_blocks: list,
    col_tolerance: int = COL_TOLERANCE,
) -> dict | None:
    """Find next question anchor in same column (strictly below current)."""
    ax, ay, aw, ah = current_anchor["x"], current_anchor["y"], current_anchor["w"], current_anchor["h"]
    ax2 = ax + aw
    cx = ax + aw / 2

    best_anchor = None
    best_y = None

    for q in all_questions:
        qt = (q.get("question_text") or "").replace(" ", "").rstrip("=")
        if not qt:
            continue
        other = find_question_anchor(qt, ocr_blocks)
        if other is None:
            continue
        if other["y"] <= ay:
            continue
        if other["x"] == ax and other["y"] == ay:
            continue

        ox, ow = other["x"], other["w"]
        ox2 = ox + ow
        ocx = ox + ow / 2

        x_overlap = ox < ax2 and ox2 > ax
        center_near = abs(cx - ocx) < col_tolerance

        if x_overlap or center_near:
            if best_y is None or other["y"] < best_y:
                best_y = other["y"]
                best_anchor = other

    return best_anchor


def build_answer_zone(
    anchor: dict,
    next_anchor: dict | None,
    layout: str,
    img_w: int = 1280,
    img_h: int = 1280,
) -> dict | None:
    """Build answer zone bbox. Returns None if zone too short (low confidence)."""
    params = LAYOUT_PARAMS.get(layout, LAYOUT_PARAMS["unknown"])
    ax, ay, aw, ah = anchor["x"], anchor["y"], anchor["w"], anchor["h"]

    x1 = max(0, ax - 120)
    x2 = min(img_w, ax + aw + 120)
    y1 = ay  # start at anchor top (answer block may overlap anchor vertically)

    if next_anchor is not None:
        y2 = next_anchor["y"] - params["margin"]
    else:
        y2 = min(img_h, ay + params["conservative_depth"])

    zone_h = y2 - y1
    if zone_h < params["min_zone_height"]:
        return None  # Low confidence — reject

    return {"x": int(x1), "y": int(y1), "w": int(x2 - x1), "h": int(zone_h)}


def find_answer_in_zone(
    zone: dict,
    digit_blocks: list,
    answer_digits: str,
    layout: str,
    anchor: dict | None = None,
) -> dict | None:
    """Find best answer block within zone. Uses fuzzy digit match."""
    if not answer_digits:
        return None

    zx, zy, zw, zh = zone["x"], zone["y"], zone["w"], zone["h"]

    candidates = [
        b for b in digit_blocks
        if (zx <= b["x"] < zx + zw and zy <= b["y"] < zy + zh
            and _fuzzy_match_digits(_extract_digits(b.get("text", "")), answer_digits))
    ]
    if not candidates:
        return None

    if layout == "vertical":
        return max(candidates, key=lambda b: b["y"])
    if layout == "horizontal":
        max_x = max(b["x"] for b in candidates)
        tied = [b for b in candidates if b["x"] == max_x]
        if len(tied) == 1:
            return tied[0]
        q_cy = (anchor["y"] + anchor["h"] / 2) if anchor else 0
        return min(tied, key=lambda b: abs(b["y"] - q_cy))
    if len(candidates) == 1:
        return candidates[0]
    qcx = (anchor["x"] + anchor["w"] / 2) if anchor else 0
    qcy = (anchor["y"] + anchor["h"] / 2) if anchor else 0
    return min(candidates, key=lambda b: abs(b["x"] - qcx) + abs(b["y"] - qcy))


def _enrich_questions_with_ocr_bbox(
    questions: list,
    ocr_blocks: list,
    *,
    image_width: int = 1280,
    image_height: int = 1280,
) -> list:
    """为 Qwen 提取的题目补充 answer_bbox，从 OCR blocks 中匹配。

    使用 answer zone 策略（Phase B）：
    1. find_question_anchor — 必须含运算符，不能纯数字
    2. find_next_in_column — 找同列下一题锚点构建 zone 边界
    3. build_answer_zone — 按布局参数构建 answer zone
    4. find_answer_in_zone — 在 zone 内按布局取最优候选
    """
    result = []
    for q in questions:
        q = dict(q)
        if q.get("answer_bbox") is not None:
            result.append(q)
            continue

        student_answer = q.get("student_answer") or ""
        answer_digits = _extract_digits(student_answer)
        if not answer_digits:
            result.append(q)
            continue

        question_text = q.get("question_text") or ""

        # Step 1: find question anchor (must contain operator, not pure digits)
        anchor = find_question_anchor(question_text, ocr_blocks)
        if anchor is None:
            result.append(q)
            continue

        ax, ay, aw, ah = anchor["x"], anchor["y"], anchor["w"], anchor["h"]

        # Step 2: stem exclusion for digit_blocks
        stem_right = ax + aw * 0.3
        stem_bottom = ay + ah * 0.3
        digit_blocks = [
            b for b in ocr_blocks
            if _extract_digits(b.get("text", ""))
            and not (b["x"] < stem_right and b["y"] < stem_bottom)
        ]

        # Step 3: classify layout, find next column anchor, build zone
        layout = _classify_layout(anchor)
        next_anchor = find_next_in_column(anchor, questions, ocr_blocks, COL_TOLERANCE)
        zone = build_answer_zone(anchor, next_anchor, layout, image_width, image_height)

        if zone is None:
            result.append(q)
            continue

        # Step 4: find answer in zone
        best_block = find_answer_in_zone(zone, digit_blocks, answer_digits, layout, anchor)

        # Oversized safety gate: reject answer_bbox > 3x question area or > 1024px
        if best_block is not None:
            bw, bh = best_block.get("w", 0), best_block.get("h", 0)
            q_area = aw * ah
            if (q_area > 0 and bw * bh > q_area * 3) or bw > 1024 or bh > 1024:
                best_block = None

        if best_block is not None:
            q["answer_bbox"] = [best_block["x"], best_block["y"], best_block["w"], best_block["h"]]
            if q.get("bbox") is None:
                q["bbox"] = [ax, ay, aw, ah]

        result.append(q)
    return result


def _build_questions_from_raw(
    jid: str,
    raw_questions: list[dict],
    document_classification: dict | None,
    shared_vd: str,
    aid: str,
    page_id: str,
    source_call_id: str,
    parse_cost_per_q: float,
    source: str,
    image_url: str | None,
) -> list[Question]:
    if not should_extract_questions(document_classification):
        return []
    questions: list[Question] = []
    for raw_index, rq in enumerate(raw_questions):
        if _should_drop_raw_question(rq):
            info(f"[BG] Skip non-question item #{raw_index} for {jid}: {str(rq.get('content', ''))[:80]}")
            continue

        qid = f"{jid}-{rq.get('number', raw_index + 1)}-{raw_index}"
        q_text = clean_question_text(
            rq.get("content", f"第{rq.get('number', raw_index + 1)}题"),
            document_classification,
        )
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

        raw_no = rq.get("number", raw_index + 1)
        try:
            q_no = int(raw_no)
        except (ValueError, TypeError):
            q_no = raw_index + 1

        payload = RecognitionQuestionContract(
            question_id=qid,
            question_number=q_no,
            question_text=q_text,
            kind="question",
            question_role=rq.get("question_role"),
            context_text=rq.get("context_text"),
            options=rq.get("options"),
            blank_count=rq.get("blank_count"),
            bbox=_normalize_question_bbox(rq.get("bbox")),
            answer_bbox=_normalize_question_bbox(rq.get("answer_bbox")),
            image_url=image_url or None,
            visual_description=shared_vd,
            status=QuestionStatus.completed.value,
            student_answer=student_ans,
            section_title=section_title,
            section_index=section_index,
            sub_index=sub_index,
            answer_bbox_source=rq.get("answer_bbox_source"),
            answer_bbox_score=rq.get("answer_bbox_score"),
            layout_row_index=rq.get("layout_row_index"),
            layout_column_index=rq.get("layout_column_index"),
            layout_group_index=rq.get("layout_group_index"),
            source=source,
            confidence=normalize_confidence(rq.get("confidence")),
            error_code=None,
        )
        drop_candidate = should_drop_candidate_question(payload.question_text, payload.bbox, document_classification)
        if (
            drop_candidate
            and source == "math_ocr_first"
            and not is_meta_instruction_or_footer_text(payload.question_text)
            and not is_pseudo_or_garbled_question(payload.question_text)
        ):
            # Arithmetic pages can receive an over-broad header region that overlaps the
            # upper oral-calculation rows. math_ocr_first already enforced its own ROI
            # and row/column/group constraints, so keep non-meta arithmetic seeds here.
            drop_candidate = False
        if drop_candidate:
            info(f"[BG] Drop meta-like question #{raw_index} for {jid}: {payload.question_text[:80]}")
            continue
        question = Question(**payload.model_dump())
        questions.append(question)
        _db.create_question_item(
            qid, aid, page_id, q_no, q_text, question.bbox or [], shared_vd[:200],
            source_call_id=source_call_id, parse_cost_allocated_cny=parse_cost_per_q
        )
    return assign_question_sections(questions, document_classification)


def _build_questions_from_structured(
    jid: str,
    raw_questions: list[dict],
    document_classification: dict | None,
    source: str,
    image_url: str | None,
) -> list[Question]:
    questions: list[Question] = []
    for raw_index, rq in enumerate(raw_questions):
        qid = f"{jid}-struct-{rq.get('question_number', raw_index + 1)}-{raw_index}"
        question_text = clean_question_text(rq.get("question_text", ""), document_classification)
        bbox = _normalize_question_bbox(rq.get("bbox"))
        if should_drop_candidate_question(question_text, bbox, document_classification):
            continue
        payload = RecognitionQuestionContract(
            question_id=qid,
            question_number=int(rq.get("question_number") or (raw_index + 1)),
            question_text=question_text,
            kind=rq.get("kind", "question"),
            question_role=rq.get("question_role"),
            context_text=rq.get("context_text"),
            options=rq.get("options"),
            blank_count=rq.get("blank_count"),
            bbox=bbox,
            answer_bbox=None,
            image_url=image_url or None,
            status=QuestionStatus.completed.value,
            student_answer=None,
            section_title=rq.get("section_title"),
            section_index=rq.get("section_index"),
            sub_index=rq.get("sub_index"),
            source=source,
            confidence=normalize_confidence(rq.get("confidence")),
            error_code=None,
        )
        questions.append(Question(**payload.model_dump()))
    return assign_question_sections(questions, document_classification)


async def worker_process_job(jid: str, contents: bytes, file, now: str, parent_id: str, child_id: str):
    """后台异步执行：Qwen-VL 全图识题优先 → OCR+切题回落 → Schema 校验 → 保存"""
    import os as _os_env
    SLA_ENABLED = _os_env.getenv("YOMI_RECOGNITION_10S_SLA", "true").lower() in ("1", "true", "yes")
    SLA_DEADLINE_S = int(_os_env.getenv("YOMI_SYNC_JOB_TIMEOUT_SECONDS", "10"))
    QWEN_FULL_TIMEOUT_S = _get_qwen_full_timeout_seconds()
    OCR_TIMEOUT_S = int(_os_env.getenv("YOMI_GENERAL_OCR_TIMEOUT_SECONDS",
                        _os_env.getenv("YOMI_GENERAL_OCR_TIMEOUT_SECONDS", "3")))
    DISABLE_PER_Q_VISION = _os_env.getenv("YOMI_DISABLE_SYNC_PER_QUESTION_VISION", "true").lower() in ("1", "true", "yes")
    PER_Q_VISION_LIMIT = int(_os_env.getenv("YOMI_PER_QUESTION_VISION_SYNC_LIMIT", "0"))
    BPLUS_ENABLED = _os_env.getenv("YOMI_BPLUS_RECOGNITION", "false").lower() in ("1", "true", "yes")
    BPLUSPLUS_ENABLED = _os_env.getenv("YOMI_BPLUSPLUS_RECOGNITION", "false").lower() in ("1", "true", "yes")
    MATH_OCR_FIRST_ENABLED = _os_env.getenv("YOMI_MATH_OCR_FIRST", "false").lower() in ("1", "true", "yes")
    TEST_FAKE_RECOGNITION = _os_env.getenv("YOMI_TEST_FAKE_RECOGNITION", "false").lower() in ("1", "true", "yes")
    OCR_FIRST_ENABLED = _os_env.getenv("YOMI_OCR_FIRST", "false").lower() in ("1", "true", "yes")
    import uuid as _uuid
    trace_id = _uuid.uuid4().hex
    _log_info(f"job_start jid={jid}", trace_id=trace_id, parent_id=parent_id, child_id=child_id)
    t_start = time.time()

    # 持久化原始图片供 vision 二次路由使用
    import os as _osp
    _osp.makedirs("/tmp/yomi", exist_ok=True)
    with open(f"/tmp/yomi/{jid}.jpg", "wb") as _pf:
        _pf.write(contents)
    try:
        from preprocessor import generate_preprocess_bundle

        recognition_image, _ = generate_preprocess_bundle(
            contents,
            jid,
            source_path=f"/tmp/yomi/{jid}.jpg",
            output_dir=f"/tmp/yomi/preprocess/{jid}",
        )
        _jobs[jid]["recognition_image"] = recognition_image.model_dump()
        _jobs[jid]["preprocess_versions"] = [
            version.model_dump() for version in recognition_image.preprocess_versions
        ]
    except Exception as _pe:
        info(f"[BG] Sidecar preprocess skipped: {_pe}")

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

    result_image_url = oss_signed_url or ""
    if TEST_FAKE_RECOGNITION and not result_image_url:
        import base64 as _base64
        mime_type = getattr(file, "content_type", None) or "image/jpeg"
        result_image_url = f"data:{mime_type};base64,{_base64.b64encode(contents).decode('ascii')}"
    _jobs[jid]["image_url"] = result_image_url

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
        if TEST_FAKE_RECOGNITION and not OCR_FIRST_ENABLED:
            info(f"[BG] Test fake recognition enabled for {jid}")
            questions = _build_test_mode_questions(jid)
            _jobs[jid]["document_classification"] = DocumentClassification(
                doc_family="math_arithmetic",
                subject="math",
                support_level="full",
                route_hint="math_rule_first",
                reason="本地测试模式固定生成数学口算题。",
            ).model_dump()
            for q in questions:
                q.image_url = result_image_url or None
                _db.create_question_item(
                    q.question_id, aid, page_id, q.question_number, q.question_text, q.bbox or [],
                    q.visual_description or ""
                )
            _jobs[jid]["job"].status = JobStatus.schema_validating
            _jobs[jid]["parse_mode"] = "test_fake"
            _jobs[jid]["parser_provider"] = "local"
            _jobs[jid]["parser_model"] = "fake-recognition"
            _jobs[jid]["qwen_parse_call_id"] = ""
            _jobs[jid]["total_parse_cost_cny"] = 0.0
            grading_cost = 0.0
            try:
                grading_cost = await grade_answers(jid, questions, trace_id, parent_id, child_id)
            except Exception as _ge:
                error(f"[BG] Fake recognition grading skipped (non-blocking): {_ge}")
            total_parse_cost += grading_cost
            save_result(jid, questions, now, file, JobStatus.completed)
            debug("[diag] worker_completed jid={jid} status=completed qcount={len(questions)} cost_cny={total_parse_cost:.4f}")
            return

        # ── Phase 0: 图片压缩预处理（加速 Qwen-VL）──
        _img_bytes = contents
        try:
            from PIL import Image
            import io as _io
            _img_pil = Image.open(_io.BytesIO(contents))
            _w, _h = _img_pil.size
            image_width = _w
            image_height = _h
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
            image_width = None
            image_height = None

        
        # ── OCR-first 候选路径（YOMI_OCR_FIRST=true 时启用）──
        if OCR_FIRST_ENABLED:
            info(f"[BG] OCR-first path enabled for {jid}")
            # OCR only (no Qwen-VL full-image call)
            if TEST_FAKE_RECOGNITION:
                # Fake OCR blocks for integration testing (no real API call)
                info(f"[BG] OCR-first fake blocks mode for {jid}")
                ocr_raw_f = {
                    "success": True,
                    "blocks": [
                        {"text": "1. 12 + 34 = ?", "x": 10, "y": 20, "w": 180, "h": 25},
                        {"text": "46", "x": 120, "y": 25, "w": 40, "h": 22},
                        {"text": "2. 56 - 28 = ?", "x": 10, "y": 60, "w": 200, "h": 25},
                        {"text": "28", "x": 130, "y": 65, "w": 40, "h": 22},
                    ],
                    "text": "fake ocr blocks for test",
                    "latency_ms": 0,
                }
            else:
                try:
                    ocr_f = AliyunOCRClient()
                    ocr_raw_f = ocr_f.recognize(contents)
                except Exception as _e:
                    error(f"[BG] OCR-first OCR error: {_e}")
                    ocr_raw_f = {"success": False, "blocks": [], "text": "", "latency_ms": 0}

            ocr_blocks = ocr_raw_f.get("blocks", [])
            ocr_latency = ocr_raw_f.get("latency_ms", 0)
            _jobs[jid]["ocr_blocks"] = ocr_blocks
            _jobs[jid]["ocr_block_source"] = "aliyun_general_ocr"

            document_classification = _build_document_classification(
                ocr_blocks, [],
                image_width=image_width,
                image_height=image_height,
            )
            _jobs[jid]["document_classification"] = document_classification

            _jobs[jid]["job"].status = JobStatus.cutting

            if ocr_blocks:
                questions, boost_candidates = _ocr_first_extract_questions(
                    jid, ocr_blocks, document_classification,
                    image_width, image_height, result_image_url,
                    aid, page_id,
                )
                info(f"[BG] OCR-first: {len(ocr_blocks)} blocks -> {len(questions)} questions, {len(boost_candidates)} boost candidates")

                if boost_candidates:
                    _jobs[jid]["job"].status = JobStatus.vision_reviewing
                    questions = await _qwen_boost_group(
                        jid, questions, boost_candidates, _img_bytes,
                        oss_signed_url, document_classification,
                        trace_id, parent_id, child_id,
                        image_width, image_height,
                    )
                    info(f"[BG] Qwen boost completed for {jid}")
                    # bbox-only retry: one call per image for questions with student_answer but no answer_bbox
                    questions = await _qwen_bbox_only_retry(
                        jid, questions, _img_bytes, oss_signed_url,
                        trace_id, parent_id, child_id,
                        image_width, image_height,
                    )
            else:
                info(f"[BG] OCR-first: 0 blocks, no questions")
                questions = []

            # Validate answer_bbox on all questions
            for q in questions:
                if hasattr(q, "answer_bbox") and q.answer_bbox:
                    q.answer_bbox = _validate_answer_bbox(
                        q.answer_bbox, q.bbox, image_width, image_height
                    )

            questions = assign_question_sections(questions, document_classification)

            # Zero questions guard
            if len(questions) == 0:
                final_status = JobStatus.needs_review
            else:
                final_status = JobStatus.completed

            _jobs[jid]["job"].status = JobStatus.schema_validating
            _jobs[jid]["parse_mode"] = "ocr_first"
            _jobs[jid]["parser_provider"] = "aliyun_ocr"
            _jobs[jid]["parser_model"] = "ocr_general"
            _jobs[jid]["qwen_parse_call_id"] = f"ocr_first_{jid}"
            _jobs[jid]["total_parse_cost_cny"] = total_parse_cost

            document_classification = _build_document_classification(
                ocr_blocks, questions,
                image_width=image_width, image_height=image_height,
            )
            document_classification = _update_document_classification_stats(
                document_classification, questions,
                image_width=image_width, image_height=image_height,
            )
            questions = _drop_questions_for_conservative_page_type(questions, document_classification)
            _jobs[jid]["document_classification"] = document_classification

            # Build grading_units for grade_answers
            grading_units = build_grading_units(
                questions,
                image_width=image_width,
                image_height=image_height,
            )
            apply_grading_unit_metadata(questions, grading_units, ocr_blocks)
            _jobs[jid]["grading_units"] = grading_units

            # Grading — preserve Qwen answer_bbox through grading
            _pre_grade_bbox = {
                getattr(q, "question_id", str(i)): getattr(q, "answer_bbox", None)
                for i, q in enumerate(questions)
                if getattr(q, "answer_bbox", None)
            }
            grading_cost = 0.0
            try:
                grading_cost = await grade_answers(jid, questions, grading_units, contents, trace_id, parent_id, child_id)
            except Exception as _ge:
                error(f"[BG] OCR-first grading failed: {_ge}")
            # Restore answer_bbox if grading wiped it (JSON parse errors, etc.)
            for q in questions:
                qid = getattr(q, "question_id", None)
                if qid and _pre_grade_bbox.get(qid) and not getattr(q, "answer_bbox", None):
                    q.answer_bbox = _pre_grade_bbox[qid]
                    debug(f"[diag] preserved answer_bbox for qid={qid}")
            total_parse_cost += grading_cost

            _with_g = sum(1 for q in questions if getattr(q, "is_correct", None) is not None)
            _with_sa = sum(1 for q in questions if getattr(q, "student_answer", None))

            if len(questions) > 0 and _with_sa == 0 and _with_g == 0:
                final_status = JobStatus.needs_review

            gated_status = _apply_document_support_gate(
                final_status, questions, document_classification,
                grading_count=_with_g, student_answer_count=_with_sa,
            )
            if gated_status != final_status:
                final_status = gated_status

            save_result(jid, questions, now, file, final_status)
            debug(f"[diag] ocr_first_completed jid={jid} status={final_status.value} qcount={len(questions)}")
            return


        # ── Phase 1: Qwen-VL 全图 + 通用 OCR 并行 ──
        _jobs[jid]["job"].status = JobStatus.ocr_running
        use_qwen_vl = False
        math_ocr_first_used = False
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
        _jobs[jid]["ocr_blocks"] = ocr_blocks
        export_ocr_blocks_if_enabled(jid, ocr_blocks)
        _jobs[jid]["ocr_block_source"] = "aliyun_general_ocr"
        image_payload = _jobs[jid].get("recognition_image") or {}
        image_width = image_payload.get("width")
        image_height = image_payload.get("height")
        document_classification = _build_document_classification(
            ocr_blocks,
            [],
            image_width=image_width,
            image_height=image_height,
        )
        _jobs[jid]["document_classification"] = document_classification
        info(f"[BG] Parallel done: Qwen={'OK' if qwen_result.get('success') else 'FAIL'}({qwen_latency}ms) OCR={len(ocr_blocks)}blocks({ocr_latency}ms)")

        # ── OCR call logging (always log if OCR was called) ──
        if ocr_latency > 0:
            _ocr_log = make_log_entry(
                task_id=jid, provider_name="aliyun_ocr", model_name="ocr_general",
                feature_code="ocr_general",
                trace_id=trace_id, latency_ms=ocr_latency,
                success=len(ocr_blocks) > 0,
                parent_user_id=parent_id, child_id=child_id,
                billing_status="billed", image_count=1, credit_cost=0.004,
                call_source=_get_call_source(),
                blocks_count=len(ocr_blocks),
            )
            _model_calls.append(_ocr_log)
            _db.save_model_call(_ocr_log)

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
            billing_status="billed" if qwen_result["success"] else "failed",
            pricing=pricing,
            blocks_count=len(qwen_result.get("questions", [])),
        )
        qwen_parse_call_id = _log["id"]
        total_parse_cost += _log.get("cost_cny", 0.0)
        _model_calls.append(_log)
        _db.save_model_call(_log)

        if qwen_result["success"] and qwen_result["questions"]:
            raw_questions = qwen_result["questions"]
            shared_vd = qwen_result.get("raw_content", "Qwen-VL 全图识别结果")
            parse_cost_per_q = total_parse_cost / len(raw_questions) if raw_questions else 0.0
            questions = _build_questions_from_raw(
                jid=jid,
                raw_questions=raw_questions,
                document_classification=document_classification,
                shared_vd=shared_vd,
                aid=aid,
                page_id=page_id,
                source_call_id=qwen_parse_call_id,
                parse_cost_per_q=parse_cost_per_q,
                source="qwen_vl",
                image_url=result_image_url,
            )
            # Enrich Qwen questions with OCR bboxes (Qwen-VL returns no coordinates)
            if ocr_blocks:
                questions = _enrich_questions_with_ocr_bbox(questions, ocr_blocks)
            question_count = len(questions)
            if question_count >= 5:
                use_qwen_vl = True
            info(f"[BG] Qwen-VL extracted {question_count} questions in {qwen_latency}ms")
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

            # ── 数学 OCR-first 路径（灰度） ──
            if MATH_OCR_FIRST_ENABLED:
                doc_family = str(document_classification.get("doc_family", "")).strip()
                page_type = str(document_classification.get("page_type", "unknown")).strip()
                math_families = {"math_arithmetic", "math_vertical", "math_comparison_logic", "math_word_problem"}
                blocked_types = {"non_homework", "cover_or_instruction_page"}
                if doc_family in math_families and page_type not in blocked_types:
                    info(f"[BG] Math OCR-first route: doc_family={doc_family}")
                    _math_result = math_ocr_first_extract(ocr_blocks, document_classification)
                    _jobs[jid]["roi_result"] = _math_result["stats"].get("roi_result")
                    if _math_result["quality_gate_passed"]:
                        _math_questions = _build_questions_from_raw(
                            jid=jid,
                            raw_questions=_math_result["questions"],
                            document_classification=document_classification,
                            shared_vd="Math OCR-first 识别结果",
                            aid=aid,
                            page_id=page_id,
                            source_call_id=f"mathocr_{jid}",
                            parse_cost_per_q=0.0,
                            source="math_ocr_first",
                            image_url=result_image_url,
                        )
                        if _math_questions:
                            use_qwen_vl = True
                            math_ocr_first_used = True
                            questions = _math_questions
                            qwen_parse_call_id = f"mathocr_{jid}"
                            info(
                                f"[BG] Math OCR-first: {len(questions)}q, "
                                f"answer_bbox_ratio={_math_result['stats'].get('answer_bbox_ratio', 0)}"
                            )
                        else:
                            info("[BG] Math OCR-first produced 0 questions after normalization")
                    else:
                        info(f"[BG] Math OCR-first quality gate failed: {_math_result.get('reason', 'unknown')}")

            if use_qwen_vl:
                pass
            elif _is_homework_image(ocr_blocks):
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
                    questions = _build_questions_from_raw(
                        jid=jid,
                        raw_questions=_all_qs,
                        document_classification=document_classification,
                        shared_vd="B++ 局部识别结果",
                        aid=aid,
                        page_id=page_id,
                        source_call_id=f"bpp_{jid}",
                        parse_cost_per_q=0.0,
                        source="bplusplus_qwen",
                        image_url=result_image_url,
                    )
                    question_count = len(questions)
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

        # ── OCR fallback: use pre-fetched blocks (already obtained in parallel) ──
        if not use_qwen_vl:
            _jobs[jid]["job"].status = JobStatus.cutting

            if ocr_blocks:
                if should_extract_questions(document_classification):
                    filtered_blocks = filter_ocr_blocks_for_question_region(ocr_blocks, document_classification)
                    _pos_blocks = [
                        {"text": b["text"], "pos": [b.get("x", 0), b.get("y", 0), b.get("w", 0), b.get("h", 0)]}
                        for b in filtered_blocks
                    ]
                    cut_results = cut_to_questions(_pos_blocks)
                    questions = []
                    for i, cq in enumerate(cut_results):
                        cleaned_text = clean_question_text(cq["question_text"], document_classification)
                        qid = f"{jid}-{cq['question_number']}-{i}"
                        q_bbox = _normalize_question_bbox(cq.get("bbox"))
                        if should_drop_candidate_question(cleaned_text, q_bbox, document_classification):
                            continue
                        payload = RecognitionQuestionContract(
                            question_id=qid,
                            question_number=cq["question_number"],
                            question_text=cleaned_text,
                            kind="question",
                            bbox=q_bbox,
                            answer_bbox=None,
                            image_url=result_image_url or None,
                            visual_description=None,
                            status=QuestionStatus.completed.value,
                            student_answer=None,
                            source="ocr_cut",
                            confidence=None,
                            error_code=None,
                        )
                        questions.append(Question(**payload.model_dump()))
                        _db.create_question_item(qid, aid, page_id, cq["question_number"], cleaned_text, q_bbox or [])
                    questions = assign_question_sections(questions, document_classification)
                    info(
                        f"[BG] OCR+Cut: {len(ocr_blocks)} blocks → "
                        f"{len(filtered_blocks)} filtered blocks → {len(questions)} questions"
                    )
                elif should_extract_structural_questions(document_classification):
                    structured_questions = extract_structured_questions_from_ocr(
                        ocr_blocks,
                        document_classification,
                        image_width=image_width,
                        image_height=image_height,
                    )
                    questions = _build_questions_from_structured(
                        jid,
                        structured_questions,
                        document_classification,
                        source="ocr_structured",
                        image_url=result_image_url,
                    )
                    info(
                        f"[BG] OCR+Structured: {len(ocr_blocks)} blocks → "
                        f"{len(structured_questions)} structured payloads → {len(questions)} questions"
                    )
                else:
                    info(
                        f"[BG] OCR+Cut skipped for {jid}: "
                        f"page_type={document_classification.get('page_type', 'unknown')}"
                    )
                    questions = []
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
                                billing_status="billed" if vl_result["success"] else "failed",
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
                                    billing_status="billed" if vl_result["success"] else "failed",
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
        elif math_ocr_first_used:
            info("[BG] Math OCR-first mode: skipping per-question vision review")
        else:
            info("[BG] Qwen-VL full-page mode: skipping per-question vision review")

        # Stage: schema_validating — 基准 Table 21
        _jobs[jid]["job"].status = JobStatus.schema_validating
        _ocr_only = not use_qwen_vl and DISABLE_PER_Q_VISION
        has_failures = False
        questions = assign_question_sections(questions, document_classification)
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

        document_classification = _build_document_classification(
            ocr_blocks,
            questions,
            image_width=image_width,
            image_height=image_height,
        )
        document_classification = _update_document_classification_stats(
            document_classification,
            questions,
            image_width=image_width,
            image_height=image_height,
        )
        questions = _drop_questions_for_conservative_page_type(questions, document_classification)
        document_classification = _update_document_classification_stats(
            document_classification,
            questions,
            image_width=image_width,
            image_height=image_height,
        )
        _jobs[jid]["document_classification"] = document_classification
        grading_units = build_grading_units(
            questions,
            image_width=image_width,
            image_height=image_height,
        )
        apply_grading_unit_metadata(questions, grading_units, ocr_blocks)
        _jobs[jid]["grading_units"] = grading_units

        final_status = _decide_final_status(questions, has_failures, _ocr_only)
        # ── Quality gate: OCR-only 路径缺少答案识别 → needs_review ──
        if _ocr_only:
            # OCR 成功切出题目 → 先过作业图预筛
            if _is_homework_image(ocr_blocks):
                info(f"[BG] OCR-only homework image (Qwen failed) → needs_review for {jid}")
            else:
                info(f"[BG] OCR-only non-homework image → needs_review for {jid}")
        # 存储解析元数据供 save_result 写入 parse_jobs
        if math_ocr_first_used:
            _jobs[jid]["parse_mode"] = "math_ocr_first"
            _jobs[jid]["parser_provider"] = "local"
            _jobs[jid]["parser_model"] = "math_ocr_first"
        else:
            _jobs[jid]["parse_mode"] = "qwen_vl"
            _jobs[jid]["parser_provider"] = "aliyun_dashscope"
            _jobs[jid]["parser_model"] = "qwen-vl-max"
        _jobs[jid]["qwen_parse_call_id"] = qwen_parse_call_id
        _jobs[jid]["total_parse_cost_cny"] = total_parse_cost
        # ── Stage: grading ── DeepSeek 批量判对错 ──
        grading_cost = 0.0
        try:
            grading_cost = await grade_answers(jid, questions, grading_units, contents, trace_id, parent_id, child_id)
        except Exception as _ge:
            error(f"[BG] Grading stage failed (non-blocking): {_ge}")
        total_parse_cost += grading_cost
        # ── Post-grading quality gate: 有题但无可判定结果 → needs_review ──
        _with_g = sum(1 for q in questions if (getattr(q, "is_correct", None) is not None) or (isinstance(q, dict) and q.get("is_correct") is not None))
        _with_sa = sum(1 for q in questions if (getattr(q, "student_answer", None)) or (isinstance(q, dict) and q.get("student_answer")))
        if len(questions) > 0 and _with_sa == 0 and _with_g == 0 and final_status not in (JobStatus.needs_review, JobStatus.low_confidence):
            if document_classification.get("support_level") == "full":
                info(f"[BG] Quality gate: {len(questions)}q but 0 answers/0 graded -> needs_review for {jid}")
                final_status = JobStatus.needs_review
            else:
                info(
                    f"[BG] Quality gate: {len(questions)}q but 0 answers/0 graded on "
                    f"{document_classification.get('doc_family', 'unknown')} -> low_confidence for {jid}"
                )
                final_status = JobStatus.low_confidence
        gated_status = _apply_document_support_gate(
            final_status,
            questions,
            document_classification,
            grading_count=_with_g,
            student_answer_count=_with_sa,
        )
        if gated_status != final_status:
            info(
                f"[BG] Document gate: {document_classification.get('doc_family', 'unknown')} "
                f"({document_classification.get('support_level', 'partial')}) -> {gated_status.value} for {jid}"
            )
            final_status = gated_status
        # ── Zero-question safety gate: 0 questions must never complete ──
        if len(questions) == 0 and final_status not in (JobStatus.failed, JobStatus.needs_review, JobStatus.low_confidence):
            _log_info(f"[BG] Zero-question gate: 0q -> needs_review for {jid}")
            final_status = JobStatus.needs_review
        # ── Generate overlay marks for frontend grading overlay ──
        overlay_marks = []
        for q in questions:
            if _get_question_field(q, "kind", "question") != "question":
                continue
            mark = build_overlay_mark(q)
            if mark is not None:
                overlay_marks.append({
                    "question_id": mark.question_id,
                    "mark_type": mark.mark_type,
                    "mark_bbox": mark.mark_bbox,
                    "question_number": _get_question_field(q, "question_number", 0),
                })
        _jobs[jid]["overlay"] = overlay_marks
        # ── Generate group boxes from grading units ──
        group_boxes = build_group_boxes(grading_units)
        _jobs[jid]["group_boxes"] = group_boxes
        info(f"[BG] overlay={len(overlay_marks)} group_boxes={len(group_boxes)} for {jid}")
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
            call_source=_get_call_source(),
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
