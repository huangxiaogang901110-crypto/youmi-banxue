"""
Qwen-VL 视觉理解客户端 — Phase 1 真实 AI 接入
支持全图识题（extract_questions）和逐题视觉分析（analyze_question）
通过 DashScope API 调用，模型：qwen-vl-max
图片传输：OSS 签名 URL 优先（不传 base64），OSS 不可用时回落 base64
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

    def _call(self, image_bytes: bytes = None, image_url: str = None, prompt: str = "", max_tokens: int = 2000, timeout: int = 30, temperature: float = None) -> dict:
        """底层调用，返回原始 API 响应。
        image_url 优先（OSS 签名 URL），否则 image_bytes → base64 data URL。
        timeout: HTTP 超时秒数，默认 30s。
        """
        t_start = time.time()
        if not self._available():
            return {
                "content": "",
                "success": False,
                "error": "api_key_empty",
                "latency_ms": 0,
            }

        if image_url:
            _url = image_url
        elif image_bytes:
            image_b64 = base64.b64encode(image_bytes).decode("ascii")
            _url = f"data:image/jpeg;base64,{image_b64}"
        else:
            return {
                "content": "",
                "success": False,
                "error": "no_image_input",
                "latency_ms": 0,
            }

        body_dict = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _url}},
                    {"type": "text", "text": prompt},
                ],
            }],
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            body_dict["temperature"] = temperature
        body = json.dumps(body_dict).encode("utf-8")

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
            resp = opener.open(req, timeout=timeout)
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

    def extract_questions(self, image_bytes: bytes = None, image_url: str = None, timeout: int = 30) -> dict:
        """
        全图识题：直接让 Qwen-VL 识别图片中所有题目。
        image_url 优先（OSS 签名 URL），否则 image_bytes → base64。
        timeout: HTTP 超时秒数，默认 30s。
        返回 {"questions": [...], "success": bool, "latency_ms": int, "error": str}
        其中 questions 数组每项含 number, type, content。
        """
        prompt = (
            "请识别这张作业图片中的所有题目。对每道题，输出：题号、题目类型、题目文字内容、孩子手写/填写的答案。"
            "如果题目按大题/题组分组（如\"一、口算\"\"二、填空\"），请识别大题标题和分组关系。"
            "请用 JSON 数组格式输出，格式为 "
            '[{"number":题号,"type":"类型","content":"题目文字","student_answer":"孩子答案",'
            '"section_title":"大题标题或null","section_index":大题序号(从1开始),"sub_index":大题内小题序号(从1开始)}]。'
            "如果孩子没有写、看不清、被遮挡，student_answer 填 null。"
            "不要把题目自带的例题答案、解析文字当成孩子答案。"
            "如果只有单一题型没有分组，section_title 填 null。"
            "只输出 JSON 数组，不要有其他文字。"
        )
        r = self._call(image_bytes=image_bytes, image_url=image_url, prompt=prompt, max_tokens=3000, timeout=timeout)
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
        image_bytes: bytes = None,
        image_url: str = None,
        bbox: List[float] = None,
        question_text: str = "",
    ) -> dict:
        """
        分析题目图片区域，返回语义描述。
        image_url 优先（OSS 签名 URL），否则 image_bytes → base64。
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

        bbox = bbox or [0, 0, 0, 0]
        x, y, w, h = [int(v) for v in bbox]
        prompt = (
            "分析题目图片区域。必须返回严格 JSON，不要任何额外文字：\n"
            '{"visual_description":"题目图形/表格/符号描述",'
            '"student_answer":"孩子手写答案或null"}\n'
            "规则：\n"
            "- visual_description：只描述图形/表格/符号，不解题\n"
            "- student_answer：区域内孩子手写笔迹原文，没有则填 null\n"
            "- 只输出一行 JSON，不加解释、不加 markdown 代码块\n"
            f"OCR 文字：{question_text[:300]}\n"
            f"坐标：({x},{y}) 宽{w}高{h}"
        )

        r = self._call(image_bytes=image_bytes, image_url=image_url, prompt=prompt, max_tokens=500)
        if not r["success"]:
            return {
                "visual_description": f"[Qwen-VL 异常] {question_text[:80]}",
                "student_answer": None,
                "latency_ms": r["latency_ms"],
                "success": False,
                "error": r.get("error", "unknown"),
            }
        content = r.get("content", "")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # JSON 失败 → 正则兜底提取 student_answer
            sa = None
            for pat in [
                r'"student_answer"\s*:\s*"([^"]+)"',
                r"student_answer\s*:\s*'([^']+)'",
                r'孩子答案[：:]\s*(.+?)(?:\n|$)',
                r'student_answer\s*[：:]\s*(.+?)(?:\n|$)',
            ]:
                m = re.search(pat, content)
                if m and m.group(1).strip().lower() not in ("null", "none", "无", "空", ""):
                    sa = m.group(1).strip()
                    break
            parsed = {"visual_description": content, "student_answer": sa}
        return {
            "visual_description": parsed.get("visual_description") or f"[Qwen-VL 返回空] {question_text[:80]}",
            "student_answer": parsed.get("student_answer") if parsed.get("student_answer") and parsed.get("student_answer") != "null" else None,
            "latency_ms": r["latency_ms"],
            "success": True,
            "usage": r.get("usage", {}),
        }
