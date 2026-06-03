"""
grading_v2/marks_builder.py

从 pipeline 输出（questions + OCR blocks + groups）生成 GradingDocument marks。
不发起任何外部 API 调用。

质量门（按顺序，任一命中则不画红圈并标 needs_review）：
  (a) answer_bbox 缺失        → no red_circle，question.needs_review
  (b) answer_bbox 超大 / 跨多题 → no red_circle，question.needs_review
  (c) 错题：优先从 OCR blocks 精确匹配局部 bbox，再用 answer_bbox
  (d) 所有输出 bbox 已 clamp 到图片边界
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from schemas.grading_document import (
    GradingDocument,
    GradingGroup,
    GradingMark,
    GradingQuestion,
    PageMeta,
    QualityFlags,
)
from schemas.recognition import _get_question_field, normalize_bbox, normalize_confidence


# ── mark policy 分类器 ────────────────────────────────────────────────────────

# 题型分类（决定红圈策略）
# single_answer        : 简单填空/计算，只圈最终答案区
# choice               : 选择题，圈选中的选项
# vertical_calculation : 竖式计算，优先圈错误步骤或最终结果
# multi_step           : 多步骤/应用题，优先圈错误步骤或最终结果
# meta_or_ignored      : 说明/标题等非题目节点，跳过所有 mark 生成
MarkPolicy = Literal[
    "single_answer",
    "choice",
    "vertical_calculation",
    "multi_step",
    "meta_or_ignored",
]

_NON_QUESTION_KINDS: frozenset[str] = frozenset({
    "meta", "section", "ignored", "ignore", "header", "footer",
    "title", "summary", "instruction", "instructions", "note",
})


def _classify_mark_policy(q: Any) -> str:
    """
    轻量题型分类器 — 决定红圈绘制策略。
    仅使用 question dict/对象中已有字段，不发起任何外部调用。

    分类规则（按优先级）：
      1. kind 非 question → meta_or_ignored
      2. options 列表有 ≥2 项 → choice
      3. question_role 含 vertical / question_text 含竖式 → vertical_calculation
      4. question_role 含 multi_step / word_problem / subquestion → multi_step
      5. question_text 长度 > 80 → multi_step（应用题启发式）
      6. 其余 → single_answer
    """
    kind = str(_get_question_field(q, "kind", "question") or "question").strip().lower()
    if kind in _NON_QUESTION_KINDS:
        return "meta_or_ignored"

    options = _get_question_field(q, "options")
    if isinstance(options, list) and len(options) >= 2:
        return "choice"

    question_role = str(_get_question_field(q, "question_role") or "").strip().lower()
    question_text = str(_get_question_field(q, "question_text") or "").strip()

    if "vertical" in question_role or "竖式" in question_text or "竖算" in question_text:
        return "vertical_calculation"

    _MULTI_ROLE_KEYS = ("multi_step", "word_problem", "subquestion", "grouped_question")
    if any(k in question_role for k in _MULTI_ROLE_KEYS):
        return "multi_step"

    if len(question_text) > 80:
        return "multi_step"

    return "single_answer"


# ── 常量（不绑定具体像素坐标，只表达比例/倍数关系） ───────────────────────────

# answer_bbox 面积 / 图片面积 超过此比例 → 过大（防止跨多题、大片空白整框画圈）
_MAX_ANSWER_IMAGE_FRACTION: float = 0.60

# answer_bbox 单边尺寸上限（像素），对齐现有 build_overlay_mark 逻辑
_MAX_ANSWER_DIM_PX: int = 1024

# answer_bbox 面积 / question_bbox 面积 超过此倍数 → 过大
_MAX_ANSWER_Q_RATIO: float = 3.0

# answer_bbox 与其他题目 bbox 的交叉面积 / answer_bbox 面积 超过此阈值 → 跨多题
_MULTI_Q_OVERLAP_THRESHOLD: float = 0.20

# intent 中可包含错误文本的字段名（按优先级）
_TEXT_HINT_FIELDS: tuple[str, ...] = ("error_text", "wrong_answer", "target_text", "error_answer")


# ── 几何工具函数 ──────────────────────────────────────────────────────────────

def _clamp_bbox(
    bbox: list[float], image_width: int, image_height: int
) -> Optional[list[float]]:
    """
    将 [x, y, w, h] clamp 到图片边界，返回合法 bbox 或 None。
    x/y/w/h 均不超出图片范围，w/h 不为负。
    """
    x, y, w, h = bbox
    x = max(0.0, min(float(x), float(image_width)))
    y = max(0.0, min(float(y), float(image_height)))
    w = max(0.0, min(float(w), float(image_width) - x))
    h = max(0.0, min(float(h), float(image_height) - y))
    if w <= 0 or h <= 0:
        return None
    return [x, y, w, h]


def _bbox_area(bbox: list[float]) -> float:
    return float(bbox[2]) * float(bbox[3])


def _intersection_area(a: list[float], b: list[float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(ax, bx)
    iy = max(ay, by)
    ix2 = min(ax + aw, bx + bw)
    iy2 = min(ay + ah, by + bh)
    if ix2 <= ix or iy2 <= iy:
        return 0.0
    return (ix2 - ix) * (iy2 - iy)


def _spans_multiple_questions(
    answer_bbox: list[float],
    question_id: str,
    all_questions: list[Any],
) -> bool:
    """
    若 answer_bbox 与 2 个或以上其他题目的 bbox 显著重叠，返回 True。
    显著：交叉面积 / answer_bbox 面积 >= _MULTI_Q_OVERLAP_THRESHOLD。
    """
    ab_area = _bbox_area(answer_bbox)
    if ab_area <= 0:
        return False
    overlap_count = 0
    for q in all_questions:
        if _get_question_field(q, "question_id", "") == question_id:
            continue
        qbbox = normalize_bbox(_get_question_field(q, "bbox"))
        if qbbox is None:
            continue
        inter = _intersection_area(answer_bbox, qbbox)
        if inter / ab_area >= _MULTI_Q_OVERLAP_THRESHOLD:
            overlap_count += 1
            if overlap_count >= 2:
                return True
    return False


def _is_bbox_too_large(
    answer_bbox: list[float],
    question_bbox: Optional[list[float]],
    image_width: int,
    image_height: int,
    all_questions: list[Any],
    question_id: str,
) -> bool:
    """综合判断 answer_bbox 是否应被拒绝（对齐并扩展现有 build_overlay_mark 逻辑）。"""
    aw, ah = float(answer_bbox[2]), float(answer_bbox[3])

    # 1. 单边尺寸上限（对齐现有逻辑）
    if aw > _MAX_ANSWER_DIM_PX or ah > _MAX_ANSWER_DIM_PX:
        return True

    # 2. 占图片面积比例
    image_area = float(image_width) * float(image_height)
    if image_area > 0 and _bbox_area(answer_bbox) / image_area > _MAX_ANSWER_IMAGE_FRACTION:
        return True

    # 3. 相对题目面积倍数
    if question_bbox is not None:
        q_area = _bbox_area(question_bbox)
        if q_area > 0 and _bbox_area(answer_bbox) > q_area * _MAX_ANSWER_Q_RATIO:
            return True

    # 4. 跨多题
    if _spans_multiple_questions(answer_bbox, question_id, all_questions):
        return True

    return False


# ── 文本工具函数 ──────────────────────────────────────────────────────────────

def _normalize_text(s: str) -> str:
    """
    轻量文本规范化：trim + 去全角/特殊空格，保留中文/数字/英文原样。
    不做激进纠错，不引入复杂 NLP。
    """
    if not s:
        return ""
    s = s.strip()
    for ch in ("\u3000", "\u00a0", "\u200b", "\ufeff"):
        s = s.replace(ch, "")
    return s


def _extract_text_hint(intent: Optional[dict]) -> Optional[str]:
    """
    从 intent dict 中提取错误文本提示（error_text / wrong_answer / target_text 等）。
    返回 normalize 后的字符串，或 None（无可用提示）。
    """
    if not intent:
        return None
    for field in _TEXT_HINT_FIELDS:
        val = intent.get(field)
        if val and isinstance(val, str):
            hint = _normalize_text(val)
            if hint:
                return hint
    return None


def _center_distance(bbox_a: list[float], bbox_b: list[float]) -> float:
    """返回两个 [x, y, w, h] bbox 中心点的欧氏距离。"""
    cx_a = bbox_a[0] + bbox_a[2] / 2
    cy_a = bbox_a[1] + bbox_a[3] / 2
    cx_b = bbox_b[0] + bbox_b[2] / 2
    cy_b = bbox_b[1] + bbox_b[3] / 2
    return ((cx_a - cx_b) ** 2 + (cy_a - cy_b) ** 2) ** 0.5


def _is_center_within(block_bbox: list[float], container: list[float]) -> bool:
    """判断 block_bbox 的中心点是否在 container [x, y, w, h] 内（含边界）。"""
    if container[2] <= 0 or container[3] <= 0:
        return False
    bcx = block_bbox[0] + block_bbox[2] / 2
    bcy = block_bbox[1] + block_bbox[3] / 2
    cx, cy, cw, ch = container
    return cx <= bcx <= cx + cw and cy <= bcy <= cy + ch


# ── OCR block 匹配（精确文本 + 空间回退） ────────────────────────────────────

def _find_ocr_bbox_by_text(
    text_hint: str,
    answer_bbox: list[float],
    question_bbox: Optional[list[float]],
    ocr_blocks: list[dict],
) -> tuple[Optional[list[float]], bool]:
    """
    在 OCR blocks 中按文本匹配 text_hint，返回 (最佳 bbox, is_ambiguous)。

    匹配优先级：
      1. answer_bbox 范围内命中 → 取最近 answer_bbox 中心的
      2. question_bbox 范围内命中 → 取最近 answer_bbox 中心的
      3. 多命中但均不在任何答题区 → 歧义，返回 (None, True)
      4. 单一命中（无论位置）→ 直接返回

    返回 (None, False) 表示无命中；(None, True) 表示歧义；
    (bbox, False) 表示唯一可信命中。
    """
    norm_hint = _normalize_text(text_hint)
    if not norm_hint:
        return None, False

    matched: list[list[float]] = []
    for block in ocr_blocks:
        raw_text = block.get("text") or ""
        if norm_hint != _normalize_text(raw_text):
            continue
        raw = block.get("bbox") or [
            block.get("x", 0), block.get("y", 0),
            block.get("w", 0), block.get("h", 0),
        ]
        bbbox = normalize_bbox(raw)
        if bbbox is not None:
            matched.append(bbbox)

    if not matched:
        return None, False
    if len(matched) == 1:
        return matched[0], False

    # 多命中：按空间优先级消歧
    # Tier 1: answer_bbox 内
    in_answer = [b for b in matched if _is_center_within(b, answer_bbox)]
    if in_answer:
        in_answer.sort(key=lambda b: _center_distance(b, answer_bbox))
        return in_answer[0], False

    # Tier 2: question_bbox 内
    if question_bbox is not None:
        in_q = [b for b in matched if _is_center_within(b, question_bbox)]
        if in_q:
            in_q.sort(key=lambda b: _center_distance(b, answer_bbox))
            return in_q[0], False

    # 多命中但均不在答题区/题目区 → 歧义，不画圈
    return None, True


def _find_best_ocr_bbox(
    answer_bbox: list[float],
    ocr_blocks: list[dict],
    tolerance_px: float = 30.0,
) -> Optional[list[float]]:
    """
    在 OCR blocks 中找与 answer_bbox 空间最近的 block，返回其 bbox。
    优先选重叠最大的；无重叠时选邻近（< tolerance_px）的。
    找不到合适 block 时返回 None（调用方回退到 answer_bbox）。
    """
    best_score: float = 0.0
    best_bbox: Optional[list[float]] = None

    for block in ocr_blocks:
        raw = block.get("bbox") or [
            block.get("x", 0), block.get("y", 0),
            block.get("w", 0), block.get("h", 0),
        ]
        bbbox = normalize_bbox(raw)
        if bbbox is None:
            continue

        inter = _intersection_area(answer_bbox, bbbox)
        if inter > 0:
            if inter > best_score:
                best_score = inter
                best_bbox = bbbox
        else:
            # 无重叠时按邻近度给一个小权重
            bx, by, bw, bh = bbbox
            ax, ay, aw, ah = answer_bbox
            dx = max(0.0, max(ax, bx) - min(ax + aw, bx + bw))
            dy = max(0.0, max(ay, by) - min(ay + ah, by + bh))
            if dx <= tolerance_px and dy <= tolerance_px and best_bbox is None:
                # 仅在还没找到重叠 block 时才考虑邻近 block
                pseudo_score = 0.1 / (1.0 + dx + dy)
                if pseudo_score > best_score:
                    best_score = pseudo_score
                    best_bbox = bbbox

    return best_bbox


# ── 公共 API ──────────────────────────────────────────────────────────────────

def build_marks(
    *,
    image_width: int,
    image_height: int,
    questions: list[Any],
    ocr_blocks: list[dict],
    groups: list[dict],
    qwen_grading_intent: Optional[dict] = None,
    image_confidence: Optional[float] = None,
    source: str = "marks_builder_v2",
) -> GradingDocument:
    """
    从 pipeline 输出构建 GradingDocument。

    Parameters
    ----------
    image_width / image_height
        原图尺寸（像素）。
    questions
        pipeline 的 question list（RecognitionQuestionContract 或 dict）。
    ocr_blocks
        原始 OCR block dicts（{text, x, y, w, h} 或 {text, bbox}）。
    groups
        build_group_boxes() 返回的题组 dict list。
    qwen_grading_intent
        可选：Qwen 批改意图，格式：
          {"version": "v1", "questions": [{question_id, is_correct, confidence, ...}]}
        存在时补充 student_answer/standard_answer/error_text/confidence；
        不覆盖 pipeline 的 is_correct（is_correct 字段被忽略）。
    image_confidence
        图片整体识别置信度（0-1）。
    source
        marks 来源标签，写入 GradingMark.source。
    """
    # ── Intent 索引（question_id → intent dict）─────────────────────────────
    intent_by_id: dict[str, dict] = {}
    if isinstance(qwen_grading_intent, dict):
        for item in (qwen_grading_intent.get("questions") or []):
            qid = item.get("question_id")
            if qid:
                intent_by_id[qid] = item

    # ── 初始化 ────────────────────────────────────────────────────────────────
    page_meta = PageMeta(
        image_width=image_width,
        image_height=image_height,
        confidence=normalize_confidence(image_confidence),
    )
    doc_quality = QualityFlags()
    grading_groups: list[GradingGroup] = []
    grading_questions: list[GradingQuestion] = []
    marks: list[GradingMark] = []

    # ── 题组 marks ────────────────────────────────────────────────────────────
    q_to_group: dict[str, str] = {}
    for idx, grp in enumerate(groups, start=1):
        gid: str = grp.get("group_id") or f"group-{idx}"
        label_text: str = grp.get("label") or str(idx)
        title: Optional[str] = grp.get("title") or grp.get("label")

        raw_bbox = grp.get("bbox")
        gbbox = normalize_bbox(raw_bbox)
        if gbbox:
            gbbox = _clamp_bbox(gbbox, image_width, image_height)

        grading_groups.append(GradingGroup(
            group_id=gid,
            group_index=idx,
            bbox=gbbox,
            title=title,
            confidence=None,
        ))

        # 记录题目 → 题组映射
        for qid in grp.get("question_ids") or []:
            q_to_group[qid] = gid

        if gbbox:
            # group_box mark
            marks.append(GradingMark(
                mark_id=f"mark-gbox-{gid}",
                type="group_box",
                target_group_id=gid,
                bbox=gbbox,
                text=label_text,
                source=source,
            ))
            # group_label mark — 定位在题组 bbox 左上角上方 20px
            label_y = max(0.0, gbbox[1] - 20.0)
            label_w = min(60.0, gbbox[2])
            label_bbox = [gbbox[0], label_y, label_w, 20.0]
            marks.append(GradingMark(
                mark_id=f"mark-glabel-{gid}",
                type="group_label",
                target_group_id=gid,
                bbox=label_bbox,
                text=label_text,
                source=source,
            ))

    # ── 题目 marks ────────────────────────────────────────────────────────────
    for order_idx, q in enumerate(questions):
        # 只处理 kind=question 的节点（meta_or_ignored 直接跳过）
        kind = str(_get_question_field(q, "kind", "question") or "question").strip().lower()
        if kind != "question":
            continue

        # ── mark policy 分类 ──────────────────────────────────────────────
        mark_policy: str = _classify_mark_policy(q)

        qid: str = _get_question_field(q, "question_id", "") or ""

        # 读原始数据（先 normalize，质量门检查用原始值，clamp 留到渲染前）
        q_bbox_raw = normalize_bbox(_get_question_field(q, "bbox"))
        a_bbox_raw = normalize_bbox(_get_question_field(q, "answer_bbox"))
        student_answer = _get_question_field(q, "student_answer")
        grading_expl = _get_question_field(q, "grading_explanation")
        pipeline_confidence = normalize_confidence(_get_question_field(q, "confidence"))

        # pipeline is_correct 始终权威，不被 intent 覆盖
        is_correct = _get_question_field(q, "is_correct")

        # intent 仅提供补充信息（student_answer/standard_answer/error_text/confidence）
        intent = intent_by_id.get(qid)
        if intent is not None:
            std_answer = intent.get("standard_answer")
            intent_conf = normalize_confidence(intent.get("confidence"))
            confidence = intent_conf if intent_conf is not None else pipeline_confidence
            if student_answer is None:
                student_answer = intent.get("student_answer")
        else:
            std_answer = None
            confidence = pipeline_confidence

        # 批改结果
        grading_result = None
        if is_correct is True:
            grading_result = "correct"
        elif is_correct is False:
            grading_result = "incorrect"

        # ── 质量门：在 clamp 之前，对原始值检查尺寸 ──────────────────────────
        q_needs_review = False
        q_bbox_too_large = False
        q_answer_bbox_missing = False

        # 用于后续 clamp 的工作变量
        q_bbox: Optional[list[float]] = None
        a_bbox: Optional[list[float]] = None

        if a_bbox_raw is None:
            # 质量门 (a)：answer_bbox 缺失
            q_answer_bbox_missing = True
            if grading_result is not None:
                q_needs_review = True
        elif _is_bbox_too_large(a_bbox_raw, q_bbox_raw, image_width, image_height, questions, qid):
            # 质量门 (b)：answer_bbox 原始值过大 / 跨多题（用原始值检查保证 1024px 上限有效）
            q_bbox_too_large = True
            if grading_result == "incorrect":
                q_needs_review = True
            # a_bbox 留 None，不用于绘制
        else:
            # 通过质量门，clamp 到图片边界供绘制
            a_bbox = _clamp_bbox(a_bbox_raw, image_width, image_height)

        if q_bbox_raw:
            q_bbox = _clamp_bbox(q_bbox_raw, image_width, image_height)

        # ── 生成 marks ────────────────────────────────────────────────────────
        if grading_result == "correct" and a_bbox is not None and student_answer:
            # green_tick：落在 answer_bbox
            clamped = _clamp_bbox(a_bbox, image_width, image_height)
            if clamped:
                marks.append(GradingMark(
                    mark_id=f"mark-tick-{qid}",
                    type="green_tick",
                    target_question_id=qid,
                    target_group_id=q_to_group.get(qid),
                    bbox=clamped,
                    text=student_answer,
                    confidence=confidence,
                    source=source,
                ))

        elif grading_result == "incorrect" and not q_needs_review and a_bbox is not None:
            # 质量门 (c)：依 mark_policy 决定红圈定位策略
            text_hint = _extract_text_hint(intent)
            draw_bbox: Optional[list[float]] = None

            if text_hint:
                # 所有 policy 均支持文本精确匹配（最高优先级）
                refined_bbox, is_ambiguous = _find_ocr_bbox_by_text(
                    text_hint, a_bbox, q_bbox, ocr_blocks
                )
                if refined_bbox is not None and not is_ambiguous:
                    draw_bbox = refined_bbox
                else:
                    # 文本找不到或歧义 → needs_review，不画红圈
                    q_needs_review = True

            elif mark_policy in ("single_answer", "choice"):
                # 空间匹配：仅当 OCR block 面积 ≤ answer_bbox 面积时才使用
                # （防止大 OCR block 撑大红圈，反向不精确）
                refined = _find_best_ocr_bbox(a_bbox, ocr_blocks)
                if refined is not None and _bbox_area(refined) <= _bbox_area(a_bbox):
                    draw_bbox = refined
                else:
                    draw_bbox = a_bbox

            else:
                # multi_step / vertical_calculation：无文本提示时直接使用 answer_bbox
                # 空间匹配可能命中题组大框/跨行内容，禁止圈整页/整栏
                draw_bbox = a_bbox

            if draw_bbox is not None:
                # 质量门 (d)：clamp 到图片边界
                clamped = _clamp_bbox(draw_bbox, image_width, image_height)
                if clamped:
                    marks.append(GradingMark(
                        mark_id=f"mark-circle-{qid}",
                        type="red_circle",
                        target_question_id=qid,
                        target_group_id=q_to_group.get(qid),
                        bbox=clamped,
                        text=student_answer,
                        confidence=confidence,
                        source=source,
                    ))

        # 更新文档级质量标志
        if q_needs_review:
            doc_quality.needs_review = True
        if q_bbox_too_large:
            doc_quality.bbox_too_large = True
        if q_answer_bbox_missing:
            doc_quality.answer_bbox_missing = True

        grading_questions.append(GradingQuestion(
            question_id=qid,
            group_id=q_to_group.get(qid),
            order_index=order_idx,
            question_bbox=q_bbox,
            answer_bbox=a_bbox,
            stem_text=_get_question_field(q, "question_text"),
            student_answer=student_answer,
            standard_answer=std_answer,
            subject=None,
            question_type=_get_question_field(q, "question_type"),
            mark_policy=mark_policy,
            needs_review=q_needs_review,
            grading_result=grading_result,
            confidence=confidence,
            evidence=grading_expl,
        ))

    # 整体低置信度标志
    if image_confidence is not None and image_confidence < 0.6:
        doc_quality.low_confidence = True

    return GradingDocument(
        page_meta=page_meta,
        groups=grading_groups,
        questions=grading_questions,
        marks=marks,
        quality_flags=doc_quality,
    )
