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


def _y_overlap_ratio(block_a: "OCRBlock", block_b: "OCRBlock") -> float:
    """
    计算两个 block 在 y 方向的重叠比例，用较矮 block 的高度归一化。

    含 OCR 抖动容差：将 min_h 的 50% 加入 overlap 计算，
    使 gap 为小正数（OCR y 坐标轻微抖动）的同行 block 也能正确判定。

    返回值 >= 0.3 视为同行；gap 超过 50% min_h 时返回 0。
    """
    min_h = min(block_a.h, block_b.h)
    if min_h <= 0:
        return 0.0
    tol = min_h * 0.5  # 容差：较矮块高度的 50%，覆盖 OCR 轻微 y 抖动
    overlap = min(block_a.bottom, block_b.bottom) - max(block_a.y, block_b.y) + tol
    if overlap <= 0:
        return 0.0
    return overlap / min_h


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
        # 4. 无题号：按 y 间距 fallback 分组；同行(y 重叠)且 x 间距大也拆（Repair-2B）
        current: List[OCRBlock] = [blocks[0]]
        for i in range(1, len(blocks)):
            prev = blocks[i - 1]
            cur = blocks[i]
            gap = cur.y - prev.bottom
            if gap > gap_threshold:
                groups.append(QuestionGroup(current, len(groups) + 1, ""))
                current = [cur]
            elif _y_overlap_ratio(prev, cur) >= 0.3:
                # y 方向有明显重叠 → 同行（对 OCR 轻微 y 抖动稳健），检测水平列间距
                x_gap = cur.x - prev.right
                if x_gap > gap_threshold * 2:
                    groups.append(QuestionGroup(current, len(groups) + 1, ""))
                    current = [cur]
                else:
                    current.append(cur)
            else:
                current.append(cur)
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


def _expand_multi_arith_block(b: OCRBlock) -> List[OCRBlock]:
    """将单 block 内多个算式按空白拆为伪子块（Repair-2B）"""
    matches = list(_RE_ARITH.finditer(b.text))
    if len(matches) < 2:
        return [b]
    parts = [p.strip() for p in re.split(r'\s{2,}', b.text) if p.strip()]
    if len(parts) < 2:
        parts = [m.group(0) for m in matches]
    n = len(parts)
    sub_w = max(1, b.w // n)
    result: List[OCRBlock] = []
    for i, part in enumerate(parts):
        result.append(OCRBlock(part, [b.x + i * sub_w, b.y, sub_w, b.h]))
    return result


def _split_arithmetic_groups(groups: List[QuestionGroup]) -> List[QuestionGroup]:
    """对 y-gap / x-gap fallback 产生的大组，按算术行二次拆分为独立题目（Repair-2B）"""
    result: List[QuestionGroup] = []
    for g in groups:
        # 先展开单 block 内多算式
        expanded: List[OCRBlock] = []
        for b in g.blocks:
            expanded.extend(_expand_multi_arith_block(b))
        blocks = expanded

        arith_blocks = [b for b in blocks if _is_arithmetic_block(b.text)]
        # 无算术块则不拆分（降低阈值：≥1 block 且 ≥1 算术即拆）
        if not arith_blocks:
            result.append(g)
            continue

        # 拆分：以每个算术 block 为中心，往前吞非算术邻居
        sub_groups: List[List[OCRBlock]] = []
        pending_non_arith: List[OCRBlock] = []
        for b in blocks:
            if _is_arithmetic_block(b.text):
                sub_groups.append(pending_non_arith + [b])
                pending_non_arith = []
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
