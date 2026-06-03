"""
GradingDocument V2 数据契约。

设计原则：
- 与现有 RecognitionDocument / RecognitionOverlayMark 并列，不替换旧链路
- GradingMark 把 group_box / group_label / green_tick / red_circle / click_target 统一到一个列表
- QualityFlags 记录文档级质量问题，前端据此决定是否展示复查提示
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# ── Enhance decision status ───────────────────────────────────────────────────

EnhanceDecisionStatus = Literal["pending", "complete", "skipped", "conflict"]


# ── Literal types ─────────────────────────────────────────────────────────────

MarkType = Literal[
    "group_box",     # 题组灰框
    "group_label",   # 题组标签（文字）
    "green_tick",    # 正确对勾
    "red_circle",    # 错误红圈
    "click_target",  # 可点击锚点（预留）
]

GradingResult = Literal["correct", "incorrect", "unknown"]


# ── Sub-models ────────────────────────────────────────────────────────────────

class PageMeta(BaseModel):
    """图片级元数据。"""
    image_width: int
    image_height: int
    confidence: Optional[float] = None


class GradingGroup(BaseModel):
    """题组（来自批改单元聚合）。"""
    group_id: str
    group_index: int
    bbox: Optional[list[float]] = None   # [x, y, w, h] 原图坐标
    title: Optional[str] = None
    confidence: Optional[float] = None


class GradingQuestion(BaseModel):
    """单题批改数据。"""
    question_id: str
    group_id: Optional[str] = None
    order_index: int
    question_bbox: Optional[list[float]] = None   # 题目框 [x, y, w, h]
    answer_bbox: Optional[list[float]] = None      # 答案框 [x, y, w, h]（已 clamp）
    stem_text: Optional[str] = None
    student_answer: Optional[str] = None
    standard_answer: Optional[str] = None
    subject: Optional[str] = None
    question_type: Optional[str] = None
    grading_result: Optional[GradingResult] = None
    confidence: Optional[float] = None
    evidence: Optional[str] = None   # 批改依据（解释文本）


class GradingMark(BaseModel):
    """
    统一批改标注。

    bbox 使用原图坐标 [x, y, w, h]，前端负责缩放到显示尺寸。
    style 预留给前端自定义样式覆盖（颜色、粗细等）。
    """
    mark_id: str
    type: MarkType
    target_question_id: Optional[str] = None
    target_group_id: Optional[str] = None
    bbox: list[float]                              # [x, y, w, h]，已 clamp 到图片边界
    text: Optional[str] = None
    style: Optional[dict[str, Any]] = None        # 预留，前端可按需覆盖
    confidence: Optional[float] = None
    source: Optional[str] = None


class QualityFlags(BaseModel):
    """文档级质量标志。任意一项为 True 时前端可展示复查提示。"""
    low_confidence: bool = False         # 整体置信度偏低
    bbox_too_large: bool = False         # 存在超大 bbox
    answer_bbox_missing: bool = False    # 存在已批改但 answer_bbox 缺失的题目
    qwen_ocr_mismatch: bool = False      # Qwen 意图与 OCR 文本不一致（预留）
    needs_review: bool = False           # 综合判断需人工复查
    non_homework: bool = False           # 页面可能不是作业（预留）


# ── Root document ─────────────────────────────────────────────────────────────

class GradingDocument(BaseModel):
    """
    V2 批改文档。

    marks 是前端渲染的唯一数据来源：
      - group_box / group_label → 题组可视化
      - green_tick / red_circle → 批改结果
      - click_target → 预留交互锚点
    """
    page_meta: PageMeta
    groups: list[GradingGroup] = Field(default_factory=list)
    questions: list[GradingQuestion] = Field(default_factory=list)
    marks: list[GradingMark] = Field(default_factory=list)
    quality_flags: QualityFlags = Field(default_factory=QualityFlags)


# ── Qwen 局部增强模型 ─────────────────────────────────────────────────────────

class EnhanceCandidateInput(BaseModel):
    """
    Qwen 局部增强候选输入。

    描述一道需要异步 Qwen-VL crop 增强的题目及其上下文。
    由 select_enhance_candidates() 生成；不含图片二进制，只含元数据。
    """
    question_id: str
    crop_bbox: Optional[list[float]] = None        # 传给 Qwen 的局部 crop 区域 [x,y,w,h]
    answer_bbox: Optional[list[float]] = None      # 答案框（answer_bbox 安全门用）
    ocr_text: Optional[str] = None                 # 该区域的 OCR 识别文本
    student_answer: Optional[str] = None
    standard_answer: Optional[str] = None
    current_grading_result: Optional[GradingResult] = None
    current_confidence: Optional[float] = None
    quality_flags: list[str] = Field(default_factory=list)  # 入选理由：low_confidence/needs_review/suspected_incorrect
    reason: Optional[str] = None                   # 人可读的入选理由描述


class EnhanceResult(BaseModel):
    """
    Qwen 局部增强输出——单题批改增强结果。

    由 Qwen-VL crop 调用（真实或 mock）生成，
    由 apply_enhance_result() 消费后更新 GradingDocument。
    """
    question_id: str
    enhanced_result: Optional[GradingResult] = None    # Qwen 判断的批改结果
    enhanced_error_text: Optional[str] = None          # Qwen 识别的错误答案文本
    confidence: Optional[float] = None                 # Qwen 对此次增强结果的置信度
    reason: Optional[str] = None                       # Qwen 给出的增强理由
    suggested_marks: Optional[list[dict[str, Any]]] = None  # 预留：Qwen 建议的 mark 信息
    qwen_ocr_mismatch: bool = False                    # Qwen 意图与 OCR 结果不一致
    needs_review: bool = False                         # 综合判断需人工复查


class EnhanceDecision(BaseModel):
    """
    单题增强决策——记录选题原因、输入输出和最终处置状态。

    status 语义：
      pending   尚未处理（初始状态）
      complete  Qwen 高置信 + 不冲突，marks 已更新
      skipped   Qwen 低置信或无增强必要，保留原 marks
      conflict  Qwen 与 OCR 结果冲突，已置 qwen_ocr_mismatch + needs_review
    """
    question_id: str
    status: EnhanceDecisionStatus
    input: EnhanceCandidateInput
    output: Optional[EnhanceResult] = None
