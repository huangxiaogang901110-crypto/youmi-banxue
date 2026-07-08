"""
grading_v2/fusion.py

GradingDocument V2 融合原型入口。

将以下输入合并为 GradingDocument：
  - OCR blocks（带坐标的文字检测结果）
  - 现有 pipeline questions（已含 is_correct / answer_bbox / student_answer）
  - Qwen 批改意图（可选；mock / cached / 真实 Qwen-VL 输出）

Qwen intent 格式（可选）：
  {
    "version": "v1",
    "questions": [
      {"question_id": "q1", "is_correct": false, "confidence": 0.92,
       "student_answer": "5", "standard_answer": "6"}
    ]
  }

Feature flag: YOMI_GRADING_V2=1
  目前未接入主 pipeline，通过此函数独立调用或测试脚本激活。
  默认关闭，不替换现有主链路。

环境变量：
  YOMI_GRADING_V2=1          启用 V2（供未来 pipeline 集成时检查）
  YOMI_GRADING_V2_INTENT_PATH=/path/to/intent.json
                              加载自定义 intent JSON，覆盖 sample_intent.json
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from schemas.grading_document import GradingDocument
from grading_v2.marks_builder import build_marks


# sample_intent.json 与本文件同目录
_SAMPLE_INTENT_PATH = Path(__file__).parent / "sample_intent.json"


def _load_sample_intent() -> dict:
    """加载 sample_intent.json（仅用于开发 / 冒烟测试）。失败时静默返回空 dict。"""
    try:
        return json.loads(_SAMPLE_INTENT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def fuse_grading_document(
    *,
    image_width: int,
    image_height: int,
    questions: list[Any],
    ocr_blocks: list[dict],
    groups: list[dict],
    qwen_intent: Optional[dict] = None,
    use_sample_intent: bool = False,
    image_confidence: Optional[float] = None,
) -> GradingDocument:
    """
    融合 pipeline 输出为 GradingDocument。

    Intent 解析优先级（高 → 低）：
      1. qwen_intent 参数（显式传入）
      2. 环境变量 YOMI_GRADING_V2_INTENT_PATH 指向的文件
      3. use_sample_intent=True 时加载 sample_intent.json
      4. None → 仅用 pipeline 已有的 is_correct

    Parameters
    ----------
    image_width / image_height
        原图尺寸（像素）。
    questions
        pipeline question list（RecognitionQuestionContract 或 dict）。
    ocr_blocks
        原始 OCR block dicts（{text, x, y, w, h} 或 {text, bbox}）。
    groups
        build_group_boxes() 返回的题组 dict list。
    qwen_intent
        显式传入的 Qwen 批改意图 dict。
    use_sample_intent
        True 时在 qwen_intent=None 且无环境变量时加载 sample_intent.json。
        仅供冒烟测试 / 开发，生产不应置为 True。
    image_confidence
        图片整体 OCR 置信度（0-1）。

    Returns
    -------
    GradingDocument
    """
    # ── Intent 解析 ───────────────────────────────────────────────────────────
    resolved_intent: Optional[dict] = qwen_intent

    if resolved_intent is None:
        env_path = os.getenv("YOMI_GRADING_V2_INTENT_PATH", "").strip()
        if env_path:
            try:
                resolved_intent = json.loads(Path(env_path).read_text(encoding="utf-8"))
            except Exception:
                pass  # 非阻塞：文件读取失败时继续

    if resolved_intent is None and use_sample_intent:
        resolved_intent = _load_sample_intent()

    return build_marks(
        image_width=image_width,
        image_height=image_height,
        questions=questions,
        ocr_blocks=ocr_blocks,
        groups=groups,
        qwen_grading_intent=resolved_intent,
        image_confidence=image_confidence,
        source="fusion_v2",
    )


def is_v2_enabled() -> bool:
    """检查 YOMI_GRADING_V2 环境变量是否开启（供未来 pipeline 集成时使用）。"""
    return os.getenv("YOMI_GRADING_V2", "").strip() == "1"
