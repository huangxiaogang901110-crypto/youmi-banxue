"""
阿里云 OCR 客户端 — 使用官方 SDK。
Phase 1：教育题目识别（RecognizeEduQuestionOcr），针对作业/试卷场景优化。
"""
import os
import json
import time
import base64
from pathlib import Path
from typing import Optional

# Lazy-load SDK
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


class AliyunOCRClient:
    """阿里云 OCR 文字识别客户端。输入图片字节 → 输出文字和坐标。"""

    def recognize(self, image_bytes: bytes) -> dict:
        """
        调用教育题目识别 API（RecognizeEduQuestionOcr）。
        针对作业/试卷题目场景专项优化，比通用 OCR 更准。
        Args:
            image_bytes: 图片原始字节（JPG/PNG），不需要 base64 编码。
        Returns:
            API 原始响应 dict，含 Data.content 和 Data.prism_wordsInfo
        """
        from alibabacloud_ocr_api20210707.models import RecognizeEduQuestionOcrRequest
        client = _get_client()
        req = RecognizeEduQuestionOcrRequest(
            need_rotate=False,
            body=image_bytes,
        )
        resp = client.recognize_edu_question_ocr(req)
        data = resp.body.data
        if isinstance(data, str):
            data = json.loads(data)
        return {"Data": data, "RequestId": resp.body.request_id}

    def extract_text_and_blocks(self, result: dict) -> dict:
        """从 OCR 结果提取文字和坐标块。"""
        data = result.get("Data", {})
        if isinstance(data, str):
            data = json.loads(data)

        content = data.get("content", "")
        blocks = []
        words = data.get("prism_wordsInfo", [])
        for w in words:
            blocks.append({
                "text": w.get("word", ""),
                "confidence": round(w.get("prob", 0) / 100.0, 3),
                "pos": [
                    w.get("x", 0), w.get("y", 0),
                    w.get("width", 0), w.get("height", 0),
                ],
            })
        return {"full_text": content, "blocks": blocks}
