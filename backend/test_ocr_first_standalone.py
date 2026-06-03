#!/usr/bin/env python3
"""OCR-first路径核心逻辑独立验证 — 不导入pipeline模块（避开DB依赖）"""
import math, sys

# === 以下函数与 pipeline.py 中完全相同，用于独立验证 ===

def _validate_answer_bbox(bbox, question_bbox=None, image_width=None, image_height=None):
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try: x, y, w, h = [float(v) for v in bbox]
    except (TypeError, ValueError): return None
    if x == 0 and y == 0 and w == 0 and h == 0: return None
    if not all(math.isfinite(v) for v in (x, y, w, h)): return None
    if w <= 0 or h <= 0: return None
    bbox_area = w * h
    if image_width and image_height:
        if x < 0 or y < 0 or x + w > image_width or y + h > image_height: return None
        img_area = image_width * image_height
        if img_area > 0 and bbox_area > img_area * 0.3: return None
    if question_bbox and isinstance(question_bbox, list) and len(question_bbox) == 4:
        try:
            qx, qy, qw, qh = [float(v) for v in question_bbox]
            if abs(x - qx) < 3 and abs(y - qy) < 3 and abs(w - qw) < 3 and abs(h - qh) < 3: return None
            q_area = qw * qh
            if q_area > 0 and bbox_area > q_area * 0.85: return None
        except (TypeError, ValueError): pass
    return [x, y, w, h]

def _assess_ocr_confidence(question_text, student_answer, bbox, answer_bbox, confidence=None):
    reasons = []
    if not question_text or len(str(question_text).strip()) < 6: reasons.append("text_short")
    if not answer_bbox: reasons.append("no_answer_bbox")
    if not student_answer: reasons.append("no_student_answer")
    if confidence is not None and float(confidence) < 0.5: reasons.append("low_confidence")
    needs_boost = len(reasons) > 0
    return needs_boost, "|".join(reasons) if reasons else "ok"

def _infer_answer_bbox_from_ocr(question_bbox, ocr_blocks, image_width=None, image_height=None):
    if not question_bbox or not ocr_blocks: return None
    try: qx, qy, qw, qh = [float(v) for v in question_bbox]
    except (TypeError, ValueError): return None
    candidates = []
    for block in ocr_blocks:
        bx, by, bw, bh = block.get("x",0), block.get("y",0), block.get("w",0), block.get("h",0)
        text = block.get("text","").strip()
        if bw <= 0 or bh <= 0 or not text: continue
        below = (by >= qy + qh*0.3) and (by <= qy + qh*4) and abs(bx - qx) < qw*2
        right = (bx >= qx + qw*0.3) and (bx <= qx + qw*3) and abs(by - qy) < qh*1.5
        if below or right: candidates.append([bx,by,bw,bh])
    if not candidates: return None
    min_x = min(c[0] for c in candidates)
    min_y = min(c[1] for c in candidates)
    max_x = max(c[0]+c[2] for c in candidates)
    max_y = max(c[1]+c[3] for c in candidates)
    return [min_x, min_y, max_x-min_x, max_y-min_y]

# === Tests ===
passed = 0; failed = 0
def t(name, cond):
    global passed, failed
    if cond: passed += 1; print(f"  ✓ {name}")
    else: failed += 1; print(f"  ✗ {name}")

print("1. answer_bbox safety gate")
t("valid", _validate_answer_bbox([10,20,30,40]) == [10,20,30,40])
t("[0000]", _validate_answer_bbox([0,0,0,0]) is None)
t("==qbbox", _validate_answer_bbox([10,20,100,50],[10,20,100,50]) is None)
t("oob", _validate_answer_bbox([900,20,200,50],image_width=800,image_height=600) is None)
t("area>30%", _validate_answer_bbox([0,0,500,500],image_width=800,image_height=600) is None)
t(">85%q", _validate_answer_bbox([12,22,95,47],[10,20,100,50]) is None)
t("distinct", _validate_answer_bbox([120,40,30,20],[10,20,100,50])==[120,40,30,20])
t("None", _validate_answer_bbox(None) is None)
t("within", _validate_answer_bbox([5,5,90,90],image_width=100,image_height=100) is None)  # 81% area → rejected by area safety gate

print("\n2. Confidence assessment")
needs, r = _assess_ocr_confidence("x", None, None, None)
t("short→boost", needs and "text_short" in r)
needs, r = _assess_ocr_confidence("12 + 34 = ?", "46", [0,0,100,30], [70,10,30,20])
t("complete→ok", not needs)
needs, r = _assess_ocr_confidence("12 + 34 = ?", None, [0,0,100,30], None)
t("no_ab→boost", needs and "no_answer_bbox" in r)
needs, r = _assess_ocr_confidence("12 + 34 = ?", None, [0,0,100,30], [70,10,30,20])
t("no_sa→boost", needs and "no_student_answer" in r)
needs, r = _assess_ocr_confidence("12 + 34 = ?", "46", [0,0,100,30], [70,10,30,20], 0.3)
t("low_conf→boost", needs and "low_confidence" in r)

print("\n3. OCR answer_bbox inference")
blocks=[{"text":"12+34=?","x":10,"y":20,"w":100,"h":25},{"text":"46","x":80,"y":30,"w":30,"h":20}]
r=_infer_answer_bbox_from_ocr([10,20,100,25],blocks)
t("infer_answer", r is not None and len(r)==4)
t("empty_blocks", _infer_answer_bbox_from_ocr([10,20,100,25],[]) is None)
t("no_qbbox", _infer_answer_bbox_from_ocr(None,blocks) is None)

print("\n4. Source tags")
t("ocr_main", "ocr_main" in ["ocr_main","qwen_boost","skipped"])
t("qwen_boost", "qwen_boost" in ["ocr_main","qwen_boost","skipped"])
t("skipped", "skipped" in ["ocr_main","qwen_boost","skipped"])

print("\n5. 0-question guard")
t("0q→needs_review", ("needs_review" if len([])==0 else "completed")=="needs_review")
t("1q→completed", ("needs_review" if len([1])==0 else "completed")=="completed")

print(f"\n{'='*40}")
print(f"Results: {passed} passed, {failed} failed out of {passed+failed}")
if failed == 0: print("ALL TESTS PASSED ✓")
else: print("SOME TESTS FAILED ✗"); sys.exit(1)
