import json
import os
from unittest.mock import patch

from ocr_block_export import export_ocr_blocks_if_enabled


def _cleanup(job_id):
    path = f"/tmp/ocrblocks_{job_id}.json"
    if os.path.exists(path):
        os.remove(path)
    return path


def test_export_skips_when_env_not_set(monkeypatch):
    monkeypatch.delenv("YOMI_EXPORT_OCR_BLOCKS", raising=False)
    monkeypatch.delenv("YOMIEXPORTOCRBLOCKS", raising=False)
    path = _cleanup("test_job_env_off")

    export_ocr_blocks_if_enabled("test_job_env_off", [{"text": "1+1=", "x": 1, "y": 2, "w": 3, "h": 4}])

    assert not os.path.exists(path)


def test_export_writes_file_for_canonical_env(monkeypatch):
    monkeypatch.setenv("YOMI_EXPORT_OCR_BLOCKS", "1")
    monkeypatch.delenv("YOMIEXPORTOCRBLOCKS", raising=False)
    job_id = "test_job_1"
    path = _cleanup(job_id)
    assert path.endswith(f"ocrblocks_{job_id}.json")
    blocks = [{"text": "1+1=", "x": 1, "y": 2, "w": 3, "h": 4}]

    try:
        export_ocr_blocks_if_enabled(job_id, blocks)

        assert os.path.exists(path)
        with open(path, "r", encoding="utf-8") as export_file:
            payload = json.load(export_file)
        assert payload["job_id"] == job_id
        assert payload["created_at"]
        assert payload["blocks"] == blocks
    finally:
        _cleanup(job_id)


def test_export_writes_file_for_legacy_env_without_confidence(monkeypatch):
    monkeypatch.delenv("YOMI_EXPORT_OCR_BLOCKS", raising=False)
    monkeypatch.setenv("YOMIEXPORTOCRBLOCKS", "1")
    job_id = "test_job_2"
    path = _cleanup(job_id)
    assert path.endswith(f"ocrblocks_{job_id}.json")
    blocks = [{"text": "3+5=", "x": 10, "y": 20, "w": 30, "h": 40}]

    try:
        export_ocr_blocks_if_enabled(job_id, blocks)

        assert os.path.exists(path)
        with open(path, "r", encoding="utf-8") as export_file:
            payload = json.load(export_file)
        assert payload["blocks"] == blocks
        assert "confidence" not in payload["blocks"][0]
    finally:
        _cleanup(job_id)


def test_export_swallows_write_errors_and_warns(monkeypatch):
    monkeypatch.setenv("YOMI_EXPORT_OCR_BLOCKS", "1")
    monkeypatch.delenv("YOMIEXPORTOCRBLOCKS", raising=False)
    warnings = []

    def _fake_warning(message):
        warnings.append(message)

    with patch("ocr_block_export.warning", _fake_warning), patch(
        "builtins.open",
        side_effect=PermissionError("no write permission"),
    ):
        export_ocr_blocks_if_enabled("test_job_warn", [{"text": "7-2=", "x": 0, "y": 0, "w": 1, "h": 1}])

    assert warnings
    assert "OCR blocks export failed for test_job_warn" in warnings[0]
