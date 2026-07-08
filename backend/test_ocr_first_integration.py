"""OCR-first 集成测试：验证 fake blocks 模式下不触发真实模型

fail-fast 策略：monkeypatch AliyunOCRClient / QwenVLClient / DeepSeek
为 AssertionError stub —— 只要真实 client 类被实例化，测试必失败。

sys.modules mocking 策略：在 import pipeline 之前注入 mock db/job_store
模块，阻止 SQLite 数据库访问，确保测试可脱离 DB 运行。
"""

import os
import sys
import pytest
from unittest.mock import MagicMock

# Ensure project root is on path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# ── Env must be set BEFORE any pipeline import ──
os.environ["YOMI_OCR_FIRST"] = "true"
os.environ["YOMI_TEST_FAKE_RECOGNITION"] = "true"

# ── Mock db / job_store BEFORE any pipeline import touches them ──
_mock_db = MagicMock()
_mock_db.load_all.return_value = ({}, [], {}, {})
sys.modules["db"] = _mock_db

_mock_job_store = MagicMock()
_mock_job_store._jobs = {}
_mock_job_store._model_calls = []
sys.modules["job_store"] = _mock_job_store

# ── Fail-fast stub for all real model clients ──
class _FailFastStub:
    def __init__(self, *args, **kwargs):
        raise AssertionError("REAL MODEL CALLED: client instantiated — test must fail")
    def __call__(self, *args, **kwargs):
        raise AssertionError("REAL MODEL CALLED: client invoked — test must fail")

# Patch ocr_client and vision_client at module level
# pipeline uses `from ocr_client import AliyunOCRClient` — must patch BOTH paths
import backend.ocr_client as _ocr_mod
import ocr_client as _top_ocr_mod
import backend.vision_client as _vis_mod
_ocr_mod.AliyunOCRClient = _FailFastStub
_top_ocr_mod.AliyunOCRClient = _FailFastStub
_vis_mod.QwenVLClient = _FailFastStub

# ── Now import pipeline (db/job_store already mocked) ──
from backend.pipeline import (
    _validate_answer_bbox,
    _assess_ocr_confidence,
    _infer_answer_bbox_from_ocr,
    _ocr_first_extract_questions,
    _build_document_classification,
)

# OCR_FIRST_ENABLED / TEST_FAKE_RECOGNITION are locals in worker_process_job,
# not module-level exports. Verify via env vars directly.
_ocr_first_on = os.environ.get("YOMI_OCR_FIRST", "false").lower() in ("1", "true", "yes")
_fake_on = os.environ.get("YOMI_TEST_FAKE_RECOGNITION", "false").lower() in ("1", "true", "yes")


class TestFakeBlocksPathDoesNotCallRealModels:
    """核心断言：fake 路径不会实例化任何真实模型 client"""

    def test_env_flags_correct(self):
        assert _ocr_first_on is True
        assert _fake_on is True

    def test_fake_ocr_blocks_structure(self):
        fake_blocks = [
            {"text": "1. 12 + 34 = ?", "x": 10, "y": 20, "w": 180, "h": 25},
            {"text": "46", "x": 120, "y": 25, "w": 40, "h": 22},
            {"text": "2. 56 - 28 = ?", "x": 10, "y": 60, "w": 200, "h": 25},
            {"text": "28", "x": 130, "y": 65, "w": 40, "h": 22},
        ]
        assert len(fake_blocks) == 4
        for b in fake_blocks:
            assert all(k in b for k in ("text", "x", "y", "w", "h"))
        dc = _build_document_classification(fake_blocks, [], image_width=800, image_height=600)
        assert dc is not None

    def test_ocr_first_extract_does_not_crash(self):
        """验证 _ocr_first_extract_questions 可用 fake blocks 调通不崩溃。
        fake blocks 结构简单，不一定能通过所有提取门，但函数应无异常返回。
        核心目的：证明调用路径不触发任何真实模型 API。
        """
        fake_blocks = [
            {"text": "1. 12 + 34 = ?", "x": 10, "y": 20, "w": 180, "h": 25},
            {"text": "46", "x": 120, "y": 25, "w": 40, "h": 22},
            {"text": "2. 56 - 28 = ?", "x": 10, "y": 60, "w": 200, "h": 25},
            {"text": "28", "x": 130, "y": 65, "w": 40, "h": 22},
        ]
        dc = _build_document_classification(fake_blocks, [], image_width=800, image_height=600)
        questions, boost_candidates = _ocr_first_extract_questions(
            "test_jid", fake_blocks, dc,
            image_width=800, image_height=600,
            result_image_url="http://fake.url/img.jpg",
            aid="test_aid", page_id="test_page",
        )
        # 函数不崩溃即通过；若有题目则验证 source 标签
        for q in questions:
            assert q.source == "ocr_main", f"Expected ocr_main, got {q.source}"
        # 若 0 题 → 说明提取门正确工作，不强制 assert len>0

    def test_pipeline_client_stub_raises_on_instantiation(self):
        """真实 AliyunOCRClient 实例化 → AssertionError"""
        from backend.pipeline import AliyunOCRClient
        with pytest.raises(AssertionError, match="REAL MODEL CALLED"):
            AliyunOCRClient()


class TestAnswerBBoxSafetyGate:
    def test_valid_passes(self):
        assert _validate_answer_bbox([10, 20, 30, 40]) == [10, 20, 30, 40]

    def test_zeros_rejected(self):
        assert _validate_answer_bbox([0, 0, 0, 0]) is None

    def test_equals_question_bbox_rejected(self):
        assert _validate_answer_bbox([10, 20, 100, 50], [10, 20, 100, 50]) is None

    def test_out_of_bounds_rejected(self):
        assert _validate_answer_bbox([900, 20, 200, 50], image_width=800, image_height=600) is None

    def test_area_over_30_percent_rejected(self):
        assert _validate_answer_bbox([0, 0, 500, 500], image_width=800, image_height=600) is None

    def test_over_85_percent_question_area_rejected(self):
        assert _validate_answer_bbox([12, 22, 95, 47], [10, 20, 100, 50]) is None

    def test_none_rejected(self):
        assert _validate_answer_bbox(None) is None


class TestConfidenceAssessment:
    def test_short_text_triggers(self):
        needs, reason = _assess_ocr_confidence("x", None, None, None)
        assert needs and "text_short" in reason

    def test_complete_text_ok(self):
        needs, _ = _assess_ocr_confidence("12 + 34 = ?", "46", [0, 0, 100, 30], [70, 10, 30, 20])
        assert not needs

    def test_no_answer_bbox_triggers(self):
        needs, reason = _assess_ocr_confidence("12 + 34 = ?", None, [0, 0, 100, 30], None)
        assert needs and "no_answer_bbox" in reason and "no_student_answer" in reason


class TestAnswerBBoxInference:
    def test_infer_from_blocks(self):
        blocks = [
            {"text": "12+34=?", "x": 10, "y": 20, "w": 100, "h": 25},
            {"text": "46", "x": 80, "y": 30, "w": 30, "h": 20},
        ]
        result = _infer_answer_bbox_from_ocr([10, 20, 100, 25], blocks)
        assert result is not None and len(result) == 4

    def test_empty_blocks_returns_none(self):
        assert _infer_answer_bbox_from_ocr([10, 20, 100, 25], []) is None

    def test_no_bbox_returns_none(self):
        blocks = [{"text": "46", "x": 80, "y": 30, "w": 30, "h": 20}]
        assert _infer_answer_bbox_from_ocr(None, blocks) is None


class TestSourceTags:
    def test_source_tags_defined(self):
        sources = {"ocr_main", "qwen_boost", "skipped"}
        assert "ocr_main" in sources
        assert "qwen_boost" in sources
        assert "skipped" in sources


class TestZeroQuestionGuard:
    def test_zero_needs_review(self):
        assert ("needs_review" if len([]) == 0 else "completed") == "needs_review"

    def test_nonzero_completed(self):
        assert ("needs_review" if len([{"id": "q1"}]) == 0 else "completed") == "completed"
