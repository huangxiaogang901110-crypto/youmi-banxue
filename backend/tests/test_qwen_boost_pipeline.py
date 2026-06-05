"""
Tests for pipeline.py Qwen-boost observability and bbox-only retry.

Covers:
1. _get_call_source() reads YOMICALL_SOURCE, falls back to YOMICALLSOURCE, defaults to
   dev_ocrfirst_real_test (never "prod").
2. _qwen_boost_group exception → failed model_call written to _model_calls.
3. _qwen_boost_group API failure (success=False) → failed model_call written.
4. _qwen_bbox_only_retry: max 1 Qwen call per image (function calls _call exactly once).
5. _qwen_bbox_only_retry success → answer_bbox written to qualifying question.
6. _qwen_bbox_only_retry API failure → answer_bbox stays None, model_call success=False.
7. _validate_answer_bbox([10,10,60,30]) is kept as xywh — not misidentified as xyxy.
8. bbox_format=xyxy in _qwen_boost_group is correctly converted to xywh.

All tests use mocks — no real OCR/Qwen/DeepSeek/database calls.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _BACKEND)

from pipeline import (  # noqa: E402
    _get_call_source,
    _qwen_bbox_only_retry,
    _qwen_boost_group,
    _validate_answer_bbox,
)
from models import Question, QuestionStatus  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_question(
    qnum: int = 1,
    *,
    student_answer: str | None = None,
    answer_bbox=None,
    bbox=None,
) -> Question:
    return Question(
        question_id=f"q-{qnum}",
        question_number=qnum,
        question_text=f"题{qnum}: 1+1=",
        kind="question",
        bbox=bbox or [10.0, 10.0, 200.0, 50.0],
        answer_bbox=answer_bbox,
        status=QuestionStatus.completed,
        student_answer=student_answer,
        source="ocr",
    )


def _qwen_ok_response(items: list) -> dict:
    """Fake successful Qwen response returning a JSON array of items."""
    return {
        "success": True,
        "content": json.dumps(items),
        "latency_ms": 100,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _mock_patches():
    """Context managers that suppress all external I/O."""
    return [
        patch("pipeline.get_active_pricing", return_value={}),
        patch("pipeline._db"),
    ]


# ── 1. _get_call_source ───────────────────────────────────────────────────────

class TestGetCallSource(unittest.TestCase):

    def test_reads_YOMICALL_SOURCE(self):
        with patch.dict(os.environ, {"YOMICALL_SOURCE": "dev_ocrfirst_real_test"}, clear=False):
            self.assertEqual(_get_call_source(), "dev_ocrfirst_real_test")

    def test_falls_back_to_legacy_YOMICALLSOURCE(self):
        env = {"YOMICALLSOURCE": "staging_legacy"}
        # Ensure the primary key is absent
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("YOMICALL_SOURCE", None)
            self.assertEqual(_get_call_source(), "staging_legacy")

    def test_default_is_dev_not_prod(self):
        """When neither env var is set, must default to dev_ocrfirst_real_test, never 'prod'."""
        env_without = {k: v for k, v in os.environ.items()
                       if k not in ("YOMICALL_SOURCE", "YOMICALLSOURCE")}
        with patch.dict(os.environ, env_without, clear=True):
            result = _get_call_source()
        self.assertEqual(result, "dev_ocrfirst_real_test")
        self.assertNotEqual(result, "prod")

    def test_whitespace_stripped(self):
        with patch.dict(os.environ, {"YOMICALL_SOURCE": "  dev_ocrfirst_real_test  "}, clear=False):
            self.assertEqual(_get_call_source(), "dev_ocrfirst_real_test")


# ── 2 & 3. _qwen_boost_group failure → model_call written ────────────────────

class TestQwenBoostGroupFailureObservability(unittest.TestCase):

    def _run_boost(self, qwen_side_effect=None, qwen_return=None):
        """Run _qwen_boost_group with one candidate question, return captured model_calls."""
        q = _make_question(1)
        boost_candidates = [(0, q, "no_student_answer")]
        captured: list = []

        fake_client = MagicMock()
        if qwen_side_effect is not None:
            fake_client._call.side_effect = qwen_side_effect
        else:
            fake_client._call.return_value = qwen_return

        with patch("pipeline.QwenVLClient", return_value=fake_client), \
             patch("pipeline.get_active_pricing", return_value={}), \
             patch("pipeline._db"), \
             patch("pipeline._model_calls", captured):
            _run(_qwen_boost_group(
                jid="job1",
                questions=[q],
                boost_candidates=boost_candidates,
                image_bytes=b"fake",
                oss_signed_url=None,
                document_classification={},
                trace_id="t1",
                parent_id="p1",
                child_id="c1",
                image_width=1280,
                image_height=1280,
            ))
        return captured

    def test_exception_writes_failed_model_call(self):
        """Network exception must produce a model_call entry with success=False."""
        calls = self._run_boost(qwen_side_effect=RuntimeError("timeout"))
        self.assertEqual(len(calls), 1)
        entry = calls[0]
        self.assertFalse(entry["success"])
        self.assertEqual(entry["feature_code"], "qwen_boost_group")
        self.assertIn("timeout", entry.get("error_message", ""))

    def test_api_failure_writes_failed_model_call(self):
        """API response with success=False must produce a model_call with success=False."""
        calls = self._run_boost(qwen_return={"success": False, "error": "rate_limit"})
        self.assertEqual(len(calls), 1)
        entry = calls[0]
        self.assertFalse(entry["success"])
        self.assertEqual(entry["feature_code"], "qwen_boost_group")

    def test_failed_call_uses_get_call_source(self):
        """call_source in failed model_call must come from _get_call_source(), not 'prod'."""
        with patch.dict(os.environ, {"YOMICALL_SOURCE": "dev_ocrfirst_real_test"}):
            calls = self._run_boost(qwen_side_effect=ConnectionError("refused"))
        self.assertEqual(calls[0]["call_source"], "dev_ocrfirst_real_test")

    def test_success_call_uses_get_call_source(self):
        """call_source in successful model_call must use _get_call_source(), not 'prod'."""
        q = _make_question(1)
        boost_candidates = [(0, q, "no_student_answer")]
        captured: list = []
        response = _qwen_ok_response([
            {"question_number": 1, "student_answer": "46",
             "answer_bbox": {"x": 70, "y": 10, "width": 60, "height": 20}}
        ])

        fake_client = MagicMock()
        fake_client._call.return_value = response

        with patch("pipeline.QwenVLClient", return_value=fake_client), \
             patch("pipeline.get_active_pricing", return_value={}), \
             patch("pipeline._db"), \
             patch("pipeline._model_calls", captured), \
             patch.dict(os.environ, {"YOMICALL_SOURCE": "dev_ocrfirst_real_test"}):
            _run(_qwen_boost_group(
                jid="job2", questions=[q], boost_candidates=boost_candidates,
                image_bytes=b"fake", oss_signed_url=None, document_classification={},
                trace_id="t2", parent_id="p2", child_id="c2",
                image_width=1280, image_height=1280,
            ))

        self.assertTrue(any(e["success"] for e in captured))
        for e in captured:
            self.assertNotEqual(e.get("call_source"), "prod")


# ── 4. _qwen_bbox_only_retry: max 1 call per image ───────────────────────────

class TestBboxOnlyRetryOnce(unittest.TestCase):

    def test_single_qwen_call_for_multiple_missing_bboxes(self):
        """All questions missing answer_bbox should be batched into exactly 1 Qwen call."""
        questions = [
            _make_question(1, student_answer="46"),
            _make_question(2, student_answer="12"),
            _make_question(3, student_answer="7"),
        ]
        response = _qwen_ok_response([
            {"question_number": 1, "answer_bbox": {"x": 70, "y": 10, "width": 50, "height": 20}},
            {"question_number": 2, "answer_bbox": {"x": 70, "y": 60, "width": 50, "height": 20}},
            {"question_number": 3, "answer_bbox": {"x": 70, "y": 110, "width": 50, "height": 20}},
        ])

        fake_client = MagicMock()
        fake_client._call.return_value = response

        with patch("pipeline.QwenVLClient", return_value=fake_client), \
             patch("pipeline.get_active_pricing", return_value={}), \
             patch("pipeline._db"), \
             patch("pipeline._model_calls", []):
            _run(_qwen_bbox_only_retry(
                jid="job3", questions=questions, image_bytes=b"fake", oss_signed_url=None,
                trace_id="t3", parent_id="p3", child_id="c3",
                image_width=1280, image_height=1280,
            ))

        self.assertEqual(fake_client._call.call_count, 1,
                         "Must make exactly 1 Qwen call regardless of how many questions need retry")

    def test_no_call_when_all_have_bbox(self):
        """Questions that already have answer_bbox must not trigger a retry call."""
        questions = [_make_question(1, student_answer="46", answer_bbox=[70.0, 10.0, 50.0, 20.0])]
        fake_client = MagicMock()

        with patch("pipeline.QwenVLClient", return_value=fake_client), \
             patch("pipeline.get_active_pricing", return_value={}), \
             patch("pipeline._db"), \
             patch("pipeline._model_calls", []):
            _run(_qwen_bbox_only_retry(
                jid="job4", questions=questions, image_bytes=b"fake", oss_signed_url=None,
                trace_id="t4", parent_id="p4", child_id="c4",
            ))

        fake_client._call.assert_not_called()

    def test_no_call_when_no_student_answer(self):
        """Questions without student_answer must not trigger retry."""
        questions = [_make_question(1, student_answer=None)]
        fake_client = MagicMock()

        with patch("pipeline.QwenVLClient", return_value=fake_client), \
             patch("pipeline.get_active_pricing", return_value={}), \
             patch("pipeline._db"), \
             patch("pipeline._model_calls", []):
            _run(_qwen_bbox_only_retry(
                jid="job5", questions=questions, image_bytes=b"fake", oss_signed_url=None,
                trace_id="t5", parent_id="p5", child_id="c5",
            ))

        fake_client._call.assert_not_called()


# ── 5. retry success → answer_bbox written ───────────────────────────────────

class TestBboxOnlyRetrySuccess(unittest.TestCase):

    def test_valid_bbox_written_on_success(self):
        q = _make_question(1, student_answer="46")
        self.assertIsNone(q.answer_bbox)

        response = _qwen_ok_response([
            {"question_number": 1, "answer_bbox": {"x": 70, "y": 10, "width": 50, "height": 20}}
        ])
        fake_client = MagicMock()
        fake_client._call.return_value = response

        with patch("pipeline.QwenVLClient", return_value=fake_client), \
             patch("pipeline.get_active_pricing", return_value={}), \
             patch("pipeline._db"), \
             patch("pipeline._model_calls", []):
            result = _run(_qwen_bbox_only_retry(
                jid="job6", questions=[q], image_bytes=b"fake", oss_signed_url=None,
                trace_id="t6", parent_id="p6", child_id="c6",
                image_width=1280, image_height=1280,
            ))

        self.assertIsNotNone(result[0].answer_bbox)
        self.assertEqual(result[0].answer_bbox, [70.0, 10.0, 50.0, 20.0])

    def test_model_call_recorded_on_success(self):
        q = _make_question(1, student_answer="5")
        response = _qwen_ok_response([
            {"question_number": 1, "answer_bbox": {"x": 70, "y": 10, "width": 50, "height": 20}}
        ])
        fake_client = MagicMock()
        fake_client._call.return_value = response
        captured: list = []

        with patch("pipeline.QwenVLClient", return_value=fake_client), \
             patch("pipeline.get_active_pricing", return_value={}), \
             patch("pipeline._db"), \
             patch("pipeline._model_calls", captured):
            _run(_qwen_bbox_only_retry(
                jid="job7", questions=[q], image_bytes=b"fake", oss_signed_url=None,
                trace_id="t7", parent_id="p7", child_id="c7",
                image_width=1280, image_height=1280,
            ))

        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0]["success"])
        self.assertEqual(captured[0]["feature_code"], "qwen_bbox_only_retry")


# ── 6. retry failure → no pseudo bbox ────────────────────────────────────────

class TestBboxOnlyRetryFailure(unittest.TestCase):

    def test_exception_leaves_answer_bbox_none(self):
        q = _make_question(1, student_answer="46")
        fake_client = MagicMock()
        fake_client._call.side_effect = TimeoutError("timed out")
        captured: list = []

        with patch("pipeline.QwenVLClient", return_value=fake_client), \
             patch("pipeline.get_active_pricing", return_value={}), \
             patch("pipeline._db"), \
             patch("pipeline._model_calls", captured):
            result = _run(_qwen_bbox_only_retry(
                jid="job8", questions=[q], image_bytes=b"fake", oss_signed_url=None,
                trace_id="t8", parent_id="p8", child_id="c8",
                image_width=1280, image_height=1280,
            ))

        self.assertIsNone(result[0].answer_bbox)
        # model_call must still be recorded
        self.assertEqual(len(captured), 1)
        self.assertFalse(captured[0]["success"])
        self.assertEqual(captured[0]["feature_code"], "qwen_bbox_only_retry")

    def test_api_failure_leaves_answer_bbox_none(self):
        q = _make_question(1, student_answer="7")
        fake_client = MagicMock()
        fake_client._call.return_value = {"success": False, "error": "quota_exceeded"}
        captured: list = []

        with patch("pipeline.QwenVLClient", return_value=fake_client), \
             patch("pipeline.get_active_pricing", return_value={}), \
             patch("pipeline._db"), \
             patch("pipeline._model_calls", captured):
            result = _run(_qwen_bbox_only_retry(
                jid="job9", questions=[q], image_bytes=b"fake", oss_signed_url=None,
                trace_id="t9", parent_id="p9", child_id="c9",
                image_width=1280, image_height=1280,
            ))

        self.assertIsNone(result[0].answer_bbox)
        self.assertFalse(captured[0]["success"])

    def test_validate_rejected_leaves_answer_bbox_none(self):
        """If Qwen returns a bbox that fails _validate_answer_bbox, answer_bbox stays None."""
        q = _make_question(1, student_answer="3", bbox=[10.0, 10.0, 100.0, 40.0])
        # Return a bbox identical to question_bbox — should be rejected by safety gate
        response = _qwen_ok_response([
            {"question_number": 1, "answer_bbox": {"x": 10, "y": 10, "width": 100, "height": 40}}
        ])
        fake_client = MagicMock()
        fake_client._call.return_value = response

        with patch("pipeline.QwenVLClient", return_value=fake_client), \
             patch("pipeline.get_active_pricing", return_value={}), \
             patch("pipeline._db"), \
             patch("pipeline._model_calls", []):
            result = _run(_qwen_bbox_only_retry(
                jid="job10", questions=[q], image_bytes=b"fake", oss_signed_url=None,
                trace_id="t10", parent_id="p10", child_id="c10",
                image_width=1280, image_height=1280,
            ))

        self.assertIsNone(result[0].answer_bbox,
                          "bbox identical to question_bbox must be rejected by safety gate")


# ── 7. [10,10,60,30] must NOT be misidentified as xyxy ───────────────────────

class TestValidateAnswerBboxNoMisconversion(unittest.TestCase):

    def test_pure_list_treated_as_xywh(self):
        """[10,10,60,30] is a small xywh bbox — must pass and NOT be converted as if xyxy."""
        result = _validate_answer_bbox([10, 10, 60, 30])
        self.assertIsNotNone(result, "[10,10,60,30] is a valid xywh bbox and must not be rejected")
        self.assertEqual(result, [10.0, 10.0, 60.0, 30.0])

    def test_pure_list_small_coords_not_converted(self):
        """Pure list must not trigger xyxy→xywh conversion; [10,10,60,30] stays [10,10,60,30]."""
        result = _validate_answer_bbox([10, 10, 60, 30])
        # If misidentified as xyxy, width would become 50 and height 20
        self.assertNotEqual(result, [10.0, 10.0, 50.0, 20.0],
                            "Must not convert as xyxy when no explicit bbox_format hint")

    def test_zero_wh_rejected(self):
        self.assertIsNone(_validate_answer_bbox([0, 0, 0, 0]))

    def test_negative_wh_rejected(self):
        self.assertIsNone(_validate_answer_bbox([10, 10, -5, 20]))


# ── 8. bbox_format=xyxy correctly converted in _qwen_boost_group ─────────────

class TestQwenBoostXyxyConversion(unittest.TestCase):

    def _run_boost_with_response(self, response_items):
        q = _make_question(1, bbox=[10.0, 10.0, 200.0, 50.0])
        boost_candidates = [(0, q, "no_answer")]
        fake_client = MagicMock()
        fake_client._call.return_value = _qwen_ok_response(response_items)

        with patch("pipeline.QwenVLClient", return_value=fake_client), \
             patch("pipeline.get_active_pricing", return_value={}), \
             patch("pipeline._db"), \
             patch("pipeline._model_calls", []):
            _run(_qwen_boost_group(
                jid="jobX", questions=[q], boost_candidates=boost_candidates,
                image_bytes=b"fake", oss_signed_url=None, document_classification={},
                trace_id="tX", parent_id="pX", child_id="cX",
                image_width=1280, image_height=1280,
            ))
        return q

    def test_explicit_xyxy_converted_to_xywh(self):
        """bbox_format=xyxy with [x1,y1,x2,y2] must be stored as [x1,y1,w,h]."""
        q = self._run_boost_with_response([{
            "question_number": 1,
            "student_answer": "46",
            "answer_bbox": {"bbox": [70, 10, 130, 30], "bbox_format": "xyxy"},
        }])
        self.assertIsNotNone(q.answer_bbox)
        x, y, w, h = q.answer_bbox
        self.assertEqual(w, 60.0, "width should be x2-x1 = 130-70 = 60")
        self.assertEqual(h, 20.0, "height should be y2-y1 = 30-10 = 20")

    def test_explicit_xywh_kept_as_is(self):
        """bbox_format=xywh with [x,y,w,h] must be stored unchanged."""
        q = self._run_boost_with_response([{
            "question_number": 1,
            "student_answer": "46",
            "answer_bbox": {"bbox": [70, 10, 60, 20], "bbox_format": "xywh"},
        }])
        self.assertIsNotNone(q.answer_bbox)
        self.assertEqual(q.answer_bbox, [70.0, 10.0, 60.0, 20.0])

    def test_object_format_xywh(self):
        """Object format {x,y,width,height} must be stored as [x,y,w,h]."""
        q = self._run_boost_with_response([{
            "question_number": 1,
            "student_answer": "46",
            "answer_bbox": {"x": 70, "y": 10, "width": 60, "height": 20},
        }])
        self.assertIsNotNone(q.answer_bbox)
        self.assertEqual(q.answer_bbox, [70.0, 10.0, 60.0, 20.0])


# ── 9. _qwen_boost_group success observability ───────────────────────────────

class TestQwenBoostSuccessObservability(unittest.TestCase):
    """Verify that successful Qwen boost calls carry structured summary and per-question reasons."""

    def _run_boost(self, response_items, q=None, image_width=1280, image_height=1280):
        if q is None:
            q = _make_question(1, bbox=[10.0, 10.0, 200.0, 50.0])
        boost_candidates = [(0, q, "no_answer")]
        captured: list = []
        fake_client = MagicMock()
        fake_client._call.return_value = _qwen_ok_response(response_items)

        with patch("pipeline.QwenVLClient", return_value=fake_client), \
             patch("pipeline.get_active_pricing", return_value={}), \
             patch("pipeline._db"), \
             patch("pipeline._model_calls", captured):
            _run(_qwen_boost_group(
                jid="jobObs", questions=[q], boost_candidates=boost_candidates,
                image_bytes=b"fake", oss_signed_url=None, document_classification={},
                trace_id="tObs", parent_id="pObs", child_id="cObs",
                image_width=image_width, image_height=image_height,
            ))
        return q, captured

    def test_no_bbox_field_records_qwen_no_bbox_field(self):
        """When Qwen returns an item with no answer_bbox, question_reasons must record qwen_no_bbox_field."""
        q, captured = self._run_boost([
            {"question_number": 1, "student_answer": "46"}  # no answer_bbox key
        ])
        self.assertTrue(len(captured) >= 1)
        entry = captured[-1]  # last entry is the parse-result model_call
        reasons = entry.get("question_reasons", {})
        self.assertEqual(reasons.get(1), "qwen_no_bbox_field",
                         f"Expected qwen_no_bbox_field, got {reasons}")
        summary = entry.get("boost_summary", {})
        self.assertEqual(summary.get("no_bbox_count"), 1)

    def test_validate_rejected_records_validate_rejected(self):
        """When bbox fails _validate_answer_bbox, question_reasons must record validate_rejected."""
        # bbox identical to question_bbox → rejected by safety gate
        q = _make_question(1, bbox=[10.0, 10.0, 100.0, 40.0])
        _, captured = self._run_boost(
            [{"question_number": 1, "student_answer": "3",
              "answer_bbox": {"x": 10, "y": 10, "width": 100, "height": 40}}],
            q=q,
        )
        entry = captured[-1]
        reasons = entry.get("question_reasons", {})
        self.assertEqual(reasons.get(1), "validate_rejected",
                         f"Expected validate_rejected, got {reasons}")
        summary = entry.get("boost_summary", {})
        self.assertGreaterEqual(summary.get("validate_rejected_bbox_count", 0), 1)

    def test_student_answer_no_bbox_source_partial_boost(self):
        """When student_answer is present but bbox is absent, source must become partial_boost."""
        q, _ = self._run_boost([
            {"question_number": 1, "student_answer": "46"}  # no answer_bbox
        ])
        self.assertEqual(q.student_answer, "46")
        self.assertEqual(q.source, "partial_boost",
                         f"Expected partial_boost, got {q.source!r}")
        self.assertIsNone(q.answer_bbox)

    def test_validate_rejected_with_student_answer_source_partial_boost(self):
        """When bbox is rejected but student_answer present, source must become partial_boost."""
        q = _make_question(1, bbox=[10.0, 10.0, 100.0, 40.0])
        q, _ = self._run_boost(
            [{"question_number": 1, "student_answer": "7",
              "answer_bbox": {"x": 10, "y": 10, "width": 100, "height": 40}}],
            q=q,
        )
        self.assertEqual(q.student_answer, "7")
        self.assertEqual(q.source, "partial_boost")
        self.assertIsNone(q.answer_bbox)

    def test_success_model_call_contains_boost_summary(self):
        """Successful boost (bbox written) must include boost_summary with correct counts."""
        q, captured = self._run_boost([
            {"question_number": 1, "student_answer": "46",
             "answer_bbox": {"x": 70, "y": 10, "width": 60, "height": 20}}
        ])
        self.assertIsNotNone(q.answer_bbox)
        success_entries = [e for e in captured if e.get("success")]
        self.assertTrue(len(success_entries) >= 1, "Must have at least one success=True model_call")
        entry = success_entries[-1]
        summary = entry.get("boost_summary", {})
        self.assertIn("returned_items_count", summary)
        self.assertEqual(summary.get("returned_items_count"), 1)
        self.assertEqual(summary.get("validate_accepted_bbox_count"), 1)
        self.assertEqual(summary.get("validate_rejected_bbox_count"), 0)
        self.assertEqual(summary.get("no_bbox_count"), 0)
        reasons = entry.get("question_reasons", {})
        self.assertIn(1, reasons)
        self.assertIn("bbox_written", reasons[1])

    def test_qwen_no_item_reason_for_unmatched_question(self):
        """If Qwen response omits a question from the group, reason must be qwen_no_item."""
        q = _make_question(1, bbox=[10.0, 10.0, 200.0, 50.0])
        boost_candidates = [(0, q, "no_answer")]
        captured: list = []
        fake_client = MagicMock()
        # Response has question_number=99 which doesn't match q (question_number=1)
        fake_client._call.return_value = _qwen_ok_response([
            {"question_number": 99, "student_answer": "x",
             "answer_bbox": {"x": 70, "y": 10, "width": 60, "height": 20}}
        ])

        with patch("pipeline.QwenVLClient", return_value=fake_client), \
             patch("pipeline.get_active_pricing", return_value={}), \
             patch("pipeline._db"), \
             patch("pipeline._model_calls", captured):
            _run(_qwen_boost_group(
                jid="jobNoItem", questions=[q], boost_candidates=boost_candidates,
                image_bytes=b"fake", oss_signed_url=None, document_classification={},
                trace_id="tNI", parent_id="pNI", child_id="cNI",
                image_width=1280, image_height=1280,
            ))

        entry = captured[-1]
        reasons = entry.get("question_reasons", {})
        self.assertEqual(reasons.get(1), "qwen_no_item",
                         f"Expected qwen_no_item for unmatched q=1, got {reasons}")
        summary = entry.get("boost_summary", {})
        self.assertEqual(summary.get("no_matching_question_count"), 1)
        self.assertIn(1, summary.get("unmatched_question_numbers", []))


# ── 10. bbox-only retry trigger conditions ────────────────────────────────────

class TestBboxOnlyRetryTriggerConditions(unittest.TestCase):
    """Verify that is_correct non-None also triggers bbox-only retry."""

    def _run_retry(self, questions, qwen_return=None):
        captured: list = []
        fake_client = MagicMock()
        if qwen_return is not None:
            fake_client._call.return_value = qwen_return
        else:
            fake_client._call.return_value = {"success": False, "error": "test"}

        with patch("pipeline.QwenVLClient", return_value=fake_client), \
             patch("pipeline.get_active_pricing", return_value={}), \
             patch("pipeline._db"), \
             patch("pipeline._model_calls", captured):
            _run(_qwen_bbox_only_retry(
                jid="jobTrig", questions=questions, image_bytes=b"fake", oss_signed_url=None,
                trace_id="tTrig", parent_id="pTrig", child_id="cTrig",
                image_width=1280, image_height=1280,
            ))
        return fake_client, captured

    def test_is_correct_true_triggers_retry(self):
        """Question with is_correct=True but no answer_bbox must trigger exactly 1 Qwen call."""
        q = _make_question(1, student_answer=None, answer_bbox=None)
        q.is_correct = True  # graded externally, no bbox yet
        client, _ = self._run_retry([q])
        self.assertEqual(client._call.call_count, 1,
                         "is_correct=True without answer_bbox must trigger retry")

    def test_is_correct_false_triggers_retry(self):
        """Question with is_correct=False (wrong answer) but no answer_bbox must trigger retry."""
        q = _make_question(1, student_answer=None, answer_bbox=None)
        q.is_correct = False
        client, _ = self._run_retry([q])
        self.assertEqual(client._call.call_count, 1)

    def test_is_correct_none_and_no_student_answer_no_retry(self):
        """Question with is_correct=None and no student_answer must NOT trigger retry."""
        q = _make_question(1, student_answer=None, answer_bbox=None)
        # q.is_correct is already None by default
        client, _ = self._run_retry([q])
        client._call.assert_not_called()

    def test_is_correct_with_answer_bbox_no_retry(self):
        """Question with is_correct set AND answer_bbox already present must NOT trigger retry."""
        q = _make_question(1, student_answer=None, answer_bbox=[70.0, 10.0, 50.0, 20.0])
        q.is_correct = True
        client, _ = self._run_retry([q])
        client._call.assert_not_called()

    def test_is_correct_and_student_answer_batched_into_one_call(self):
        """Multiple eligible questions (is_correct or student_answer) must use exactly 1 Qwen call."""
        q1 = _make_question(1, student_answer="42", answer_bbox=None)
        q2 = _make_question(2, student_answer=None, answer_bbox=None)
        q2.is_correct = True
        client, _ = self._run_retry([q1, q2])
        self.assertEqual(client._call.call_count, 1,
                         "All eligible questions must be batched into exactly 1 call")

    def test_is_correct_retry_writes_bbox_on_success(self):
        """is_correct-triggered retry must write answer_bbox when Qwen returns valid bbox."""
        q = _make_question(1, student_answer=None, answer_bbox=None)
        q.is_correct = True
        response = _qwen_ok_response([
            {"question_number": 1, "answer_bbox": {"x": 70, "y": 10, "width": 50, "height": 20}}
        ])
        _, _ = self._run_retry([q], qwen_return=response)
        self.assertIsNotNone(q.answer_bbox,
                             "Retry triggered by is_correct must write answer_bbox on success")
        self.assertEqual(q.answer_bbox, [70.0, 10.0, 50.0, 20.0])


# ── 11. call_source always dev_ocrfirst_real_test (no mixed spellings) ────────

class TestCallSourceStandard(unittest.TestCase):
    """call_source must always normalise to dev_ocrfirst_real_test via _get_call_source()."""

    def test_YOMICALL_SOURCE_with_underscore_wins(self):
        with patch.dict(os.environ, {"YOMICALL_SOURCE": "dev_ocrfirst_real_test"}, clear=False):
            self.assertEqual(_get_call_source(), "dev_ocrfirst_real_test")

    def test_legacy_YOMICALLSOURCE_no_underscore_fallback(self):
        env = {"YOMICALLSOURCE": "dev_ocrfirst_real_test"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("YOMICALL_SOURCE", None)
            self.assertEqual(_get_call_source(), "dev_ocrfirst_real_test")

    def test_default_never_mixed_spelling(self):
        """Default must be dev_ocrfirst_real_test — not devocrfirstrealtest or devocrfirstreal_test."""
        env_clean = {k: v for k, v in os.environ.items()
                     if k not in ("YOMICALL_SOURCE", "YOMICALLSOURCE")}
        with patch.dict(os.environ, env_clean, clear=True):
            result = _get_call_source()
        self.assertEqual(result, "dev_ocrfirst_real_test")
        self.assertNotIn("devocrfirstrealtest", result)
        self.assertNotIn("devocrfirstreal_test", result)


if __name__ == "__main__":
    unittest.main()
