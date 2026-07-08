"""
Tests: Qwen boost observable failures + bbox-only retry behavior.

Covers:
  - Qwen boost exception → failed model_call with success=False, error_message
  - Qwen boost failed model_call carries call_source (not prod default)
  - bbox-only retry: max 1 API call per image regardless of eligible question count
  - bbox-only retry: 0 calls when no questions need retry
  - retry success → answer_bbox written
  - retry failure (exception) → answer_bbox stays None (no pseudo bbox)
  - retry failure (API error) → answer_bbox stays None
  - [10,10,60,30] pure-list xywh not misconverted to xyxy
  - bbox_format=xyxy [10,10,70,40] → correct [10,10,60,30] conversion
  - _qwen_boost_group: xyxy dict response converts correctly end-to-end
"""

import json
import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# ─── db / job_store stubs (must precede any pipeline import) ───────────────────
if "db" not in sys.modules or not isinstance(sys.modules.get("db"), MagicMock):
    _mock_db = MagicMock()
    _mock_db.load_all.return_value = ({}, [], {}, {})
    sys.modules["db"] = _mock_db

if "job_store" not in sys.modules or not isinstance(
    getattr(sys.modules.get("job_store"), "_model_calls", None), list
):
    _mock_js = MagicMock()
    _mock_js._jobs = {}
    _mock_js._model_calls = []
    _mock_js._deferred_vision_tasks = {}
    _mock_js._ts = MagicMock(return_value="2026-01-01T00:00:00")
    sys.modules["job_store"] = _mock_js

# ─── Stub real vision/ocr clients if pipeline not yet imported ─────────────────
if "backend.pipeline" not in sys.modules:
    import importlib
    for _mod_path in ("backend.ocr_client", "ocr_client", "backend.vision_client", "vision_client"):
        try:
            _m = importlib.import_module(_mod_path)
            if hasattr(_m, "AliyunOCRClient"):
                class _Stub:
                    def __init__(self, *a, **k):
                        raise AssertionError("REAL OCR/VISION CLIENT CALLED IN TEST")
                _m.AliyunOCRClient = _Stub
            if hasattr(_m, "QwenVLClient"):
                class _QStub:
                    def __init__(self, *a, **k):
                        raise AssertionError("REAL QWEN CLIENT CALLED IN TEST")
                _m.QwenVLClient = _QStub
        except ImportError:
            pass

# ─── Import pipeline functions under test ──────────────────────────────────────
from backend.pipeline import (
    _validate_answer_bbox,
    _qwen_boost_group,
    _qwen_bbox_only_retry,
)
import backend.pipeline as _pipeline


# ─── Helpers ──────────────────────────────────────────────────────────────────

class _FakeQ:
    """Minimal Question-like object for tests."""
    def __init__(self, qnum=1, text="1 + 2 = ?",
                 student_answer=None, answer_bbox=None, bbox=None):
        self.question_number = qnum
        self.question_text = text
        self.student_answer = student_answer
        self.answer_bbox = answer_bbox
        self.bbox = bbox or [5, 5, 200, 50]
        self.source = "ocr_main"


# pricing=None → _compute_cost_cny returns zeros without touching MagicMock pricing
_PATCH_PRICING = patch("backend.pipeline.get_active_pricing", return_value=None)


def _clear():
    _pipeline._model_calls.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Qwen boost exception → failed model_call recorded
# ═══════════════════════════════════════════════════════════════════════════════

class TestQwenBoostExceptionObservability:
    @pytest.mark.asyncio
    async def test_exception_writes_failed_model_call(self):
        """RuntimeError in _call must produce a model_call entry with success=False."""
        _clear()
        q = _FakeQ()
        boost_candidates = [(0, q, "no_student_answer")]

        with _PATCH_PRICING, patch("backend.pipeline.QwenVLClient") as MockCls:
            inst = MagicMock()
            inst._call.side_effect = RuntimeError("simulated_timeout")
            MockCls.return_value = inst

            await _qwen_boost_group(
                "jid_exc", [q], boost_candidates,
                b"img", None, {}, "trace1", "p1", "c1",
            )

        failed = [c for c in _pipeline._model_calls if not c.get("success")]
        assert len(failed) >= 1, "Must record at least one failed model_call"
        fc = failed[0]
        assert fc.get("feature_code") == "qwen_boost_group"
        assert "simulated_timeout" in (fc.get("error_message") or "")

    @pytest.mark.asyncio
    async def test_failed_model_call_has_correct_call_source(self):
        """Failed model_call must carry call_source from YOMICALL_SOURCE env."""
        _clear()
        os.environ["YOMICALL_SOURCE"] = "dev_ocrfirst_real_test"
        try:
            q = _FakeQ()
            boost_candidates = [(0, q, "no_student_answer")]

            with _PATCH_PRICING, patch("backend.pipeline.QwenVLClient") as MockCls:
                inst = MagicMock()
                inst._call.side_effect = RuntimeError("err")
                MockCls.return_value = inst

                await _qwen_boost_group(
                    "jid_src", [q], boost_candidates,
                    b"img", None, {}, "t", "p", "c",
                )

            failed = [c for c in _pipeline._model_calls if not c.get("success")]
            assert len(failed) >= 1
            assert failed[0].get("call_source") == "dev_ocrfirst_real_test"
        finally:
            os.environ.pop("YOMICALL_SOURCE", None)

    @pytest.mark.asyncio
    async def test_api_failure_writes_failed_model_call(self):
        """API returning success=False must also produce a failed model_call."""
        _clear()
        q = _FakeQ()
        boost_candidates = [(0, q, "no_student_answer")]

        with _PATCH_PRICING, patch("backend.pipeline.QwenVLClient") as MockCls:
            inst = MagicMock()
            inst._call.return_value = {
                "success": False,
                "error": "quota_exceeded",
                "content": "",
                "usage": {},
                "latency_ms": 5,
            }
            MockCls.return_value = inst

            await _qwen_boost_group(
                "jid_apifail", [q], boost_candidates,
                b"img", None, {}, "t", "p", "c",
            )

        failed = [c for c in _pipeline._model_calls if not c.get("success")]
        assert len(failed) >= 1
        fc = failed[0]
        assert fc.get("feature_code") == "qwen_boost_group"
        assert "quota_exceeded" in (fc.get("error_message") or "")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. bbox-only retry: max 1 API call per image
# ═══════════════════════════════════════════════════════════════════════════════

class TestBboxOnlyRetryPerImageLimit:
    @pytest.mark.asyncio
    async def test_one_api_call_for_multiple_eligible_questions(self):
        """Three questions all missing answer_bbox → exactly 1 API call."""
        _clear()
        qs = [_FakeQ(qnum=i, student_answer="42") for i in range(1, 4)]

        with _PATCH_PRICING, patch("backend.pipeline.QwenVLClient") as MockCls:
            inst = MagicMock()
            inst._call.return_value = {
                "success": False, "error": "test", "content": "", "usage": {}, "latency_ms": 10,
            }
            MockCls.return_value = inst

            await _qwen_bbox_only_retry(
                "jid_limit", qs, b"img", None, "t", "p", "c",
            )

        retry_calls = [c for c in _pipeline._model_calls
                       if c.get("feature_code") == "qwen_bbox_only_retry"]
        assert len(retry_calls) == 1, f"Expected 1 retry call, got {len(retry_calls)}"

    @pytest.mark.asyncio
    async def test_no_api_call_when_no_eligible_questions(self):
        """Questions without student_answer → QwenVLClient never instantiated."""
        _clear()
        qs = [_FakeQ(qnum=1, student_answer=None)]

        with _PATCH_PRICING, patch("backend.pipeline.QwenVLClient") as MockCls:
            await _qwen_bbox_only_retry(
                "jid_noop", qs, b"img", None, "t", "p", "c",
            )
            MockCls.assert_not_called()

        retry_calls = [c for c in _pipeline._model_calls
                       if c.get("feature_code") == "qwen_bbox_only_retry"]
        assert len(retry_calls) == 0

    @pytest.mark.asyncio
    async def test_no_api_call_when_answer_bbox_already_set(self):
        """Question with student_answer AND answer_bbox already → no retry call."""
        _clear()
        qs = [_FakeQ(qnum=1, student_answer="46", answer_bbox=[80, 20, 30, 18])]

        with _PATCH_PRICING, patch("backend.pipeline.QwenVLClient") as MockCls:
            await _qwen_bbox_only_retry(
                "jid_has_bbox", qs, b"img", None, "t", "p", "c",
            )
            MockCls.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. retry success → answer_bbox written
# ═══════════════════════════════════════════════════════════════════════════════

class TestBboxOnlyRetrySuccess:
    @pytest.mark.asyncio
    async def test_retry_success_writes_answer_bbox(self):
        """Valid bbox in retry response → question.answer_bbox set to expected value."""
        _clear()
        q = _FakeQ(qnum=1, student_answer="46", answer_bbox=None, bbox=[10, 20, 100, 25])

        resp = json.dumps([{
            "question_number": 1,
            "answer_bbox": {"x": 80, "y": 22, "width": 30, "height": 20},
        }])

        with _PATCH_PRICING, patch("backend.pipeline.QwenVLClient") as MockCls:
            inst = MagicMock()
            inst._call.return_value = {
                "success": True, "content": resp,
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "latency_ms": 200,
            }
            MockCls.return_value = inst

            result = await _qwen_bbox_only_retry(
                "jid_ok", [q], b"img", None, "t", "p", "c",
                image_width=800, image_height=600,
            )

        assert result[0].answer_bbox is not None, "answer_bbox must be set after successful retry"
        assert result[0].answer_bbox == [80.0, 22.0, 30.0, 20.0]

    @pytest.mark.asyncio
    async def test_retry_success_model_call_recorded(self):
        """Successful retry records a model_call with success=True."""
        _clear()
        q = _FakeQ(qnum=1, student_answer="46", answer_bbox=None, bbox=[10, 20, 100, 25])
        resp = json.dumps([{
            "question_number": 1,
            "answer_bbox": {"x": 80, "y": 22, "width": 30, "height": 20},
        }])

        with _PATCH_PRICING, patch("backend.pipeline.QwenVLClient") as MockCls:
            inst = MagicMock()
            inst._call.return_value = {
                "success": True, "content": resp,
                "usage": {"prompt_tokens": 50, "completion_tokens": 10}, "latency_ms": 100,
            }
            MockCls.return_value = inst

            await _qwen_bbox_only_retry(
                "jid_ok2", [q], b"img", None, "t", "p", "c",
                image_width=800, image_height=600,
            )

        success_calls = [c for c in _pipeline._model_calls
                         if c.get("feature_code") == "qwen_bbox_only_retry" and c.get("success")]
        assert len(success_calls) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 4. retry failure → answer_bbox stays None (no pseudo bbox)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBboxOnlyRetryFailure:
    @pytest.mark.asyncio
    async def test_exception_answer_bbox_remains_none(self):
        """RuntimeError in retry _call → answer_bbox must NOT be fabricated."""
        _clear()
        q = _FakeQ(qnum=1, student_answer="46", answer_bbox=None)

        with _PATCH_PRICING, patch("backend.pipeline.QwenVLClient") as MockCls:
            inst = MagicMock()
            inst._call.side_effect = RuntimeError("conn_refused")
            MockCls.return_value = inst

            result = await _qwen_bbox_only_retry(
                "jid_exc_retry", [q], b"img", None, "t", "p", "c",
            )

        assert result[0].answer_bbox is None, "answer_bbox must not be fabricated on exception"

    @pytest.mark.asyncio
    async def test_api_error_answer_bbox_remains_none(self):
        """API returning success=False → answer_bbox stays None."""
        _clear()
        q = _FakeQ(qnum=1, student_answer="46", answer_bbox=None)

        with _PATCH_PRICING, patch("backend.pipeline.QwenVLClient") as MockCls:
            inst = MagicMock()
            inst._call.return_value = {
                "success": False, "error": "quota_exceeded", "content": "", "usage": {}, "latency_ms": 5,
            }
            MockCls.return_value = inst

            result = await _qwen_bbox_only_retry(
                "jid_apifail_retry", [q], b"img", None, "t", "p", "c",
            )

        assert result[0].answer_bbox is None

    @pytest.mark.asyncio
    async def test_validate_rejected_answer_bbox_remains_none(self):
        """Retry returns a bbox that fails _validate_answer_bbox → answer_bbox stays None."""
        _clear()
        q = _FakeQ(qnum=1, student_answer="46", answer_bbox=None, bbox=[10, 10, 100, 30])

        # bbox [0,0,0,0] is invalid (all zeros)
        resp = json.dumps([{"question_number": 1, "answer_bbox": {"x": 0, "y": 0, "width": 0, "height": 0}}])

        with _PATCH_PRICING, patch("backend.pipeline.QwenVLClient") as MockCls:
            inst = MagicMock()
            inst._call.return_value = {
                "success": True, "content": resp,
                "usage": {"prompt_tokens": 50, "completion_tokens": 10}, "latency_ms": 80,
            }
            MockCls.return_value = inst

            result = await _qwen_bbox_only_retry(
                "jid_inv_retry", [q], b"img", None, "t", "p", "c",
            )

        assert result[0].answer_bbox is None, "Invalid bbox must not be written"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. [10,10,60,30] xywh pure-list not misconverted
# ═══════════════════════════════════════════════════════════════════════════════

class TestBboxXywhNotMisconverted:
    def test_10_10_60_30_passes_as_xywh(self):
        """[10,10,60,30] is valid xywh — must return unchanged, not trigger legacy rescue."""
        result = _validate_answer_bbox([10, 10, 60, 30])
        assert result == [10.0, 10.0, 60.0, 30.0], (
            f"[10,10,60,30] should be treated as xywh (x=10,y=10,w=60,h=30), got {result}"
        )

    def test_10_10_60_30_with_image_bounds(self):
        """[10,10,60,30] must pass validation even with image bounds present."""
        result = _validate_answer_bbox([10, 10, 60, 30], image_width=400, image_height=300)
        assert result == [10.0, 10.0, 60.0, 30.0]

    def test_xywh_with_question_bbox_not_same(self):
        """[10,10,60,30] with a different question_bbox passes validation."""
        result = _validate_answer_bbox([10, 10, 60, 30], question_bbox=[5, 5, 200, 100])
        assert result == [10.0, 10.0, 60.0, 30.0]

    @pytest.mark.asyncio
    async def test_boost_pure_list_xywh_not_reconverted(self):
        """_qwen_boost_group: pure-list [10,10,60,30] stays [10,10,60,30] (xywh)."""
        _clear()
        q = _FakeQ(qnum=1, bbox=[5, 5, 200, 100])
        boost_candidates = [(0, q, "no_student_answer")]

        resp = json.dumps([{
            "question_number": 1,
            "student_answer": "46",
            "answer_bbox": [10, 10, 60, 30],  # pure list, treated as xywh
        }])

        with _PATCH_PRICING, patch("backend.pipeline.QwenVLClient") as MockCls:
            inst = MagicMock()
            inst._call.return_value = {
                "success": True, "content": resp,
                "usage": {"prompt_tokens": 50, "completion_tokens": 10}, "latency_ms": 100,
            }
            MockCls.return_value = inst

            result_qs = await _qwen_boost_group(
                "jid_xywh_test", [q], boost_candidates,
                b"img", None, {}, "t", "p", "c",
                image_width=800, image_height=600,
            )

        # [10,10,60,30] as xywh should pass through unchanged (not converted to [10,10,50,20])
        assert result_qs[0].answer_bbox == [10.0, 10.0, 60.0, 30.0], (
            f"[10,10,60,30] xywh must not be reconverted, got {result_qs[0].answer_bbox}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. bbox_format=xyxy correct conversion
# ═══════════════════════════════════════════════════════════════════════════════

class TestBboxFormatXyxy:
    def test_xyxy_arithmetic(self):
        """xyxy [10,10,70,40] converts to xywh [10,10,60,30] via width=x2-x1, height=y2-y1."""
        x1, y1, x2, y2 = 10, 10, 70, 40
        w, h = x2 - x1, y2 - y1
        result = _validate_answer_bbox([x1, y1, w, h])
        assert result == [10.0, 10.0, 60.0, 30.0]

    def test_xyxy_and_xywh_same_box(self):
        """xyxy [10,10,70,40] and direct xywh [10,10,60,30] represent the same box."""
        x1, y1, x2, y2 = 10, 10, 70, 40
        xyxy_result = _validate_answer_bbox([x1, y1, x2 - x1, y2 - y1])
        xywh_result = _validate_answer_bbox([10, 10, 60, 30])
        assert xyxy_result == xywh_result == [10.0, 10.0, 60.0, 30.0]

    @pytest.mark.asyncio
    async def test_boost_xyxy_dict_converts_correctly(self):
        """_qwen_boost_group: bbox_format=xyxy {bbox:[10,10,70,40]} → answer_bbox [10,10,60,30]."""
        _clear()
        q = _FakeQ(qnum=1, bbox=[5, 5, 200, 100])
        boost_candidates = [(0, q, "no_student_answer")]

        resp = json.dumps([{
            "question_number": 1,
            "student_answer": "46",
            "answer_bbox": {"bbox": [10, 10, 70, 40], "bbox_format": "xyxy"},
        }])

        with _PATCH_PRICING, patch("backend.pipeline.QwenVLClient") as MockCls:
            inst = MagicMock()
            inst._call.return_value = {
                "success": True, "content": resp,
                "usage": {"prompt_tokens": 50, "completion_tokens": 10}, "latency_ms": 100,
            }
            MockCls.return_value = inst

            result_qs = await _qwen_boost_group(
                "jid_xyxy", [q], boost_candidates,
                b"img", None, {}, "t", "p", "c",
                image_width=800, image_height=600,
            )

        assert result_qs[0].answer_bbox == [10.0, 10.0, 60.0, 30.0], (
            f"bbox_format=xyxy [10,10,70,40] → expected [10,10,60,30], got {result_qs[0].answer_bbox}"
        )

    @pytest.mark.asyncio
    async def test_boost_xyxy_object_format_converts_correctly(self):
        """_qwen_boost_group: object {x,y,width,height} treated as xywh directly."""
        _clear()
        q = _FakeQ(qnum=1, bbox=[5, 5, 200, 100])
        boost_candidates = [(0, q, "no_student_answer")]

        resp = json.dumps([{
            "question_number": 1,
            "student_answer": "46",
            "answer_bbox": {"x": 10, "y": 10, "width": 60, "height": 30},
        }])

        with _PATCH_PRICING, patch("backend.pipeline.QwenVLClient") as MockCls:
            inst = MagicMock()
            inst._call.return_value = {
                "success": True, "content": resp,
                "usage": {"prompt_tokens": 50, "completion_tokens": 10}, "latency_ms": 100,
            }
            MockCls.return_value = inst

            result_qs = await _qwen_boost_group(
                "jid_obj", [q], boost_candidates,
                b"img", None, {}, "t", "p", "c",
                image_width=800, image_height=600,
            )

        assert result_qs[0].answer_bbox == [10.0, 10.0, 60.0, 30.0]
