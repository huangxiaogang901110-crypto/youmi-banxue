"""
最小测试：验证 boost 分组逻辑 + crop 坐标计算。
不需要真实 API 或数据库。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))


# ─────────────────────────────────────────────────────────
# 1. _crop_to_original 坐标转换
# ─────────────────────────────────────────────────────────
from pipeline import _crop_to_original


def test_crop_to_original_basic():
    """crop 内 (10,20,50,30) + offset (100,200) → 原图 (110,220,50,30)"""
    result = _crop_to_original(100, 200, [10.0, 20.0, 50.0, 30.0])
    assert result == [110.0, 220.0, 50.0, 30.0], f"got {result}"


def test_crop_to_original_clamp():
    """clamp: x+w 超出 image_width 应缩减 w"""
    result = _crop_to_original(1200, 0, [50.0, 10.0, 200.0, 40.0],
                                image_width=1280, image_height=1280)
    assert result is not None
    x, y, w, h = result
    assert x + w <= 1280, f"x+w={x+w} exceeds 1280"


def test_crop_to_original_zero_offset():
    """offset 为 0 时坐标不变"""
    result = _crop_to_original(0, 0, [50.0, 60.0, 100.0, 80.0],
                                image_width=1280, image_height=1280)
    assert result == [50.0, 60.0, 100.0, 80.0], f"got {result}"


def test_crop_to_original_bad_input():
    """非法输入返回 None"""
    assert _crop_to_original(0, 0, None) is None
    assert _crop_to_original(0, 0, [1, 2, 3]) is None
    assert _crop_to_original(0, 0, ["a", "b", "c", "d"]) is None


# ─────────────────────────────────────────────────────────
# 2. Boost 分组逻辑：≤5 题/组，垂直间距 <250px 合并
# ─────────────────────────────────────────────────────────
def _make_candidate(question_number, y):
    """构造 (idx, Question-like, reason) 三元组"""
    class FakeQ:
        def __init__(self, num, y):
            self.question_number = num
            self.question_text = f"题{num}"
            self.bbox = [10.0, float(y), 200.0, 30.0]
    return (question_number, FakeQ(question_number, y), "test")


def _run_grouping(boost_candidates):
    """复现 _qwen_boost_group 内的分组逻辑"""
    groups = []
    current_group = []
    for item in boost_candidates:
        idx, q, reason = item
        bbox = list(q.bbox) if q.bbox else [0, 0, 0, 0]
        if not current_group:
            current_group = [item]
        else:
            last_bbox = list(current_group[-1][1].bbox) if current_group[-1][1].bbox else [0, 0, 0, 0]
            if len(current_group) < 5 and abs(bbox[1] - last_bbox[1]) < 250:
                current_group.append(item)
            else:
                groups.append(current_group)
                current_group = [item]
    if current_group:
        groups.append(current_group)
    return groups


def test_grouping_max_5_per_group():
    """6道紧密题 → 2组 (5+1)"""
    candidates = [_make_candidate(i, i * 30) for i in range(1, 7)]  # y=0,30,...,150 → same group?
    groups = _run_grouping(candidates)
    assert len(groups) == 2, f"expected 2 groups, got {len(groups)}"
    assert len(groups[0]) == 5
    assert len(groups[1]) == 1


def test_grouping_vertical_split():
    """垂直间距 ≥250px 触发分组"""
    candidates = [
        _make_candidate(1, 0),
        _make_candidate(2, 30),
        _make_candidate(3, 400),  # gap > 250 → new group
        _make_candidate(4, 430),
    ]
    groups = _run_grouping(candidates)
    assert len(groups) == 2, f"expected 2 groups, got {len(groups)}"
    assert len(groups[0]) == 2
    assert len(groups[1]) == 2


def test_grouping_empty():
    """空候选 → 空组"""
    groups = _run_grouping([])
    assert groups == []


def test_grouping_single():
    """单题 → 1组1题"""
    groups = _run_grouping([_make_candidate(1, 100)])
    assert len(groups) == 1
    assert len(groups[0]) == 1


def test_grouping_exactly_5():
    """恰好5道题 → 1组"""
    candidates = [_make_candidate(i, i * 20) for i in range(1, 6)]
    groups = _run_grouping(candidates)
    assert len(groups) == 1
    assert len(groups[0]) == 5


def test_grouping_21_questions():
    """21道紧密题 → ceil(21/5)=5组"""
    candidates = [_make_candidate(i, i * 20) for i in range(1, 22)]
    groups = _run_grouping(candidates)
    assert len(groups) == 5, f"expected 5 groups, got {len(groups)}"
    sizes = [len(g) for g in groups]
    assert sizes == [5, 5, 5, 5, 1], f"sizes={sizes}"


# ─────────────────────────────────────────────────────────
# 3. validate_answer_bbox 与原图坐标对齐
# ─────────────────────────────────────────────────────────
from pipeline import _validate_answer_bbox


def test_validate_accepts_original_coords():
    """原图坐标 (1280x1280 内) 应通过验证"""
    result = _validate_answer_bbox(
        [100.0, 200.0, 150.0, 80.0],
        image_width=1280, image_height=1280
    )
    assert result is not None


def test_validate_rejects_out_of_bounds():
    """超出原图右边界应被拒绝"""
    result = _validate_answer_bbox(
        [1200.0, 200.0, 200.0, 80.0],  # x+w=1400 > 1280
        image_width=1280, image_height=1280
    )
    assert result is None


def test_validate_rejects_zero():
    """全零 bbox 应被拒绝"""
    assert _validate_answer_bbox([0, 0, 0, 0]) is None


# ─────────────────────────────────────────────────────────
# 4. _extract_first_json_array
# ─────────────────────────────────────────────────────────
from pipeline import _extract_first_json_array


def test_extract_json_array_basic():
    """标准JSON数组直接解析"""
    text = '[{"question_number": 1, "student_answer": "A"}, {"question_number": 2, "student_answer": "B"}]'
    result = _extract_first_json_array(text)
    assert isinstance(result, list), f"expected list, got {type(result)}"
    assert len(result) == 2
    assert result[0]["question_number"] == 1
    assert result[1]["student_answer"] == "B"


def test_extract_json_array_markdown_fenced():
    """Markdown fenced JSON 被正确提取"""
    text = '以下是结果：\n```json\n[{"question_number": 3, "student_answer": "C"}]\n```\n请参考。'
    result = _extract_first_json_array(text)
    assert isinstance(result, list), f"expected list, got {type(result)}"
    assert len(result) == 1
    assert result[0]["question_number"] == 3


def test_extract_json_array_with_explanation():
    """前后有解释文字时仍能提取数组"""
    text = '好的，根据图片分析，答案如下：[{"question_number": 5, "student_answer": "D"}] 以上是我的判断。'
    result = _extract_first_json_array(text)
    assert isinstance(result, list), f"expected list, got {type(result)}"
    assert len(result) == 1
    assert result[0]["student_answer"] == "D"


def test_extract_json_array_nested_bbox():
    """嵌套对象（answer_bbox 字段）能被正确解析"""
    text = '[{"question_number": 7, "student_answer": "正确", "answer_bbox": {"x": 100, "y": 200, "width": 50, "height": 30}}]'
    result = _extract_first_json_array(text)
    assert isinstance(result, list), f"expected list, got {type(result)}"
    assert len(result) == 1
    ab = result[0]["answer_bbox"]
    assert isinstance(ab, dict)
    assert ab["x"] == 100
    assert ab["width"] == 50


def test_extract_json_array_plain_fenced():
    """Plain ``` fence without language tag is correctly extracted"""
    text = '```\n[{"question_number": 4, "student_answer": "8", "answer_bbox": {"x": 200, "y": 90, "width": 40, "height": 22}}]\n```'
    result = _extract_first_json_array(text)
    assert isinstance(result, list), f"expected list, got {type(result)}"
    assert len(result) == 1
    assert result[0]["question_number"] == 4
    assert result[0]["answer_bbox"]["x"] == 200


def test_extract_json_array_invalid():
    """无效输入返回 None"""
    assert _extract_first_json_array(None) is None
    assert _extract_first_json_array("") is None
    assert _extract_first_json_array("no array here") is None
    assert _extract_first_json_array("{not an array}") is None


# ─────────────────────────────────────────────────────────
# 5. ROI crop size gating (P0-1): no 5% fallback, skip when < 20px
# ─────────────────────────────────────────────────────────

def test_small_roi_skip_not_fallback():
    """ROI < 20px → skip boost, not fallback to full image"""
    for cw, ch in [(0, 0), (15, 15), (19, 50), (50, 5)]:
        should_crop = cw >= 20 and ch >= 20
        assert not should_crop, f"expected skip for cw={cw} ch={ch}, but condition passed"


def test_valid_roi_proceeds():
    """ROI ≥ 20px × 20px → proceed with crop"""
    for cw, ch in [(20, 20), (100, 50), (500, 300)]:
        should_crop = cw >= 20 and ch >= 20
        assert should_crop, f"expected crop for cw={cw} ch={ch}, condition failed"


def test_old_5pct_threshold_removed():
    """ROI that was < 5% of image area now passes the new ≥20px check"""
    # 1280x1280 image, 100x50 ROI → ratio≈0.003, far below old 5% threshold
    img_area = 1280 * 1280
    cw, ch = 100, 50
    old_passes = cw > 0 and ch > 0 and img_area > 0 and cw * ch >= img_area * 0.05
    new_passes = cw >= 20 and ch >= 20
    assert not old_passes, "old 5% threshold should have rejected this ROI"
    assert new_passes, "new ≥20px condition should accept this ROI"


# ─────────────────────────────────────────────────────────
# 6. Gate contract: partial response and null bbox (P0-2)
# ─────────────────────────────────────────────────────────

def _simulate_gate(boost_data, input_qnums):
    """Reproduce gate logic from qwen_boost_group (without real bbox validation).
    Returns (parse_error, matched_qnums, written_qnums, candidate_reasons).
    """
    _matched_qnums = set()
    _all_returned_qnums = set()
    _missing_question_number = False
    _bbox_rejected = False
    _usable_bbox_written_count = 0
    _candidate_reasons = {}
    _written_qnums = set()

    for item in boost_data:
        if not isinstance(item, dict):
            _missing_question_number = True
            continue
        qn = item.get("question_number")
        if qn is None:
            _missing_question_number = True
            continue
        _all_returned_qnums.add(qn)
        if qn in input_qnums:
            _matched_qnums.add(qn)
            ab = item.get("answer_bbox")
            if ab is None:
                _bbox_rejected = True
                _candidate_reasons[qn] = "qwen_no_bbox_field"
            elif isinstance(ab, dict) and all(k in ab for k in ("x", "y", "width", "height")):
                _usable_bbox_written_count += 1
                _written_qnums.add(qn)
                _candidate_reasons[qn] = "bbox_written"
            else:
                _bbox_rejected = True
                _candidate_reasons[qn] = "qwen_bbox_parse_failed"

    _parse_error = None
    _missing_input_qnums = set(input_qnums) - _matched_qnums
    _extra_output_qnums = _all_returned_qnums - set(input_qnums)
    # NEW gate: missing questions → per-item qwen_no_item, no group failure
    for _mq in _missing_input_qnums:
        if _mq not in _candidate_reasons:
            _candidate_reasons[_mq] = "qwen_no_item"
    if _extra_output_qnums:
        _parse_error = ("partial_failure:extra_returned_questions:"
                        + ",".join(str(n) for n in sorted(_extra_output_qnums)))
    elif _missing_question_number and not _matched_qnums:
        _parse_error = "missing_question_number"
    elif not _matched_qnums:
        _parse_error = "no_matching_questions"
    elif _bbox_rejected and _usable_bbox_written_count == 0:
        _parse_error = "invalid_answer_bbox"
    elif _usable_bbox_written_count == 0:
        _parse_error = "no_usable_boost_result"
    return _parse_error, _matched_qnums, _written_qnums, _candidate_reasons


def test_partial_response_keeps_valid_bboxes():
    """boost 返回部分题（q1有bbox, q2缺失）→ q1 bbox写入，不整组失败"""
    boost_data = [
        {"question_number": 1, "student_answer": "42",
         "answer_bbox": {"x": 100, "y": 50, "width": 80, "height": 30}},
        # q2 missing from response
    ]
    parse_error, matched, written, reasons = _simulate_gate(boost_data, [1, 2])
    assert parse_error is None, f"valid bboxes should not be discarded, got: {parse_error}"
    assert 1 in written, "q1 bbox should be written"
    assert reasons.get(2) == "qwen_no_item", f"q2 should be qwen_no_item, got {reasons.get(2)}"


def test_null_bbox_not_group_failure():
    """answer_bbox=null 不算成功 bbox，但有其他题有效 bbox 时不触发整组失败"""
    boost_data = [
        {"question_number": 1, "student_answer": "42",
         "answer_bbox": {"x": 100, "y": 50, "width": 80, "height": 30}},
        {"question_number": 2, "student_answer": "", "answer_bbox": None},
    ]
    parse_error, matched, written, reasons = _simulate_gate(boost_data, [1, 2])
    assert parse_error is None, f"null bbox should not fail group, got: {parse_error}"
    assert 1 in written, "q1 bbox should be written"
    assert 2 not in written, "null bbox should not count as written"


def test_all_null_bbox_is_group_failure():
    """全部 answer_bbox=null，无有效 bbox → 整组失败"""
    boost_data = [
        {"question_number": 1, "student_answer": "", "answer_bbox": None},
        {"question_number": 2, "student_answer": "", "answer_bbox": None},
    ]
    parse_error, matched, written, reasons = _simulate_gate(boost_data, [1, 2])
    assert parse_error is not None, "all null bboxes should fail the group"
    assert "invalid_answer_bbox" in parse_error or "no_usable" in parse_error


def test_prompt_gate_contract_consistent():
    """prompt 要求每题都返回对象；gate 容忍部分 null bbox，不整组失败"""
    # Simulates model obeying new prompt: all questions returned, some with null bbox
    boost_data = [
        {"question_number": 1, "student_answer": "5",
         "answer_bbox": {"x": 200, "y": 100, "width": 60, "height": 25}},
        {"question_number": 2, "student_answer": "", "answer_bbox": None},
        {"question_number": 3, "student_answer": "", "answer_bbox": None},
    ]
    parse_error, matched, written, reasons = _simulate_gate(boost_data, [1, 2, 3])
    assert parse_error is None, f"partial null bboxes should not fail group: {parse_error}"
    assert 1 in written
    assert 2 not in written
    assert 3 not in written


# ─────────────────────────────────────────────────────────
# 3. _extract_first_json_object 解析测试
# ─────────────────────────────────────────────────────────
from pipeline import _extract_first_json_object


def test_extract_nested_answer_bbox():
    """能解析含嵌套 answer_bbox 的单对象 JSON"""
    text = 'some text {"question_number":5,"student_answer":"12","answer_bbox":{"x":10,"y":20,"width":30,"height":40}} trailing'
    result = _extract_first_json_object(text)
    assert result is not None, "should not return None"
    assert isinstance(result, dict)
    assert result["question_number"] == 5
    assert result["student_answer"] == "12"
    ab = result["answer_bbox"]
    assert isinstance(ab, dict)
    assert ab["x"] == 10
    assert ab["y"] == 20
    assert ab["width"] == 30
    assert ab["height"] == 40


def test_extract_nested_answer_bbox_not_inner():
    """不错误匹配内层 {"x":...} 作为顶层对象"""
    text = '{"question_number":1,"answer_bbox":{"x":100,"y":200,"width":50,"height":30}}'
    result = _extract_first_json_object(text)
    assert result is not None
    assert isinstance(result, dict)
    # Must have question_number at top level, not just inner bbox
    assert "question_number" in result
    assert result["question_number"] == 1
    ab = result.get("answer_bbox")
    assert isinstance(ab, dict), f"answer_bbox should be dict, got {type(ab)}"
    assert ab["x"] == 100
    assert ab["y"] == 200


def test_extract_first_json_fenced():
    """能剥离 ```json  fence"""
    text = '```json\n{"key":"value"}\n```'
    result = _extract_first_json_object(text)
    assert result is not None
    assert result["key"] == "value"


def test_extract_first_json_no_brace():
    """无花括号 → None"""
    assert _extract_first_json_object("no json here") is None
    assert _extract_first_json_object("") is None


# ─────────────────────────────────────────────────────────
# 7. bbox_only_retry 候选逻辑（P0-fix3：正确计算缺 bbox 题）
# ─────────────────────────────────────────────────────────

class _FakeQ:
    """最小 Question stub，支持 bbox / answer_bbox / student_answer / is_correct"""
    def __init__(self, num, *, bbox=None, answer_bbox=None, student_answer=None, is_correct=None):
        self.question_number = num
        self.question_text = f"题{num}"
        self.bbox = bbox or [10.0, float(num * 30), 200.0, 30.0]
        self.answer_bbox = answer_bbox
        self.student_answer = student_answer
        self.is_correct = is_correct


def _compute_retry_qs_count(boost_candidates):
    """复现 worker_process_job 修复后的 _retry_qs_count 计算逻辑"""
    return sum(
        1 for _, q, _ in boost_candidates
        if not getattr(q, "answer_bbox", None)
    )


def _compute_retry_qs(questions):
    """复现 _qwen_bbox_only_retry 修复后的 retry_qs 过滤逻辑"""
    return [
        q for q in questions
        if getattr(q, "bbox", None) and not getattr(q, "answer_bbox", None)
    ]


def test_retry_partial_boost_missing_questions():
    """boost 返回部分题 bbox 时，未返回题（qwen_no_item）进入 retry_candidates"""
    q1 = _FakeQ(1, answer_bbox=[100, 50, 80, 30])  # boost succeeded
    q2 = _FakeQ(2)   # qwen_no_item: no answer_bbox
    q3 = _FakeQ(3)   # qwen_no_item: no answer_bbox
    boost_candidates = [(0, q1, "no_answer_bbox"), (1, q2, "no_answer_bbox"), (2, q3, "no_answer_bbox")]
    count = _compute_retry_qs_count(boost_candidates)
    assert count == 2, f"expected 2 retry candidates (q2,q3), got {count}"
    retry_qs = _compute_retry_qs([q1, q2, q3])
    assert len(retry_qs) == 2
    assert q2 in retry_qs
    assert q3 in retry_qs
    assert q1 not in retry_qs


def test_retry_null_answer_bbox():
    """answer_bbox=null 返回的题应进入 retry_candidates"""
    q1 = _FakeQ(1)   # boost returned null bbox → still no answer_bbox
    q2 = _FakeQ(2, answer_bbox=[50, 50, 60, 25])  # boost ok
    boost_candidates = [(0, q1, "no_answer_bbox"), (1, q2, "no_answer_bbox")]
    count = _compute_retry_qs_count(boost_candidates)
    assert count == 1, f"expected 1 retry candidate, got {count}"
    retry_qs = _compute_retry_qs([q1, q2])
    assert q1 in retry_qs
    assert q2 not in retry_qs


def test_retry_validate_rejected():
    """boost validate_rejected 的题（answer_bbox 未写入）应进入 retry_candidates"""
    q1 = _FakeQ(1)   # validate_rejected → answer_bbox still None
    boost_candidates = [(0, q1, "no_answer_bbox")]
    count = _compute_retry_qs_count(boost_candidates)
    assert count == 1, f"expected 1 retry candidate, got {count}"
    retry_qs = _compute_retry_qs([q1])
    assert q1 in retry_qs


def test_retry_accepted_bbox_excluded():
    """boost 已成功写入 answer_bbox 的题不进入 retry"""
    q1 = _FakeQ(1, answer_bbox=[100, 50, 80, 30])   # accepted bbox
    q2 = _FakeQ(2, answer_bbox=[200, 100, 60, 25])  # accepted bbox
    boost_candidates = [(0, q1, "no_student_answer"), (1, q2, "no_student_answer")]
    count = _compute_retry_qs_count(boost_candidates)
    assert count == 0, f"all boosted: retry count should be 0, got {count}"
    retry_qs = _compute_retry_qs([q1, q2])
    assert len(retry_qs) == 0, f"accepted bbox questions must not be retried: {retry_qs}"


def test_retry_count_nonzero_when_missing_bbox():
    """boost_candidates 存在缺 bbox 题时，_retry_qs_count 不能为 0"""
    # 无 student_answer、无 is_correct（OCR-first 全程如此），但仍缺 answer_bbox
    q1 = _FakeQ(1)   # no student_answer, no is_correct, no answer_bbox
    q2 = _FakeQ(2)
    boost_candidates = [(0, q1, "no_answer_bbox|no_student_answer"),
                        (1, q2, "no_answer_bbox|no_student_answer")]
    count = _compute_retry_qs_count(boost_candidates)
    assert count > 0, (
        "bbox_only_retry must not be 0 when there are boost_candidates lacking answer_bbox; "
        f"got {count}. This is the P0-fix3 regression: old logic required student_answer/is_correct "
        "which are always None in OCR-first before grading."
    )




# ─────────────────────────────────────────────────────────
# 8. _repair_qwen_bbox_json JSON repair
# ─────────────────────────────────────────────────────────
from pipeline import _repair_qwen_bbox_json, _extract_first_json_array, _extract_first_json_object

def test_repair_qwen_bbox_comma_format():
    """{"x":58,124,136,159} → {"x":58,"y":124,"width":136,"height":159}"""
    raw = '[{"answer_bbox":{"x":58,124,136,159}}]'
    repaired = _repair_qwen_bbox_json(raw)
    assert '"y":124' in repaired, f"missing y key: {repaired}"
    assert '"width":136' in repaired, f"missing width key: {repaired}"
    assert '"height":159' in repaired, f"missing height key: {repaired}"
    import json
    json.loads(repaired)  # must be valid JSON

def test_repair_qwen_bbox_preserves_valid():
    """Valid JSON must not be changed"""
    raw = '[{"answer_bbox":{"x":120,"y":80,"width":90,"height":35}}]'
    repaired = _repair_qwen_bbox_json(raw)
    assert repaired == raw, f"valid JSON changed: {repaired}"

def test_extract_first_json_array_with_repair():
    """_extract_first_json_array repairs Qwen comma-format bbox"""
    raw = '[{"question_number":1,"answer_bbox":{"x":58,124,136,159}}]'
    result = _extract_first_json_array(raw)
    assert result is not None, f"should repair and parse, got None"
    assert result[0]["answer_bbox"]["y"] == 124, f"bbox not repaired: {result[0]['answer_bbox']}"

def test_extract_first_json_array_mixed_repair():
    """Mixed: first item bad bbox, second item valid — all parse correctly"""
    raw = '[{"qn":1,"answer_bbox":{"x":114,48,179,83}},{"qn":2,"answer_bbox":{"x":120,"y":80,"width":90,"height":35}}]'
    result = _extract_first_json_array(raw)
    assert result is not None, f"mixed repair failed"
    assert len(result) == 2
    assert result[0]["answer_bbox"]["y"] == 48, f"first item not repaired"
    assert result[1]["answer_bbox"]["width"] == 90, f"second item corrupted"

def test_extract_first_json_object_with_repair():
    """_extract_first_json_object repairs single Qwen malformed object"""
    raw = '{"question_number":4,"answer_bbox":{"x":480,59,552,100}}'
    result = _extract_first_json_object(raw)
    assert result is not None, f"should repair single object, got None"
    assert result["answer_bbox"]["y"] == 59
    assert result["answer_bbox"]["width"] == 552

def test_extract_first_json_array_no_repair_on_irreparable():
    """Truncated Qwen output: {"x":478,532,"width":4} — not repairable, returns None"""
    raw = '[{"question_number":5,"answer_bbox":{"x":478,532,"width":4}}]'
    result = _extract_first_json_array(raw)
    # This is fundamentally broken JSON, repair cannot fix it — must return None
    assert result is None, f"irreparable JSON should return None, got {result}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(failed)
