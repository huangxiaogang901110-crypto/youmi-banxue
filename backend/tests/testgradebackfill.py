"""
backend/pipeline.py L381-L402 DeepSeek 回填零覆盖修复测试。

覆盖：
1. 规则判题已有 True/False，DeepSeek 缺席 → 保留 True/False。
2. 原本未判题（is_correct=None），DeepSeek 缺席 → 仍为 None。
3. DeepSeek 返回明确结果 → 正常应用。
4. False 被当成有效结果保留，不被当成缺失处理。
5. dict 和 object（Question）两种问题类型的兼容性。

全部 mock，不调用真实 DeepSeek/Qwen/数据库/OCR。
"""
from __future__ import annotations

import asyncio
import json
import sys
import os
from unittest.mock import MagicMock, AsyncMock, patch, patch as _patch

# ── sys.path ──────────────────────────────────────────────────────────────────
_BACKEND = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _BACKEND)

import pipeline          # noqa: E402
import math_grader       # noqa: E402
import grade_compliance  # noqa: E402
from models import Question  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _ds_mock(grades: list):
    """DeepSeekClient mock：tutor() 返回指定 grades 列表的 JSON。"""
    instance = MagicMock()
    instance.tutor.return_value = {
        "success": True,
        "reply_text": json.dumps(grades),
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
    return MagicMock(return_value=instance)


def _run(questions: list, grades: list) -> None:
    """调用 pipeline.grade_answers，注入所有外部依赖 mock。"""
    with (
        patch("pipeline.DeepSeekClient", _ds_mock(grades)),
        patch("pipeline.run_grading_unit_review", AsyncMock(return_value=0.0)),
        patch.object(math_grader, "_try_math_rule_grading", return_value=None),
        patch.object(grade_compliance, "check_compliance",
                     return_value={"flipped": 0, "details": []}),
        patch.object(pipeline._db, "save_model_call", return_value=None),
        patch("pipeline.get_active_pricing", return_value={}),
        patch("pipeline.make_log_entry", return_value={"cost_cny": 0.0}),
    ):
        asyncio.run(pipeline.grade_answers(
            jid="test-backfill",
            questions=questions,
            grading_units=[],
            image_bytes=b"",
            trace_id="t0",
            parent_id="p0",
            child_id="c0",
        ))


def _q_dict(*, is_correct=None, student_answer="2",
            question_text="1+1=", explanation=None) -> dict:
    """构造 dict 类型的 question。"""
    d: dict = {
        "question_text": question_text,
        "student_answer": student_answer,
        "is_correct": is_correct,
    }
    if explanation is not None:
        d["grading_explanation"] = explanation
    return d


def _q_obj(*, is_correct=None, student_answer="2",
           question_text="1+1=") -> Question:
    """构造 Question object 类型的 question。"""
    return Question(
        question_id="q-test",
        question_number=1,
        question_text=question_text,
        student_answer=student_answer,
        is_correct=is_correct,
    )


# ── 1. 规则判题 True/False → DeepSeek 缺席时保留 ──────────────────────────────

class TestBackfillPreservesRuleGraded:
    """规则已判题（is_correct=True/False），DeepSeek 未覆盖该题 → 保留原值。"""

    def test_dict_true_preserved_when_deepseek_absent(self):
        """规则判 True，DeepSeek 不回该题 → is_correct 仍为 True。"""
        qs = [
            _q_dict(is_correct=True, student_answer="2"),    # idx=0 规则已判
            _q_dict(is_correct=None, student_answer="5"),    # idx=1 交 DeepSeek
        ]
        _run(qs, [{"index": 1, "is_correct": False, "grading_explanation": "wrong"}])
        assert qs[0]["is_correct"] is True, (
            "Rule-graded True must not be overwritten by DeepSeek absence"
        )

    def test_dict_false_preserved_when_deepseek_absent(self):
        """规则判 False（有效结果），DeepSeek 不回该题 → is_correct 仍为 False。"""
        qs = [
            _q_dict(is_correct=False, student_answer="7"),   # idx=0 规则已判错
            _q_dict(is_correct=None, student_answer="3"),    # idx=1 交 DeepSeek
        ]
        _run(qs, [{"index": 1, "is_correct": True, "grading_explanation": "correct"}])
        assert qs[0]["is_correct"] is False, (
            "Rule-graded False must not be overwritten by DeepSeek absence"
        )

    def test_multiple_rule_graded_all_preserved(self):
        """多题规则判题，DeepSeek 只回部分 → 所有规则题保留。"""
        qs = [
            _q_dict(is_correct=True,  student_answer="2"),   # idx=0
            _q_dict(is_correct=False, student_answer="7"),   # idx=1
            _q_dict(is_correct=None,  student_answer="3"),   # idx=2 → DeepSeek
        ]
        _run(qs, [{"index": 2, "is_correct": False, "grading_explanation": "wrong"}])
        assert qs[0]["is_correct"] is True
        assert qs[1]["is_correct"] is False

    def test_grading_explanation_not_overwritten(self):
        """规则判题的 grading_explanation 也不被覆盖。"""
        qs = [
            _q_dict(is_correct=True, student_answer="2", explanation="规则:正确"),
            _q_dict(is_correct=None, student_answer="9"),
        ]
        _run(qs, [{"index": 1, "is_correct": False, "grading_explanation": "deepseek"}])
        assert qs[0].get("grading_explanation") == "规则:正确", (
            "grading_explanation must not be overwritten when is_correct is preserved"
        )

    def test_object_question_true_preserved(self):
        """object（Question）类型：规则判 True，DeepSeek 缺席 → 保留 True。"""
        qs = [
            _q_obj(is_correct=True, student_answer="2"),     # idx=0 规则已判
            _q_dict(is_correct=None, student_answer="5"),    # idx=1 交 DeepSeek
        ]
        _run(qs, [{"index": 1, "is_correct": False, "grading_explanation": "wrong"}])
        assert qs[0].is_correct is True, (
            "Object question: rule-graded True must not be overwritten"
        )

    def test_object_question_false_preserved(self):
        """object（Question）类型：规则判 False，DeepSeek 缺席 → 保留 False。"""
        qs = [
            _q_obj(is_correct=False, student_answer="9"),    # idx=0 规则已判错
            _q_dict(is_correct=None, student_answer="3"),    # idx=1 交 DeepSeek
        ]
        _run(qs, [{"index": 1, "is_correct": True, "grading_explanation": "ok"}])
        assert qs[0].is_correct is False, (
            "Object question: rule-graded False must not be overwritten"
        )


# ── 2. 原本未判题，DeepSeek 缺席 → 仍为 None ─────────────────────────────────

class TestBackfillUndecidedRemaining:
    """is_correct=None，DeepSeek 缺席 → 仍为 None（暂未判定）。"""

    def test_none_stays_none_when_deepseek_absent(self):
        """has student_answer，DeepSeek 不回该题 → is_correct=None。"""
        qs = [
            _q_dict(is_correct=None, student_answer="3"),   # idx=0 → DeepSeek 回
            _q_dict(is_correct=None, student_answer="5"),   # idx=1 → DeepSeek 缺席
        ]
        _run(qs, [{"index": 0, "is_correct": True, "grading_explanation": "correct"}])
        assert qs[1]["is_correct"] is None, (
            "is_correct must remain None when DeepSeek returns no grade for this question"
        )

    def test_no_student_answer_stays_none(self):
        """无 student_answer 的题 → is_correct 保持 None，grading_explanation 为空。"""
        qs = [
            _q_dict(is_correct=None, student_answer=None),  # idx=0 无答案
            _q_dict(is_correct=None, student_answer="2"),   # idx=1 提供 DeepSeek 触发
        ]
        _run(qs, [{"index": 1, "is_correct": True, "grading_explanation": "ok"}])
        assert qs[0]["is_correct"] is None


# ── 3. DeepSeek 返回明确结果 → 正常应用 ──────────────────────────────────────

class TestBackfillDeepSeekApplied:
    """DeepSeek 返回明确结果 → 正常写入 is_correct。"""

    def test_deepseek_true_applied(self):
        qs = [_q_dict(is_correct=None, student_answer="2")]
        _run(qs, [{"index": 0, "is_correct": True, "grading_explanation": "correct"}])
        assert qs[0]["is_correct"] is True

    def test_deepseek_false_applied(self):
        qs = [_q_dict(is_correct=None, student_answer="5")]
        _run(qs, [{"index": 0, "is_correct": False, "grading_explanation": "wrong"}])
        assert qs[0]["is_correct"] is False

    def test_deepseek_null_becomes_none(self):
        """DeepSeek 返回 null（无法确定）→ is_correct=None。"""
        qs = [_q_dict(is_correct=None, student_answer="?")]
        _run(qs, [{"index": 0, "is_correct": None, "grading_explanation": "unclear"}])
        assert qs[0]["is_correct"] is None

    def test_deepseek_overrides_none(self):
        """DeepSeek 给出答案，覆盖原来的 None。"""
        qs = [_q_dict(is_correct=None, student_answer="8")]
        _run(qs, [{"index": 0, "is_correct": False, "grading_explanation": "8 != 2"}])
        assert qs[0]["is_correct"] is False
        assert "8 != 2" in qs[0].get("grading_explanation", "")


# ── 4. False 是有效判题结果，不能被当成缺失 ──────────────────────────────────

class TestFalseIsValidResult:
    """False 是有效判题结果，不能被当成缺失处理（不能等同于 None）。"""

    def test_false_not_treated_as_missing(self):
        """is_correct=False + has student_answer → 不被覆盖为 None。"""
        qs = [
            _q_dict(is_correct=False, student_answer="7"),   # idx=0 规则判错
            _q_dict(is_correct=None,  student_answer="3"),   # idx=1 未判，触发 gradable
        ]
        _run(qs, [{"index": 1, "is_correct": True, "grading_explanation": "ok"}])
        assert qs[0]["is_correct"] is False, (
            "False must be preserved; it is a valid grading result, not a missing value"
        )
        assert qs[1]["is_correct"] is True

    def test_false_not_confused_with_falsy(self):
        """所有题均为 False → gradable 为空（均已判），不进入 DeepSeek 流程。
        pipeline 早返回，questions 不变。"""
        qs = [
            _q_dict(is_correct=False, student_answer="4"),
            _q_dict(is_correct=False, student_answer="6"),
        ]
        # gradable 为空 → pipeline 在 gradable 检查处直接返回
        # grades 传空 list，DeepSeek mock 不会真正被调用到 tutor
        _run(qs, [])
        assert qs[0]["is_correct"] is False
        assert qs[1]["is_correct"] is False
