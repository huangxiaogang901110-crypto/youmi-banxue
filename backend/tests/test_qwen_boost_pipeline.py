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


if __name__ == "__main__":
    unittest.main()
