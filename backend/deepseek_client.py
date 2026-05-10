"""
DeepSeek 辅导客户端 — Phase 0 任务 8
基准 Table 11 R4: 解题/讲解/错因分析，不接收图片 Base64
API key 为空时优雅降级，返回占位文本
"""

import os
import time
import json
from typing import Optional, List
from urllib import request, error


class DeepSeekClient:
    """DeepSeek Chat API 客户端"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = "https://api.deepseek.com/v1"
        self.model = "deepseek-chat"

    def _available(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 10 and self.api_key.startswith("sk-"))

    def tutor(
        self,
        messages: List[dict],
        max_tokens: int = 1024,
    ) -> dict:
        """
        调用 DeepSeek 辅导。

        Args:
            messages: OpenAI 格式消息列表 [{"role":"system",...}, ...]
            max_tokens: 最大输出 token

        Returns:
            {"reply_text": str, "latency_ms": int, "success": bool, "usage": dict|None}
        """
        t_start = time.time()

        if not self._available():
            latency_ms = int((time.time() - t_start) * 1000)
            last_user = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    last_user = m.get("content", "")
                    break
            return {
                "reply_text": f"[DeepSeek 未接入] 收到问题：{last_user[:100]}",
                "latency_ms": latency_ms,
                "success": False,
                "error": "api_key_empty",
                "usage": None,
            }

        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.7,
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
            resp = request.urlopen(req, timeout=60)
            data = json.loads(resp.read().decode())
            latency_ms = int((time.time() - t_start) * 1000)

            choice = data.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content", "")
            usage = data.get("usage", {})

            return {
                "reply_text": content.strip() if content else "[DeepSeek 返回空]",
                "latency_ms": latency_ms,
                "success": True,
                "usage": {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                },
            }

        except error.HTTPError as e:
            latency_ms = int((time.time() - t_start) * 1000)
            err_body = e.read().decode()[:200]
            return {
                "reply_text": f"[DeepSeek HTTP {e.code}]",
                "latency_ms": latency_ms,
                "success": False,
                "error": f"HTTP {e.code}: {err_body}",
                "usage": None,
            }
        except Exception as e:
            latency_ms = int((time.time() - t_start) * 1000)
            return {
                "reply_text": f"[DeepSeek 异常]",
                "latency_ms": latency_ms,
                "success": False,
                "error": str(e)[:200],
                "usage": None,
            }
