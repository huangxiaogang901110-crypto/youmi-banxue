"""
答案区定位模块 — 在 OCR blocks 中定位孩子答案区域
输出每个题目的 answer_bbox
"""
from typing import List, Optional


def detect_answer_bbox(question_bbox: List[float], ocr_blocks: List[dict],
                        question_text: str = "", img_h: int = 1200) -> Optional[List[float]]:
    """
    在 question_bbox 下方查找答案区。
    返回 [x, y, w, h] 或 None。
    """
    if not ocr_blocks or not question_bbox or len(question_bbox) < 4:
        return None

    qx, qy, qw, qh = question_bbox[:4]
    q_bottom = qy + qh
    q_right = qx + qw

    # 在题目区域下方找手写内容 blocks
    candidates = []
    for b in ocr_blocks:
        by = b.get("y", 0)
        bx = b.get("x", 0)
        bw = b.get("w", 50)
        # 必须在题目区域下方且水平重叠
        if by >= q_bottom - 5 and by < q_bottom + qh * 3:
            # 水平方向：允许一定偏移
            if bx < q_right + 50 and bx + bw > qx - 50:
                candidates.append(b)

    if not candidates:
        # 简单回退：题目下方 2 倍行高
        return [qx, q_bottom + 5, qw, min(qh, 60)]

    # 取最靠近题目底部的几个blocks的合并bbox
    candidates.sort(key=lambda b: b["y"])
    # 只取前5个或全部
    top = candidates[:5]

    min_x = min(b["x"] for b in top)
    min_y = min(b["y"] for b in top)
    max_x = max(b["x"] + b.get("w", 40) for b in top)
    max_y = max(b["y"] + b.get("h", 20) for b in top)

    ans_w = max_x - min_x
    ans_h = max_y - min_y

    # 合理性检查
    if ans_w < 10 or ans_h < 8:
        return None
    if ans_h > qh * 5:
        return None

    return [float(min_x), float(min_y), float(ans_w), float(ans_h)]


def detect_all_answer_bboxes(questions: list, ocr_blocks: List[dict],
                               img_h: int = 1200) -> list:
    """
    批量为 questions 列表补全 answer_bbox。
    questions: [{'question_text':..., 'bbox':[...]}, ...]
    返回: 同样的列表，每个元素加上 'answer_bbox'
    """
    for q in questions:
        bbox = q.get("bbox", [0, 0, 0, 0])
        if bbox and len(bbox) >= 4 and not all(v == 0 for v in bbox):
            ans = detect_answer_bbox(bbox, ocr_blocks, q.get("question_text", ""), img_h)
        else:
            ans = None
        q["answer_bbox"] = ans
    return questions
