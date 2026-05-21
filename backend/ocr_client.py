# -*- coding: utf-8 -*-
"""
阿里云通用 OCR 客户端 — 使用官方 SDK RecognizeGeneral。
低成本 blocks/坐标层，不承担完整语义和判题。
"""
import os
import json
import time
from pathlib import Path
from typing import Optional

# Lazy-load SDK
_client = None
_client_lock = None

def _get_client():
    global _client, _client_lock
    # Always recreate client (SOCKS5 proxy bypass requires fresh session)
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
        read_timeout=int(os.getenv("YOMI_GENERAL_OCR_TIMEOUT_SECONDS", "3")) * 1000,
        connect_timeout=3000,
    )
    _client = Client(config)
    return _client


class AliyunOCRClient:
    """阿里云通用 OCR 文字识别客户端（RecognizeGeneral）。¥0.003/次。"""

    def recognize(self, image_bytes: bytes) -> dict:
        """
        调用通用文字识别 API（RecognizeGeneral）。
        返回 API 原始响应 dict。
        timeout 由 SDK Config read_timeout 控制（默认 3s）。
        """
        import os as _os, urllib.request as _ur
        from alibabacloud_ocr_api20210707.models import RecognizeGeneralRequest

        # Bypass global SOCKS5 proxy for OCR API
        _old_https = _os.environ.pop("HTTPS_PROXY", None)
        _old_http = _os.environ.pop("HTTP_PROXY", None)
        _old_all = _os.environ.pop("ALL_PROXY", None)
        try:
            client = _get_client()
            req = RecognizeGeneralRequest(body=image_bytes)
            t0 = time.time()
            resp = client.recognize_general(req)
            elapsed_ms = int((time.time() - t0) * 1000)
        finally:
            if _old_https is not None:
                _os.environ["HTTPS_PROXY"] = _old_https
            if _old_http is not None:
                _os.environ["HTTP_PROXY"] = _old_http
            if _old_all is not None:
                _os.environ["ALL_PROXY"] = _old_all

        # Parse response to unified format
        blocks = []
        if resp.body and resp.body.data:
            data = json.loads(resp.body.data) if isinstance(resp.body.data, str) else resp.body.data
            for w in data.get('prism_wordsInfo', []):
                blocks.append({
                    'text': w.get('word', ''),
                    'x': w.get('x', 0),
                    'y': w.get('y', 0),
                    'w': w.get('width', 0),
                    'h': w.get('height', 0),
                })
        return {
            "success": True,
            "latency_ms": elapsed_ms,
            "blocks": blocks,
            "text": ' '.join(b['text'] for b in blocks),
            "block_count": len(blocks),
            "RequestId": getattr(resp.body, 'request_id', '') if resp.body else '',
        }

    def extract_text_and_blocks(self, ocr_result: dict) -> dict:
        """从 recognize() 返回结果中提取文本和 blocks（兼容旧接口）"""
        return {
            "text": ocr_result.get("text", ""),
            "blocks": ocr_result.get("blocks", []),
            "block_count": ocr_result.get("block_count", 0),
        }
