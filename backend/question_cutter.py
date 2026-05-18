"""
智能切题模块 — Phase 0 任务 6
基准 §14: OCR block + 题号规则 + 版面规则生成候选切块
禁止大模型直接生成最终精准 bbox
"""

import re
from typing import List, Tuple, Optional


class OCRBlock:
    """OCR 识别块"""
    __slots__ = ("text", "x", "y", "w", "h")

    def __init__(self, text: str, pos: List[float]):
        self.text = text.strip()
        self.x = int(pos[0]) if len(pos) > 0 else 0
        self.y = int(pos[1]) if len(pos) > 1 else 0
        self.w = int(pos[2]) if len(pos) > 2 else 0
        self.h = int(pos[3]) if len(pos) > 3 else 0

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def right(self) -> int:
        return self.x + self.w


class QuestionGroup:
    """切题结果：一组属于同一题的 OCR 块"""
    __slots__ = ("blocks", "question_number", "number_text")

    def __init__(self, blocks: List[OCRBlock], question_number: int, number_text: str = ""):
        self.blocks = blocks
        self.question_number = question_number
        self.number_text = number_text

    @property
    def full_text(self) -> str:
        return " ".join(b.text for b in self.blocks)

    @property
    def bbox(self) -> List[float]:
        """计算合并后的 union bbox"""
        if not self.blocks:
            return [0, 0, 0, 0]
        min_x = min(b.x for b in self.blocks)
        min_y = min(b.y for b in self.blocks)
        max_r = max(b.right for b in self.blocks)
        max_b = max(b.bottom for b in self.blocks)
        return [min_x, min_y, max_r - min_x, max_b - min_y]


# ─── 题号模式 ──────────────────────────────────────────────
# 阿拉伯数字: 1. 2. 3) (1) 1、
_RE_ARABIC = re.compile(r"^(\d+)\s*[.、．)\s]+")
# 中文大题: 一、 二、 三．
_RE_CHINESE = re.compile(r"^([一二三四五六七八九十]+)\s*[、．\s]+")
# 括号小题: (1) （2） (3)
_RE_PAREN = re.compile(r"^[（(](\d+)[）)]")
# 圈号: ①②③④⑤⑥⑦⑧⑨⑩
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def _parse_question_number(text: str) -> Tuple[Optional[int], str]:
    """尝试从文本开头提取题号，返回(题号, 匹配到的数字文本)"""
    # 括号小题
    m = _RE_PAREN.match(text)
    if m:
        return int(m.group(1)), m.group(0)
    # 圈号
    if text and text[0] in _CIRCLED:
        return _CIRCLED.index(text[0]) + 1, text[0]
    # 阿拉伯数字
    m = _RE_ARABIC.match(text)
    if m:
        return int(m.group(1)), m.group(0)
    # 中文大题
    m = _RE_CHINESE.match(text)
    if m:
        cn_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                   "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        cn = m.group(1)
        if cn in cn_map:
            return cn_map[cn], m.group(0)
        # 十一、十二等
        if cn.startswith("十") and len(cn) > 1:
            return 10 + cn_map.get(cn[1], 0), m.group(0)
    return None, ""


# ─── 主入口 ────────────────────────────────────────────────

def cut_questions(ocr_blocks: List[dict], gap_threshold: int = 40) -> List[QuestionGroup]:
    """
    将 OCR 块按题号规则 + 版面规则切分为题目组。

    Args:
        ocr_blocks: OCR 返回的块列表 [{"text": "...", "pos": [x,y,w,h]}, ...]
        gap_threshold: y 方向间距阈值(px)，超过此值视为新题 (fallback)

    Returns:
        QuestionGroup 列表，每组包含若干 OCR 块
    """
    # 1. 转换为 OCRBlock，按 y 坐标排序
    blocks = [OCRBlock(b["text"], b.get("pos", [0, 0, 0, 0])) for b in ocr_blocks]
    blocks.sort(key=lambda b: (b.y, b.x))

    if not blocks:
        return []

    # 2. 第一遍扫描：找题号锚点
    anchors: List[int] = []  # block 索引
    numbers: List[int] = []  # 题号值
    for i, b in enumerate(blocks):
        num, _ = _parse_question_number(b.text)
        if num is not None:
            anchors.append(i)
            numbers.append(num)

    # 3. 按锚点分组
    groups: List[QuestionGroup] = []
    if anchors:
        # 锚点之前的块 → 归入第一个组（可能是标题/说明等）
        if anchors[0] > 0:
            pre_blocks = blocks[:anchors[0]]
            if any(len(b.text) > 2 for b in pre_blocks):
                groups.append(QuestionGroup(pre_blocks, 0, ""))

        # 每个锚点开始一个新组
        for ai, anchor_idx in enumerate(anchors):
            start = anchor_idx
            end = anchors[ai + 1] if ai + 1 < len(anchors) else len(blocks)
            group_blocks = blocks[start:end]
            num_text = ""
            num_val, num_text = _parse_question_number(group_blocks[0].text)
            groups.append(QuestionGroup(group_blocks, numbers[ai], num_text))
    else:
        # 4. 无题号：按 y 间距 fallback 分组
        current: List[OCRBlock] = [blocks[0]]
        for i in range(1, len(blocks)):
            gap = blocks[i].y - blocks[i - 1].bottom
            if gap > gap_threshold:
                groups.append(QuestionGroup(current, len(groups) + 1, ""))
                current = [blocks[i]]
            else:
                current.append(blocks[i])
        if current:
            groups.append(QuestionGroup(current, len(groups) + 1, ""))

    return groups


def cut_to_questions(ocr_blocks: List[dict]) -> List[dict]:
    """
    便捷函数：直接返回前端可用的题目列表
    """
    groups = cut_questions(ocr_blocks)
    groups = _split_arithmetic_groups(groups)
    questions = []
    for g in groups:
        questions.append({
            "question_number": g.question_number,
            "question_text": g.full_text,
            "bbox": g.bbox,
            "blocks_count": len(g.blocks),
        })
    return questions


# ─── 密集算式拆分（2026-05-19）────────────────────────────
_RE_ARITH = re.compile(r'(\d+)\s*[+\-×÷]\s*\d+')


def _is_arithmetic_block(text: str) -> bool:
    """检测文本是否含算术表达式"""
    return bool(_RE_ARITH.search(text))


def _split_arithmetic_groups(groups: List[QuestionGroup]) -> List[QuestionGroup]:
    """对 y-gap fallback 产生的大组，按算术行二次拆分为独立题目"""
    result: List[QuestionGroup] = []
    for g in groups:
        blocks = g.blocks
        # 只有 ≥3 个 block 且 ≥2 个含算术才拆分
        arith_blocks = [b for b in blocks if _is_arithmetic_block(b.text)]
        if len(blocks) < 3 or len(arith_blocks) < 2:
            result.append(g)
            continue

        # 拆分：以每个算术 block 为中心，往前吞非算术邻居
        sub_groups: List[List[OCRBlock]] = []
        pending_non_arith: List[OCRBlock] = []
        qn = 1
        for b in blocks:
            if _is_arithmetic_block(b.text):
                merged = pending_non_arith + [b]
                sub_groups.append(merged)
                pending_non_arith = []
                qn += 1
            else:
                if sub_groups:
                    sub_groups[-1].append(b)
                else:
                    pending_non_arith.append(b)
        # 尾部的非算术块归入最后一组
        if pending_non_arith and sub_groups:
            sub_groups[-1].extend(pending_non_arith)
        elif pending_non_arith and not sub_groups:
            result.append(g)
            continue

        for sg in sub_groups:
            result.append(QuestionGroup(sg, len(result) + 1, ""))
    return result
