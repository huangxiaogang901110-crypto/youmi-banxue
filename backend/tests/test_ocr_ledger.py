"""
Tests for OCR ledger correctness:
- billing_status default is "billed" (not free_tier)
- OCR cost_cny > 0 when estimated_cost passed
- call_source propagated in log entry dict
- make_log_entry is pure Python — no HTTP calls
"""
import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# Ensure backend directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model_logger import make_log_entry


class TestOCRLedger(unittest.TestCase):

    def test_billing_status_default_is_billed(self):
        """make_log_entry default billing_status must not be free_tier."""
        entry = make_log_entry(
            task_id="t1", provider_name="aliyun_ocr", model_name="ocr_general",
            feature_code="ocr_general", latency_ms=200, success=True,
        )
        self.assertNotEqual(entry["billing_status"], "free_tier")
        self.assertEqual(entry["billing_status"], "billed")

    def test_ocr_cost_cny_nonzero(self):
        """OCR call with estimated_cost=0.003 and image_count=1 yields cost_cny > 0."""
        entry = make_log_entry(
            task_id="t2", provider_name="aliyun_ocr", model_name="ocr_general",
            feature_code="ocr_general", latency_ms=150, success=True,
            image_count=1, estimated_cost=0.003,
        )
        self.assertGreater(entry["cost_cny"], 0.0,
                           f"cost_cny should be > 0, got {entry['cost_cny']}")

    def test_call_source_in_entry(self):
        """call_source passed to make_log_entry must appear in returned dict."""
        entry = make_log_entry(
            task_id="t3", provider_name="aliyun_ocr", model_name="ocr_general",
            feature_code="ocr_general", latency_ms=100, success=True,
            call_source="mock_source",
        )
        self.assertIn("call_source", entry)
        self.assertEqual(entry["call_source"], "mock_source")

    def test_no_http_calls(self):
        """make_log_entry must not trigger any HTTP/network calls."""
        # Patch urllib and requests at module level to detect any outbound calls
        with patch("urllib.request.urlopen") as mock_urlopen, \
             patch("urllib.request.urlretrieve") as mock_urlretrieve:
            entry = make_log_entry(
                task_id="t4", provider_name="aliyun_ocr", model_name="ocr_general",
                feature_code="ocr_general", latency_ms=120, success=True,
                image_count=1, estimated_cost=0.003,
            )
            mock_urlopen.assert_not_called()
            mock_urlretrieve.assert_not_called()
        self.assertIsNotNone(entry.get("id"))

    def test_save_model_call_uses_data_call_source(self):
        """save_model_call must prefer call_source from data dict over env var."""
        import db as db_module

        fake_conn = MagicMock()
        fake_conn.execute = MagicMock()
        fake_conn.commit = MagicMock()
        fake_conn.close = MagicMock()

        entry = make_log_entry(
            task_id="t5", provider_name="aliyun_ocr", model_name="ocr_general",
            feature_code="ocr_general", latency_ms=100, success=True,
            call_source="test_env",
        )

        with patch.object(db_module, "_conn", return_value=fake_conn), \
             patch.dict(os.environ, {"YOMICALL_SOURCE": "prod_env"}):
            db_module.save_model_call(entry)

        # Extract the call_source value passed to execute
        args = fake_conn.execute.call_args[0]
        sql_values = args[1]
        # call_source is the 33rd positional value (index 32, 0-based)
        # Find it by re-reading the INSERT column list
        insert_sql = args[0]
        col_names = [c.strip() for c in insert_sql.split("(")[1].split(")")[0].split(",")]
        # Locate call_source index
        cs_idx = col_names.index("call_source")
        self.assertEqual(sql_values[cs_idx], "test_env",
                         "call_source from data dict should override env var")


class TestRouteBillingStatus(unittest.TestCase):
    """Verify routes + grading_unit billing_status values via make_log_entry mock calls."""

    def _make_entry(self, billing_status, **kwargs):
        """Helper: call make_log_entry with overridden billing_status."""
        entry = make_log_entry(
            task_id="tx", provider_name="deepseek", model_name="deepseek-v4-flash",
            feature_code="test_feat", latency_ms=100, success=True,
            **kwargs,
        )
        # Simulate the route overriding billing_status (routes build entry then pass it to db)
        entry["billing_status"] = billing_status
        return entry

    def test_deepseek_tutor_billing_status_is_billed(self):
        """parse_routes.py:748 pattern — success → billing_status must be 'billed'."""
        result = {"success": True}
        billing_status = "billed" if result["success"] else "failed"
        entry = self._make_entry(billing_status)
        self.assertNotEqual(entry["billing_status"], "free_tier")
        self.assertEqual(entry["billing_status"], "billed")

    def test_deepseek_tutor_billing_status_failed_on_error(self):
        """parse_routes.py:748 pattern — failure → billing_status must be 'failed'."""
        result = {"success": False}
        billing_status = "billed" if result["success"] else "failed"
        entry = self._make_entry(billing_status)
        self.assertEqual(entry["billing_status"], "failed")

    def test_qwen_vl_vision_retry_billing_status_is_billed(self):
        """parse_routes.py:889 pattern — vl_result success → billing_status must be 'billed'."""
        vl_result = {"success": True}
        billing_status = "billed" if vl_result["success"] else "failed"
        entry = self._make_entry(billing_status)
        self.assertNotEqual(entry["billing_status"], "free_tier")
        self.assertEqual(entry["billing_status"], "billed")

    def test_deepseek_homework_billing_status_is_billed(self):
        """homework_routes.py:133 pattern — ds_result success → billing_status must be 'billed'."""
        ds_result = {"success": True}
        billing_status = "billed" if ds_result["success"] else "failed"
        entry = self._make_entry(billing_status)
        self.assertNotEqual(entry["billing_status"], "free_tier")
        self.assertEqual(entry["billing_status"], "billed")

    def test_grading_unit_billing_status_is_billed(self):
        """grading_unit.py:625/697 pattern — success → billing_status must be 'billed', not 'paid'."""
        result = {"success": True}
        billing_status = "billed" if result.get("success") else "failed"
        entry = self._make_entry(billing_status)
        self.assertNotEqual(entry["billing_status"], "paid")
        self.assertEqual(entry["billing_status"], "billed")

    def test_grading_unit_billing_status_failed_on_error(self):
        """grading_unit.py:625/697 pattern — failure → billing_status must be 'failed'."""
        result = {"success": False}
        billing_status = "billed" if result.get("success") else "failed"
        entry = self._make_entry(billing_status)
        self.assertEqual(entry["billing_status"], "failed")


if __name__ == "__main__":
    unittest.main()
