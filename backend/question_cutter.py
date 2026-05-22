"""
智能切题模块 v2 — 大块内切小题
输入: OCR blocks + Block info → 输出: Question dict 列表
"""
import re
from typing import List, Dict


# 题号正则：匹配 "1." "2、" "3．" "4)" 等
_QNO_PAT = re.compile(r'^\s*(\d{1,3})\s*[.、．)）]\s*')


def cut_block_to_questions(
    ocr_blocks: List[dict],
    block_bbox: List[float],
    block_id: str,
    block_title: str = "",
    subject: str = "",
) -> List[dict]:
    """
    在一个大块内切分为小题。
    返回: [{'question_text':..., 'bbox':..., 'question_number':..., 'block_id':...}, ...]
    """
    if not ocr_blocks:
        return []

    # 筛选 block 范围内的 blocks
    bx, by, bw, bh = block_bbox[:4] if len(block_bbox) >= 4 else (0, 0, 9999, 9999)
    candidates = []
    for b in ocr_blocks:
        cx = b.get("x", 0)
        cy = b.get("y", 0)
        cw = b.get("w", 50)
        ch = b.get("h", 20)
        # 判断是否在 block 内（宽松边界）
        center_x = cx + cw / 2
        center_y = cy + ch / 2
        if (bx - 30 <= center_x <= bx + bw + 30 and
            by - 20 <= center_y <= by + bh + 20):
            candidates.append(b)

    if not candidates:
        candidates = ocr_blocks  # fallback: use all

    # 按 Y → X 排序
    candidates.sort(key=lambda b: (b.get("y", 0), b.get("x", 0)))

    # 寻找题号行作为切分点
    q_starts = []
    for i, b in enumerate(candidates):
        m = _QNO_PAT.match(b.get("text", "").strip())
        if m:
            num = int(m.group(1))
            # 过滤假题号（年份、日期等）
            if 1 <= num <= 200:
                q_starts.append((i, num))

    if not q_starts:
        # 无题号：整块作为一题
        if candidates:
            return [_make_question(candidates, 1, block_id, block_title, subject)]
        return []

    # 合并同一题号的连续行（同一题多行OCR误拆）
    merged_starts = []
    for i, (idx, num) in enumerate(q_starts):
        if i > 0:
            prev_idx, prev_num = q_starts[i - 1]
            # 仅当题号相同时合并（同一题多行）
            if num == prev_num:
                continue
        merged_starts.append((idx, num))

    # 按题号分块
    questions = []
    for si, (start_idx, qnum) in enumerate(merged_starts):
        # 结束位置：下一题开始或末尾
        if si + 1 < len(merged_starts):
            end_idx = merged_starts[si + 1][0]
        else:
            end_idx = len(candidates)

        q_blocks = candidates[start_idx:end_idx]
        questions.append(_make_question(q_blocks, qnum, block_id, block_title, subject))

    return questions


def _make_question(blocks: List[dict], qnum: int, block_id: str,
                   block_title: str = "", subject: str = "") -> dict:
    """从blocks列表构建一个Question dict"""
    if not blocks:
        return {"question_text": "", "question_number": qnum, "bbox": [0, 0, 0, 0],
                "block_id": block_id}

    # 拼接文本：题号行+后续内容
    texts = []
    for b in blocks:
        t = b.get("text", "").strip()
        if t:
            texts.append(t)
    full_text = " ".join(texts)

    # union bbox
    vals = [(b.get("x", 0), b.get("y", 0),
             b.get("x", 0) + b.get("w", 50),
             b.get("y", 0) + b.get("h", 20)) for b in blocks]
    min_x = min(v[0] for v in vals)
    min_y = min(v[1] for v in vals)
    max_x = max(v[2] for v in vals)
    max_y = max(v[3] for v in vals)

    return {
        "question_text": full_text,
        "question_number": qnum,
        "bbox": [float(min_x), float(min_y), float(max_x - min_x), float(max_y - min_y)],
        "block_id": block_id,
        "block_title": block_title,
        "subject": subject,
    }


# 保持向后兼容：旧接口
def cut_to_questions(ocr_blocks: List[dict]) -> List[dict]:
    """旧接口：整图切题（向后兼容）"""
    return cut_block_to_questions(ocr_blocks, [0, 0, 9999, 9999], "", "", "")
