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
        self.model = "deepseek-v4-flash"

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
            # 绕过全局 SOCKS5 代理（DeepSeek 国内直连）
            proxy_handler = request.ProxyHandler({})
            opener = request.build_opener(proxy_handler)
            resp = opener.open(req, timeout=30)
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
                    "cache_hit_tokens": usage.get("prompt_cache_hit_tokens", 0),
                    "cache_miss_tokens": usage.get("prompt_cache_miss_tokens", 0),
                },
            }

        except error.HTTPError as e:
            latency_ms = int((time.time() - t_start) * 1000)
            err_body = e.read().decode()[:200]
            return {
                "reply_text": f"[DeepSeek 异常: HTTP {e.code}]",
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

    def parse_homework_text(self, text: str) -> dict:
        """
        用 deepseek-v4-flash 解析微信群作业文本，按科目和任务拆分。
        """
        t_start = time.time()

        if not self._available():
            return {"subjects": [], "success": False, "error": "api_key_empty", "latency_ms": 0}

        prompt = (
            "你是一个作业解析助手。请将微信群作业文本按科目拆分，输出 JSON。\n"
            "核心规则：先判断是「单一科目」还是「多科作业」。\n"
            "  - 如果文本没有明确写多个科目（如语文、数学），只有编号列表 → 归为同一个科目。\n"
            "  - 「练习册」「白皮讲义」「单词」「打卡」是任务类型，不是科目名。\n"
            "  - 根据内容推断科目（U9/U10/剑桥/CPRA → 英语）。\n"
            "  - 每项任务保留原标题（如「练习册：完成U9剩下全部」），一句说清楚。\n"
            "  - 只输出 JSON 数组，格式：[{\"name\":\"科目\",\"tasks\":[\"任务1\",\"任务2\"]}]\n\n"
            f"作业文本：\n{text}"
        )

        body = json.dumps({
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "system", "content": "你是一个作业解析助手，输出纯 JSON，不解释。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 800,
            "temperature": 0.1,
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
            # 绕过全局 SOCKS5 代理（DeepSeek 国内直连）
            proxy_handler = request.ProxyHandler({})
            opener = request.build_opener(proxy_handler)
            resp = opener.open(req, timeout=30)
            data = json.loads(resp.read().decode())
            latency_ms = int((time.time() - t_start) * 1000)

            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                return {"subjects": [], "success": False, "error": "empty_response", "latency_ms": latency_ms}

            import re
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                subjects = json.loads(json_match.group())
            else:
                subjects = json.loads(content)

            return {
                "subjects": subjects,
                "success": True,
                "latency_ms": latency_ms,
                "usage": data.get("usage", {}),
            }

        except error.HTTPError as e:
            latency_ms = int((time.time() - t_start) * 1000)
            return {"subjects": [], "success": False, "error": f"HTTP {e.code}: {e.read().decode()[:300]}", "latency_ms": latency_ms}
        except Exception as e:
            latency_ms = int((time.time() - t_start) * 1000)
            return {"subjects": [], "success": False, "error": str(e)[:300], "latency_ms": latency_ms}
