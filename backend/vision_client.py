"""
Qwen-VL 视觉理解客户端 — Phase 0 任务 7
基准 Table 11 R3: 只做语义理解，不画框
通过 DashScope API 调用，key 为空时返回占位文本
"""

import os
import time
import base64
import json
from typing import Optional, List
from urllib import request, error


class QwenVLClient:
    """DashScope Qwen-VL 客户端"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("QWEN_DASHSCOPE_API_KEY", "")
        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.model = "qwen-vl-plus"

    def _available(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 10 and self.api_key.startswith("sk-"))

    def analyze_question(
        self,
        image_bytes: bytes,
        bbox: List[float],
        question_text: str,
    ) -> dict:
        """
        分析题目图片区域，返回语义描述。

        Args:
            image_bytes: 原始图片字节
            bbox: [x, y, w, h] 题目区域
            question_text: OCR 识别的题目文本

        Returns:
            {"visual_description": "...", "latency_ms": int, "success": bool}
            如果 API key 为空或调用失败，visual_description 为占位文本
        """
        t_start = time.time()

        if not self._available():
            latency_ms = int((time.time() - t_start) * 1000)
            return {
                "visual_description": f"[Qwen-VL 未接入] 题目文字: {question_text[:80]}",
                "latency_ms": latency_ms,
                "success": False,
                "error": "api_key_empty",
            }

        # Build prompt — 基准约束：只描述图形/表格/公式，不画框
        x, y, w, h = [int(v) for v in bbox]
        prompt = (
            "请分析题目图片中标出的区域内容。"
            "只描述其中包含的图形、表格、线段图、公式符号、题目结构；"
            "不要生成答案，不要画坐标框，不要解题。"
            f"\n\nOCR 已识别的文字：{question_text[:300]}"
            f"\n\n区域坐标参考：({x},{y}) 宽{w}高{h}"
        )

        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        image_url = f"data:image/jpeg;base64,{image_b64}"

        body = json.dumps({
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": prompt},
                ],
            }],
            "max_tokens": 500,
        }).encode("utf-8")

        try:
            req = request.Request(
                f"{self.base_url}/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp = request.urlopen(req, timeout=30)
            data = json.loads(resp.read().decode())
            latency_ms = int((time.time() - t_start) * 1000)

            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            return {
                "visual_description": content.strip() if content else "[Qwen-VL 返回空]",
                "latency_ms": latency_ms,
                "success": True,
            }

        except error.HTTPError as e:
            latency_ms = int((time.time() - t_start) * 1000)
            err_body = e.read().decode()[:200]
            return {
                "visual_description": f"[Qwen-VL HTTP {e.code}] {question_text[:80]}",
                "latency_ms": latency_ms,
                "success": False,
                "error": f"HTTP {e.code}: {err_body}",
            }
        except Exception as e:
            latency_ms = int((time.time() - t_start) * 1000)
            return {
                "visual_description": f"[Qwen-VL 异常] {question_text[:80]}",
                "latency_ms": latency_ms,
                "success": False,
                "error": str(e)[:200],
            }
