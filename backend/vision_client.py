"""
Qwen-VL 视觉理解客户端 — Phase 1
支持全图识题（extract_questions）和逐题视觉分析（analyze_question）
通过 DashScope API 调用，模型：qwen-vl-max
"""
import os, time, base64, json, re
from typing import Optional, List
from urllib import request, error


class QwenVLClient:
    """DashScope Qwen-VL 客户端"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("QWEN_DASHSCOPE_API_KEY", "")
        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.model = "qwen-vl-max"

    def _available(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 10 and self.api_key.startswith("sk-"))

    def _call(self, image_bytes: bytes, prompt: str, max_tokens: int = 2000) -> dict:
        """底层调用，返回原始 API 响应。"""
        t_start = time.time()
        if not self._available():
            return {
                "content": "",
                "success": False,
                "error": "api_key_empty",
                "latency_ms": 0,
            }

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
            "max_tokens": max_tokens,
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
            # 绕过全局 SOCKS5 代理（DashScope 国内直连）
            proxy_handler = request.ProxyHandler({})
            opener = request.build_opener(proxy_handler)
            resp = opener.open(req, timeout=60)
            data = json.loads(resp.read().decode())
            latency_ms = int((time.time() - t_start) * 1000)
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {
                "content": content.strip(),
                "success": True,
                "latency_ms": latency_ms,
                "usage": data.get("usage", {}),
            }
        except error.HTTPError as e:
            latency_ms = int((time.time() - t_start) * 1000)
            err_body = e.read().decode()[:300]
            return {
                "content": "",
                "success": False,
                "error": f"HTTP {e.code}: {err_body}",
                "latency_ms": latency_ms,
            }
        except Exception as e:
            latency_ms = int((time.time() - t_start) * 1000)
            return {
                "content": "",
                "success": False,
                "error": str(e)[:300],
                "latency_ms": latency_ms,
            }

    def extract_questions(self, image_bytes: bytes) -> dict:
        """
        全图识题：直接让 Qwen-VL 识别图片中所有题目。
        返回 {"questions": [...], "success": bool, "latency_ms": int, "error": str}
        其中 questions 数组每项含 number, type, content。
        """
        prompt = (
            "请识别这张作业图片中的所有题目。对每道题，输出：题号、题目类型（选择题/填空题/计算题/应用题等）、"
            "题目文字内容。请用 JSON 数组格式输出，格式为 "
            '[{"number":题号,"type":"类型","content":"题目文字"}]。'
            "只输出 JSON 数组，不要有其他文字。"
        )
        r = self._call(image_bytes, prompt, max_tokens=2000)
        if not r["success"]:
            return {"questions": [], "success": False, "latency_ms": r["latency_ms"], "error": r.get("error", "unknown")}

        # 解析 JSON
        content = r["content"]
        try:
            # 尝试提取 JSON 数组
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                questions = json.loads(json_match.group())
            else:
                questions = json.loads(content)
        except json.JSONDecodeError:
            return {
                "questions": [],
                "success": False,
                "latency_ms": r["latency_ms"],
                "error": f"json_parse_error: {content[:200]}",
            }
        return {
            "questions": questions,
            "success": True,
            "latency_ms": r["latency_ms"],
            "error": None,
            "raw_content": content,
            "usage": r.get("usage", {}),
        }

    def analyze_question(
        self,
        image_bytes: bytes,
        bbox: List[float],
        question_text: str,
    ) -> dict:
        """
        分析题目图片区域，返回语义描述。
        （当全图识题已提取所有题目时，本方法可跳过以节省 token）
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

        x, y, w, h = [int(v) for v in bbox]
        prompt = (
            "请分析题目图片中标出的区域内容。"
            "只描述其中包含的图形、表格、线段图、公式符号、题目结构；"
            "不要生成答案，不要画坐标框，不要解题。"
            f"\n\nOCR 已识别的文字：{question_text[:300]}"
            f"\n\n区域坐标参考：({x},{y}) 宽{w}高{h}"
        )

        r = self._call(image_bytes, prompt, max_tokens=500)
        if not r["success"]:
            return {
                "visual_description": f"[Qwen-VL 异常] {question_text[:80]}",
                "latency_ms": r["latency_ms"],
                "success": False,
                "error": r.get("error", "unknown"),
            }
        return {
            "visual_description": r["content"] if r["content"] else f"[Qwen-VL 返回空] {question_text[:80]}",
            "latency_ms": r["latency_ms"],
            "success": True,
            "usage": r.get("usage", {}),
        }
