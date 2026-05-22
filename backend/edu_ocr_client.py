"""
教育 OCR 客户端 — 试卷结构识别 + 题目 OCR + 单题 ROI 识别。
方案 A 第一阶段：
  - PaperStructed first（版面 block bbox）
  - QuestionOcr fallback（单题/局部 OCR）
  - 文字提取由 Qwen-VL contact sheet 承担
"""
import json
import time
from typing import Optional, List, Dict, Any
import io


# ── 鉴权客户端 ──
_edu_client = None

def _get_edu_client():
    global _edu_client
    if _edu_client is not None:
        return _edu_client
    from alibabacloud_ocr_api20210707.client import Client as OCRClient
    from alibabacloud_tea_openapi.models import Config
    import os

    key_id = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
    key_secret = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
    if not key_id or not key_secret:
        raise ValueError("ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET 未设置")
    config = Config(
        access_key_id=key_id,
        access_key_secret=key_secret,
        endpoint="ocr-api.cn-hangzhou.aliyuncs.com",
    )
    _edu_client = OCRClient(config)
    return _edu_client


# ── 统一输出结构 ──

class RawQuestionRegion:
    """识别产生的单题区域。"""

    __slots__ = ("question_index", "question_bbox", "ocr_text", "ocr_blocks", "confidence", "bbox_source")

    def __init__(
        self,
        question_index: int,
        question_bbox: List[float],
        ocr_text: str = "",
        ocr_blocks: Optional[List[Dict[str, Any]]] = None,
        confidence: float = 0.0,
        bbox_source: str = "edu_ocr",
    ):
        self.question_index = question_index
        self.question_bbox = list(question_bbox)
        self.ocr_text = ocr_text
        self.ocr_blocks = ocr_blocks or []
        self.confidence = confidence
        self.bbox_source = bbox_source

    def to_dict(self) -> dict:
        return {
            "question_index": self.question_index,
            "question_bbox": self.question_bbox,
            "ocr_text": self.ocr_text,
            "ocr_blocks": self.ocr_blocks,
            "confidence": self.confidence,
            "bbox_source": self.bbox_source,
        }


class EduOCRClient:
    """教育 OCR 客户端：PaperStructed first + QuestionOcr fallback。"""

    # 题间 Y 轴合并容差（像素）
    MERGE_Y_TOLERANCE = 30
    # 最小区域面积（平方像素），过滤碎片
    MIN_AREA = 500
    # 最小区域高度
    MIN_HEIGHT = 20

    def paper_cut(self, image_bytes: bytes) -> dict:
        """
        Phase A1：调用 RecognizeEduPaperStructed 获取版面 block，
        按 Y 坐标分组 → 合并相邻 block → 生成 question regions。

        失败时回退 QuestionOcr。
        """
        t_start = time.time()
        try:
            regions = self._paper_structured_cut(image_bytes)
            source = "paper_structured"
        except Exception as e:
            # 回退 QuestionOcr
            try:
                regions, q_ms = self._question_ocr_cut(image_bytes)
                source = "question_ocr"
            except Exception as e2:
                return {
                    "regions": [],
                    "success": False,
                    "latency_ms": int((time.time() - t_start) * 1000),
                    "error": f"PaperStructed: {e}, QuestionOcr: {e2}",
                    "raw": None,
                }

        if not regions:
            try:
                regions, q_ms = self._question_ocr_cut(image_bytes)
                source = "question_ocr"
            except Exception:
                pass

        return {
            "regions": regions,
            "success": len(regions) > 0,
            "latency_ms": int((time.time() - t_start) * 1000),
            "raw": {"source": source, "region_count": len(regions)},
            "error": None if regions else f"no_regions_from_{source}",
        }

    def _paper_structured_cut(self, image_bytes: bytes) -> List[RawQuestionRegion]:
        """PaperStructed → doc_layout blocks → 合并为 question regions。"""
        from alibabacloud_ocr_api20210707.models import RecognizeEduPaperStructedRequest

        client = _get_edu_client()
        req = RecognizeEduPaperStructedRequest(need_rotate=False)
        req.body = io.BytesIO(image_bytes)
        resp = client.recognize_edu_paper_structed(req)
        data = resp.body.data
        if isinstance(data, str):
            data = json.loads(data)

        layouts = data.get("doc_layout", [])
        if not layouts:
            return []

        # 1. 提取所有 text/special_text block 的 bbox
        blocks = []
        for blk in layouts:
            lt = blk.get("layout_type", "")
            if lt not in ("text", "special_text", "formula"):
                continue
            pos = blk.get("pos", [])
            if not pos or len(pos) < 4:
                continue
            xs = [p["x"] if isinstance(p, dict) else p[0] for p in pos]
            ys = [p["y"] if isinstance(p, dict) else p[1] for p in pos]
            x, y = min(xs), min(ys)
            w, h = max(xs) - x, max(ys) - y
            if w * h < self.MIN_AREA or h < self.MIN_HEIGHT:
                continue
            blocks.append({"x": x, "y": y, "w": w, "h": h})

        if not blocks:
            return []

        # 2. 按 Y 排序
        blocks.sort(key=lambda b: (b["y"], b["x"]))

        # 3. 合并 Y 轴接近的相邻 block
        groups = []
        current = [blocks[0]]
        for blk in blocks[1:]:
            last = current[-1]
            last_bottom = last["y"] + last["h"]
            if blk["y"] - last_bottom <= self.MERGE_Y_TOLERANCE:
                current.append(blk)
            else:
                groups.append(current)
                current = [blk]
        groups.append(current)

        # 4. 每组生成一个 RawQuestionRegion
        regions = []
        for qi, group in enumerate(groups):
            xs = [b["x"] for b in group]
            ys = [b["y"] for b in group]
            rights = [b["x"] + b["w"] for b in group]
            bottoms = [b["y"] + b["h"] for b in group]
            bbox = [min(xs), min(ys), max(rights) - min(xs), max(bottoms) - min(ys)]
            regions.append(RawQuestionRegion(
                question_index=qi + 1,
                question_bbox=bbox,
                ocr_text="",
                confidence=0.8,
                bbox_source="paper_structured",
            ))
        return regions

    def _question_ocr_cut(self, image_bytes: bytes):
        """QuestionOcr 回退。返回 (regions, latency_ms)。"""
        from alibabacloud_ocr_api20210707.models import RecognizeEduQuestionOcrRequest

        t0 = time.time()
        client = _get_edu_client()
        req = RecognizeEduQuestionOcrRequest(need_rotate=False)
        req.body = io.BytesIO(image_bytes)
        resp = client.recognize_edu_question_ocr(req)
        data = resp.body.data
        if isinstance(data, str):
            data = json.loads(data)

        words = data.get("prism_wordsInfo", [])
        content = data.get("content", "")
        width = data.get("width", 0) or data.get("orgWidth", 0)
        height = data.get("height", 0) or data.get("orgHeight", 0)

        if not words:
            if content:
                return [RawQuestionRegion(
                    question_index=1,
                    question_bbox=[0, 0, width, height],
                    ocr_text=content,
                    confidence=0.5,
                    bbox_source="question_ocr",
                )], int((time.time() - t0) * 1000)
            return [], int((time.time() - t0) * 1000)

        # 按 Y 聚合为行 → 按间隔切题
        sorted_words = sorted(words, key=lambda w: (w.get("y", 0), w.get("x", 0)))
        lines = []
        current_line = []
        current_y = None
        for w in sorted_words:
            wy = w.get("y", 0)
            if current_y is None or abs(wy - current_y) <= 15:
                current_line.append(w)
                current_y = wy if current_y is None else min(current_y, wy)
            else:
                if current_line:
                    lines.append((current_y, current_line))
                current_line = [w]
                current_y = wy
        if current_line:
            lines.append((current_y, current_line))

        # 按行间间隔切题
        question_groups = []
        current_group = []
        prev_bottom = None
        for line_y, lw in sorted(lines, key=lambda l: l[0]):
            line_bottom = line_y + max(w.get("y", 0) + w.get("height", 0) - line_y for w in lw)
            if prev_bottom is not None and (line_y - prev_bottom) > 25:
                if current_group:
                    question_groups.append(current_group)
                current_group = []
            current_group.append((line_y, lw))
            prev_bottom = max(prev_bottom or 0, line_bottom)
        if current_group:
            question_groups.append(current_group)

        regions = []
        for qi, group in enumerate(question_groups):
            all_words = [w for _, lw in group for w in lw]
            xs = [w.get("x", 0) for w in all_words]
            ys = [w.get("y", 0) for w in all_words]
            rights = [w.get("x", 0) + w.get("width", 0) for w in all_words]
            bottoms = [w.get("y", 0) + w.get("height", 0) for w in all_words]
            bbox = [min(xs), min(ys), max(rights) - min(xs), max(bottoms) - min(ys)]
            line_texts = ["".join(w.get("word", "") for w in lw) for _, lw in group]
            ocr_text = "\n".join(line_texts)
            probs = [w.get("prob", 0) for w in all_words if w.get("prob")]
            avg_conf = round(sum(probs) / len(probs) / 100.0, 3) if probs else 0.5
            regions.append(RawQuestionRegion(
                question_index=qi + 1,
                question_bbox=bbox,
                ocr_text=ocr_text,
                confidence=avg_conf,
                bbox_source="question_ocr",
            ))
        return regions, int((time.time() - t0) * 1000)

    # 保留单题 ROI 方法
    def ocr_region(self, image_bytes: bytes, bbox: List[float] = None) -> dict:
        """对指定区域做单题 OCR，返回 OCR 文字。"""
        from alibabacloud_ocr_api20210707.models import RecognizeEduQuestionOcrRequest

        t0 = time.time()
        try:
            client = _get_edu_client()
            req = RecognizeEduQuestionOcrRequest(need_rotate=False)
            req.body = io.BytesIO(image_bytes)
            resp = client.recognize_edu_question_ocr(req)
            data = resp.body.data
            if isinstance(data, str):
                data = json.loads(data)
            return {
                "text": data.get("content", ""),
                "words": data.get("prism_wordsInfo", []),
                "success": True,
                "latency_ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {
                "text": "",
                "words": [],
                "success": False,
                "error": str(e)[:200],
                "latency_ms": int((time.time() - t0) * 1000),
            }
