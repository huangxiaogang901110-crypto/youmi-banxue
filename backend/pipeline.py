"""
管线核心函数。
从 main.py 抽出，不改行为。
"""
import asyncio
import json
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
from general_ocr_client import GeneralOCRClient
from question_cutter import cut_to_questions
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
    """
    # 只对有 student_answer 的题判题
    gradable = [(i, q) for i, q in enumerate(questions)
                 if hasattr(q, "student_answer") and q.student_answer or
                 isinstance(q, dict) and q.get("student_answer")]
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


async def worker_process_job(jid: str, contents: bytes, file, now: str, parent_id: str, child_id: str):
    """后台异步执行：Qwen-VL 全图识题优先 → OCR+切题回落 → Schema 校验 → 保存"""
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
        # ── Phase 1: Qwen-VL 全图识题优先 ──
        _jobs[jid]["job"].status = JobStatus.enhancing
        qwen_vl = QwenVLClient()
        use_qwen_vl = False
        qwen_parse_call_id = ""  # 默认空，OCR 回落时不填
        questions = []

        if qwen_vl._available():
            _jobs[jid]["job"].status = JobStatus.ocr_running  # 复用状态表示"识别中"
            info(f"[BG] Qwen-VL extracting questions for {jid}...")
            qwen_result = qwen_vl.extract_questions(
                image_bytes=contents if not oss_signed_url else None,
                image_url=oss_signed_url,
            )
            qwen_latency = int((time.time() - t_start) * 1000)
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
                # 阈值：Qwen-VL 返回 <5 题 → 不可靠，回落 OCR+切题
                if question_count < 5:
                    print(f"[BG] Qwen-VL only got {question_count} questions, falling back to OCR+cutting", flush=True)
                else:
                    use_qwen_vl = True
                info(f"[BG] Qwen-VL extracted {question_count} questions in {qwen_latency}ms")

                # ── 通用 OCR 坐标融合（灰度开关 YOMI_USE_GENERAL_OCR）──
                _use_gen_ocr = _osp.getenv("YOMI_USE_GENERAL_OCR", "false").lower() == "true"
                if _use_gen_ocr:
                    try:
                        gen_ocr = GeneralOCRClient()
                        if gen_ocr._available():
                            go_result = gen_ocr.recognize(contents)
                            if go_result["success"] and go_result["blocks"]:
                                ocr_blocks = go_result["blocks"]
                                info(f"[BG] General OCR: {len(ocr_blocks)} blocks in {go_result['latency_ms']}ms")
                                # ── Fusion: L1 text match → L2 section → L3 spatial ──
                                import re as _re_fusion
                                _NOISE_KW = set(["姓名","班级","年级","日期","打卡日期","建议用时","实际用时","老师改正","错题改正","得分","评语","批注","页码","页眉","页脚","练习册","课时","课间活动","附记","装订线","家长签字","检查人","学校","出版社","单元","册别","ISBN","直接写出得数","填一填","列竖式计算","注意划出","易混易错","照样子","任务一","任务二"])
                                # 过滤噪声 blocks
                                _valid_blocks = [b for b in ocr_blocks if not any(kw in b["text"] for kw in _NOISE_KW) and len(b["text"].strip()) > 1]
                                _used = set()
                                for _i, rq in enumerate(raw_questions):
                                    _qt = rq.get("content", "") or ""
                                    _cat = "meta" if any(kw in _qt for kw in _NOISE_KW) else "text"
                                    if not _qt.strip() or _cat == "meta":
                                        continue
                                    _si = rq.get("section_index") or 1
                                    _sub = rq.get("sub_index") or _i + 1
                                    # L1: digit/char overlap
                                    _best_s, _best_bi = 0.0, []
                                    _n1 = set(_re_fusion.findall(r'\d+', _qt))
                                    _e1 = set(_re_fusion.findall(r'[a-zA-Z]{2,}', _qt.lower()))
                                    for _bi, _b in enumerate(_valid_blocks):
                                        if _bi in _used: continue
                                        _bt = _b["text"]
                                        _ns = len(_n1 & set(_re_fusion.findall(r'\d+', _bt))) / len(_n1) if _n1 else 0
                                        _es = len(_e1 & set(_re_fusion.findall(r'[a-zA-Z]{2,}', _bt.lower()))) / len(_e1) if _e1 else 0
                                        _s = max(_ns, _es)
                                        if _s > _best_s:
                                            _best_s, _best_bi = _s, [_bi]
                                    if _best_s >= 0.3 and _best_bi:
                                        _bxs = [[_valid_blocks[bi]["x"],_valid_blocks[bi]["y"],_valid_blocks[bi]["w"],_valid_blocks[bi]["h"]] for bi in _best_bi]
                                        _bx = [min(x[0] for x in _bxs), min(x[1] for x in _bxs), max(x[0]+x[2] for x in _bxs)-min(x[0] for x in _bxs), max(x[1]+x[3] for x in _bxs)-min(x[1] for x in _bxs)]
                                        for bi in _best_bi: _used.add(bi)
                                        rq["bbox"] = _bx
                                        # answer_bbox: last block in matched set
                                        _ab = _bxs[-1]
                                        if _ab[2]*_ab[3] < 500000 and _ab[2] > 0 and _ab[3] > 0:
                                            rq["answer_bbox"] = _ab[:]
                                    # L2: section spatial fallback (for unmatched questions)
                                    elif _si and _sub:
                                        _sec_blocks = [_b for _bi,_b in enumerate(_valid_blocks) if _bi not in _used]
                                        if _sec_blocks:
                                            _ys = sorted(set(_b["y"] for _b in _sec_blocks))
                                            _per = (_ys[-1]-_ys[0])/max(len(raw_questions),1) if len(_ys)>1 else 30
                                            _y0 = _ys[0] + _per*(_sub-1)
                                            _matched = [_b for _b in _sec_blocks if _y0 <= _b["y"] < _y0+_per]
                                            if _matched:
                                                _bxs = [[_b["x"],_b["y"],_b["w"],_b["h"]] for _b in _matched]
                                                _bx = [min(x[0] for x in _bxs), min(x[1] for x in _bxs), max(x[0]+x[2] for x in _bxs)-min(x[0] for x in _bxs), max(x[1]+x[3] for x in _bxs)-min(x[1] for x in _bxs)]
                                                rq["bbox"] = _bx
                                info(f"[BG] General OCR fusion: {len(_used)}/{len(_valid_blocks)} blocks matched to {question_count} questions")
                            else:
                                warning(f"[BG] General OCR returned no blocks, using Qwen-VL zero bbox")
                        else:
                            warning(f"[BG] General OCR not available (no AK/SK), using Qwen-VL zero bbox")
                    except Exception as _goe:
                        error(f"[BG] General OCR exception: {_goe}, falling back to Qwen-VL zero bbox")

                # 分组展示用：用 raw_content 作为视觉描述
                shared_vd = qwen_result.get("raw_content", f"Qwen-VL 全图识别，共 {question_count} 题")

                # 按题数平摊 Qwen-VL 解析成本
                parse_cost_per_q = total_parse_cost / question_count if question_count > 0 else 0.0

                for i, rq in enumerate(raw_questions):
                    qid = f"{jid}-{rq.get('number', i+1)}-{i}"
                    q_text = rq.get("content", f"第{rq.get('number', i+1)}题")
                    # 安全提取 student_answer：null/None/空字符串视为无答案
                    student_ans = rq.get("student_answer")
                    if student_ans is None or (isinstance(student_ans, str) and not student_ans.strip()):
                        student_ans = None
                    # 安全提取分组字段
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
                    # 安全解析题号：Qwen-VL 可能返回非数字（如 "VOCABULARY 1"）
                    raw_no = rq.get("number", i+1)
                    try:
                        q_no = int(raw_no)
                    except (ValueError, TypeError):
                        q_no = i + 1
                    questions.append(Question(
                        question_id=qid,
                        question_number=q_no,
                        question_text=q_text,
                        bbox=[0, 0, 0, 0],  # 全图识题无精确 bbox
                        visual_description=shared_vd,
                        status=QuestionStatus.completed,
                        student_answer=student_ans,
                        section_title=section_title,
                        section_index=section_index,
                        sub_index=sub_index,
                    ))
                    _db.create_question_item(qid, aid, page_id, q_no, q_text, [0, 0, 0, 0], shared_vd[:200],
                                              source_call_id=qwen_parse_call_id, parse_cost_allocated_cny=parse_cost_per_q)
            else:
                error(f"[BG] Qwen-VL failed: {qwen_result.get('error', 'no questions')}, falling back to OCR")

        # ── OCR 回落（Qwen-VL 失败或不可用时）──
        if not use_qwen_vl:
            _jobs[jid]["job"].status = JobStatus.ocr_running
            ocr = AliyunOCRClient()
            result = ocr.recognize(contents)
            extracted = ocr.extract_text_and_blocks(result)
            latency_ms = int((time.time() - t_start) * 1000)

            _log = make_log_entry(
                task_id=jid,
                provider_name="aliyun_ocr",
                model_name="ocr_api20210707",
                feature_code="ocr",
                trace_id=trace_id,
                latency_ms=latency_ms,
                success=True,
                parent_user_id=parent_id,
                child_id=child_id,
                request_id=result.get("RequestId", ""),
                billing_status="free_tier",
                blocks_count=len(extracted["blocks"]),
            )
            _model_calls.append(_log)
            _db.save_model_call(_log)

            # Stage: cutting — 智能切题（题号规则 + 版面规则）
            _jobs[jid]["job"].status = JobStatus.cutting
            cut_results = cut_to_questions(extracted["blocks"])
            questions = []
            for i, cq in enumerate(cut_results):
                qid = f"{jid}-{cq['question_number']}-{i}"
                questions.append(Question(
                    question_id=qid,
                    question_number=cq["question_number"],
                    question_text=cq["question_text"],
                    bbox=cq["bbox"],
                    visual_description=None,
                    status=QuestionStatus.completed,
                    student_answer=None,
                ))
                _db.create_question_item(qid, aid, page_id, cq["question_number"], cq["question_text"], cq["bbox"])
            info(f"[BG] OCR+Cut: {len(extracted['blocks'])} blocks → {len(questions)} questions")

        # Stage: vision_reviewing - Qwen-VL 逐题复审（仅 OCR 回落路径）
        if not use_qwen_vl:
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
                            break  # 进延后队列，不阻塞其他题
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
        else:
            info("[BG] Qwen-VL full-page mode: skipping per-question vision review")

        # ── 延后队列处理（基准 §9/17 — 429 独立队列）────────
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

        # Stage: schema_validating — 基准 Table 21
        _jobs[jid]["job"].status = JobStatus.schema_validating
        has_failures = False
        for q in questions:
            if q.status == QuestionStatus.failed:
                has_failures = True
                continue
            # Phase 0 轻量校验：题目文本 + 视觉描述完整性
            if not q.question_text or len(q.question_text.strip()) < 2:
                q.status = QuestionStatus.failed
                has_failures = True
            elif q.visual_description is None:
                q.status = QuestionStatus.failed
                has_failures = True

        final_status = JobStatus.needs_review if has_failures else JobStatus.completed
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
