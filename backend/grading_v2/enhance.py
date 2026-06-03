"""
grading_v2/enhance.py

Qwen 局部增强模块——异步 crop 增强，不改 OCR 主链路。

设计原则
--------
  - OCR 骨架为主链路，Qwen-VL 仅异步增强局部 crop，两者独立。
  - select_enhance_candidates：只选 low_confidence / needs_review /
    suspected_incorrect 的题；清晰高置信正确题不进。
  - apply_enhance_result 决策树：
      1. Qwen 低置信（< QWEN_HIGH_CONF）→ skipped，保留原 marks
      2. Qwen 与 OCR 结果冲突 → conflict，
         qwen_ocr_mismatch + needs_review 写入 doc.quality_flags
      3. Qwen 高置信 + 不冲突 → complete，更新 marks
         - red_circle 仍须通过 answer_bbox 安全门

环境约束
--------
  - 不调用任何真实模型 API（测试全量 mock）
  - 不修改 DB schema
  - 不影响现有 54 个测试
"""
from __future__ import annotations

from typing import Optional

from schemas.grading_document import (
    EnhanceCandidateInput,
    EnhanceDecision,
    EnhanceResult,
    GradingDocument,
    GradingMark,
)

# ── 置信度阈值 ────────────────────────────────────────────────────────────────

# 题目置信度低于此值 → 标记为 low_confidence 候选
_LOW_CONF_THRESHOLD: float = 0.70

# 题目置信度高于此值 + 结果为 correct + answer_bbox 存在 → 排除（清晰高置信正确）
_HIGH_CONF_CORRECT_THRESHOLD: float = 0.85

# Qwen 增强结果置信度须 ≥ 此值才被采纳
_QWEN_HIGH_CONF: float = 0.75


# ── 候选选取 ──────────────────────────────────────────────────────────────────

def select_enhance_candidates(
    doc: GradingDocument,
) -> list[EnhanceCandidateInput]:
    """
    从 GradingDocument 中选出需要 Qwen 异步增强的候选题目。

    入选条件（满足其一即可）
    -----------------------
    low_confidence
        题目 confidence < 0.70，或 confidence 为 None。
    needs_review
        grading_result 为 None（未知），或 answer_bbox 缺失。
    suspected_incorrect
        grading_result = "incorrect"；
        或 grading_result = "correct" 但置信度未达 HIGH_CONF 阈值
        （无法确信判断为正确）。

    排除条件
    --------
    grading_result = "correct"  AND  confidence ≥ 0.85
    AND  answer_bbox 存在 → 清晰高置信正确，无需增强。
    """
    candidates: list[EnhanceCandidateInput] = []

    for q in doc.questions:
        conf = q.confidence
        result = q.grading_result

        # ── 排除：清晰高置信正确 ──────────────────────────────────────────
        if (
            result == "correct"
            and conf is not None
            and conf >= _HIGH_CONF_CORRECT_THRESHOLD
            and q.answer_bbox is not None
        ):
            continue

        # ── 收集入选理由 ──────────────────────────────────────────────────
        flags: list[str] = []

        if conf is None or conf < _LOW_CONF_THRESHOLD:
            flags.append("low_confidence")

        if result is None or q.answer_bbox is None:
            flags.append("needs_review")

        if result == "incorrect":
            flags.append("suspected_incorrect")
        elif result == "correct" and "low_confidence" not in flags:
            # correct 但置信度不够高 → 不确定是否真的正确
            flags.append("suspected_incorrect")

        if not flags:
            # 理论上不应出现（未被排除则至少命中一个 flag），保留安全门
            continue

        # ── 构建候选描述 ──────────────────────────────────────────────────
        reason_parts: list[str] = []
        if "low_confidence" in flags:
            reason_parts.append(
                f"confidence={conf:.2f}" if conf is not None else "confidence=None"
            )
        if "needs_review" in flags:
            reason_parts.append(
                "answer_bbox missing" if q.answer_bbox is None else "result unknown"
            )
        if "suspected_incorrect" in flags:
            reason_parts.append(
                "grading_result=incorrect"
                if result == "incorrect"
                else "correct result uncertain"
            )

        candidates.append(EnhanceCandidateInput(
            question_id=q.question_id,
            crop_bbox=q.question_bbox,       # 整题框作为 crop 区域传给 Qwen
            answer_bbox=q.answer_bbox,
            ocr_text=q.stem_text,
            student_answer=q.student_answer,
            standard_answer=q.standard_answer,
            current_grading_result=result,
            current_confidence=conf,
            quality_flags=flags,
            reason="; ".join(reason_parts) if reason_parts else None,
        ))

    return candidates


# ── 增强结果应用 ──────────────────────────────────────────────────────────────

def apply_enhance_result(
    doc: GradingDocument,
    result: EnhanceResult,
) -> tuple[GradingDocument, EnhanceDecision]:
    """
    将 Qwen 增强结果应用到 GradingDocument，返回 (更新后的文档, 决策记录)。

    决策树
    ------
    1. Qwen 置信度 < QWEN_HIGH_CONF (0.75) → **skipped**，原 marks 不变。
    2. Qwen enhanced_result 与当前 OCR grading_result 相反 → **conflict**：
       - doc.quality_flags.qwen_ocr_mismatch = True
       - doc.quality_flags.needs_review = True
       - marks 不变。
    3. Qwen 高置信 + 不冲突 → **complete**，更新 marks：
       - red_circle：仍须通过 answer_bbox 安全门；
         若 answer_bbox 缺失则降级为 skipped + needs_review。
       - green_tick：需 answer_bbox 存在且有 student_answer。

    doc 参数视为不可变——函数内通过 model_copy(deep=True) 创建副本修改后返回。
    root quality_flags 是唯一权威：所有质量判断写入 updated_doc.quality_flags。
    """
    qid = result.question_id

    # 找到对应的 GradingQuestion（可能不存在）
    q_obj = next((q for q in doc.questions if q.question_id == qid), None)

    candidate_input = EnhanceCandidateInput(
        question_id=qid,
        crop_bbox=q_obj.question_bbox if q_obj else None,
        answer_bbox=q_obj.answer_bbox if q_obj else None,
        current_grading_result=q_obj.grading_result if q_obj else None,
        current_confidence=q_obj.confidence if q_obj else None,
        student_answer=q_obj.student_answer if q_obj else None,
        standard_answer=q_obj.standard_answer if q_obj else None,
        quality_flags=[],
        reason=None,
    )

    # 深拷贝：不修改调用方传入的 doc
    updated_doc = doc.model_copy(deep=True)

    qwen_conf = result.confidence

    # ── 决策 1：Qwen 低置信 → skipped ────────────────────────────────────────
    if qwen_conf is None or qwen_conf < _QWEN_HIGH_CONF:
        return updated_doc, EnhanceDecision(
            question_id=qid,
            status="skipped",
            input=candidate_input,
            output=result,
        )

    # ── 决策 2：冲突检测 ──────────────────────────────────────────────────────
    current_result = q_obj.grading_result if q_obj else None
    enhanced_result = result.enhanced_result

    is_conflict = (
        current_result is not None
        and current_result != "unknown"
        and enhanced_result is not None
        and enhanced_result != "unknown"
        and current_result != enhanced_result
    )

    if is_conflict:
        updated_doc.quality_flags.qwen_ocr_mismatch = True
        updated_doc.quality_flags.needs_review = True

        return updated_doc, EnhanceDecision(
            question_id=qid,
            status="conflict",
            input=candidate_input,
            output=result.model_copy(update={
                "qwen_ocr_mismatch": True,
                "needs_review": True,
            }),
        )

    # ── 决策 3：高置信 + 不冲突 → complete ───────────────────────────────────
    answer_bbox: Optional[list[float]] = q_obj.answer_bbox if q_obj else None

    # 安全门先行：验证能否构造合法 replacement marks，通过后再替换旧 marks
    if enhanced_result == "incorrect":
        # red_circle 安全门：必须有 answer_bbox；缺失属于安全门失败而非 Qwen/OCR 冲突
        if answer_bbox is None:
            updated_doc.quality_flags.needs_review = True
            updated_doc.quality_flags.answer_bbox_missing = True
            return updated_doc, EnhanceDecision(
                question_id=qid,
                status="skipped",
                input=candidate_input,
                output=result.model_copy(update={"needs_review": True}),
            )

    elif enhanced_result == "correct":
        # green_tick 安全门：必须有 answer_bbox
        if answer_bbox is None:
            updated_doc.quality_flags.needs_review = True
            return updated_doc, EnhanceDecision(
                question_id=qid,
                status="skipped",
                input=candidate_input,
                output=result.model_copy(update={"needs_review": True}),
            )
        # student_answer 安全门：无学生答案则无法构造 green_tick，不能删旧 marks
        if not (q_obj and q_obj.student_answer):
            updated_doc.quality_flags.needs_review = True
            return updated_doc, EnhanceDecision(
                question_id=qid,
                status="skipped",
                input=candidate_input,
                output=result.model_copy(update={"needs_review": True}),
            )

    # 安全门通过：先构造 replacement marks
    replacement_marks: list[GradingMark] = []

    if enhanced_result == "incorrect":
        replacement_marks.append(GradingMark(
            mark_id=f"mark-circle-{qid}-enhanced",
            type="red_circle",
            target_question_id=qid,
            bbox=answer_bbox,
            confidence=qwen_conf,
            source="qwen_enhance",
        ))

    elif enhanced_result == "correct":
        if q_obj is not None and q_obj.student_answer:
            replacement_marks.append(GradingMark(
                mark_id=f"mark-tick-{qid}-enhanced",
                type="green_tick",
                target_question_id=qid,
                bbox=answer_bbox,
                text=q_obj.student_answer,
                confidence=qwen_conf,
                source="qwen_enhance",
            ))

    # replacement 构造成功 → 替换旧 marks
    updated_doc.marks = [
        m for m in updated_doc.marks
        if m.target_question_id != qid
    ] + replacement_marks

    # 更新 GradingQuestion 中的批改结果和置信度
    for i, q in enumerate(updated_doc.questions):
        if q.question_id == qid:
            updated_doc.questions[i] = q.model_copy(update={
                "grading_result": enhanced_result,
                "confidence": qwen_conf,
            })
            break

    return updated_doc, EnhanceDecision(
        question_id=qid,
        status="complete",
        input=candidate_input,
        output=result,
    )
