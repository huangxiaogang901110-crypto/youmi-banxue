"""
通用 OCR 客户端 — RecognizeGeneral，成本约 ¥0.003/次。
提供原始 prism_wordsInfo blocks，供 bbox fusion 使用。
"""
import os, json, time
from typing import Optional

_client = None


def _get_client():
    global _client
    if _client is None:
        from alibabacloud_ocr_api20210707.client import Client
        from alibabacloud_tea_openapi.models import Config
        key_id = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
        key_secret = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
        if not key_id or not key_secret:
            raise ValueError("ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET 未设置")
        config = Config(
            access_key_id=key_id,
            access_key_secret=key_secret,
            endpoint="ocr-api.cn-hangzhou.aliyuncs.com",
        )
        _client = Client(config)
    return _client


class GeneralOCRClient:
    """通用文字识别客户端。输入图片字节 → 输出 prism_wordsInfo blocks。"""

    def _available(self) -> bool:
        return bool(os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", ""))

    def recognize(self, image_bytes: bytes) -> dict:
        """
        调用通用文字识别 API（RecognizeGeneral）。
        返回 {"blocks": [...], "success": bool, "latency_ms": int}
        """
        from alibabacloud_ocr_api20210707.models import RecognizeGeneralRequest
        t0 = time.time()
        try:
            client = _get_client()
            req = RecognizeGeneralRequest(body=image_bytes)
            resp = client.recognize_general(req)
            latency_ms = int((time.time() - t0) * 1000)
            data = resp.body.data
            if isinstance(data, str):
                data = json.loads(data)
            words = data.get("prism_wordsInfo", [])
            blocks = []
            for w in words:
                blocks.append({
                    "text": w.get("word", ""),
                    "x": w.get("x", 0), "y": w.get("y", 0),
                    "w": w.get("width", 0), "h": w.get("height", 0),
                    "confidence": w.get("prob", 0) / 100.0 if w.get("prob") else 0,
                    "angle": w.get("angle", 0),
                })
            return {
                "blocks": blocks,
                "success": True,
                "latency_ms": latency_ms,
                "full_text": data.get("content", ""),
                "image_width": data.get("width", 0),
                "image_height": data.get("height", 0),
            }
        except Exception as e:
            return {
                "blocks": [],
                "success": False,
                "latency_ms": int((time.time() - t0) * 1000),
                "error": str(e)[:200],
            }
