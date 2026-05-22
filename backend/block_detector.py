"""
版面大块检测模块 — 将 OCR blocks 分组为大块(Block)
用于语文/数学/英语作业的分块展示
"""
from typing import List, Dict
from models import Block


def detect_blocks(ocr_blocks: List[dict], img_w: int, img_h: int, subject: str = "") -> List[Block]:
    """
    输入: OCR blocks [{text, x, y, w, h}, ...]
    输出: Block[] 按视觉顺序排列的大块
    """
    if not ocr_blocks:
        return []

    # 按Y排序
    blocks = sorted(ocr_blocks, key=lambda b: (b["y"], b["x"]))

    # 计算平均行高
    heights = [b.get("h", 20) for b in blocks if b.get("h", 0) > 5]
    avg_h = sum(heights) / len(heights) if heights else 30

    # 检测分隔点
    split_indices = []
    for i in range(1, len(blocks)):
        prev = blocks[i - 1]
        curr = blocks[i]
        gap = curr["y"] - (prev["y"] + prev.get("h", 0))

        # 规则1: 大空白间隔
        if gap > avg_h * 3:
            split_indices.append(i)
            continue

        # 规则2: 题号大幅跳跃
        import re
        prev_num = _extract_number(prev["text"])
        curr_num = _extract_number(curr["text"])
        if prev_num is not None and curr_num is not None and curr_num - prev_num > 5:
            split_indices.append(i)
            continue

        # 规则3: 题型标签行
        if _is_section_header(curr["text"]):
            split_indices.append(i)
            continue

    # 按分隔点分组
    groups = []
    start = 0
    for si in sorted(set(split_indices)):
        if si > start:
            groups.append(blocks[start:si])
        start = si
    if start < len(blocks):
        groups.append(blocks[start:])

    # 合并小组（<2个block的组合并到相邻组）
    merged = []
    buf = []
    for g in groups:
        if len(g) < 2 and merged:
            merged[-1].extend(g)
        elif len(g) < 2 and buf:
            buf.extend(g)
        else:
            if buf:
                merged.append(buf)
                buf = []
            merged.append(g)
    if buf:
        if merged:
            merged[-1].extend(buf)
        else:
            merged.append(buf)
    if not merged:
        merged = [blocks]

    # 生成 Block 对象
    result = []
    for idx, group in enumerate(merged):
        title = _guess_block_title(group, subject)
        btype = _guess_block_type(group, subject)
        min_x = min(b["x"] for b in group)
        min_y = min(b["y"] for b in group)
        max_x = max(b["x"] + b.get("w", 50) for b in group)
        max_y = max(b["y"] + b.get("h", 20) for b in group)
        import uuid
        result.append(Block(
            block_id=uuid.uuid4().hex[:10],
            title=title,
            subject=subject,
            question_type=btype,
            bbox=[float(min_x), float(min_y), float(max_x - min_x), float(max_y - min_y)],
            order=idx,
        ))
    return result


def _extract_number(text: str):
    import re
    m = re.match(r'^\s*(\d{1,3})', text.strip())
    return int(m.group(1)) if m else None


def _is_section_header(text: str) -> bool:
    """检测是否为大块标题行"""
    t = text.strip()
    # 题型关键词
    type_keywords = ["口算", "竖式", "填空", "选择", "判断", "连线", "画图",
                     "阅读", "完形", "写作", "听力", "翻译",
                     "看拼音", "组词", "造句", "默写", "背诵"]
    for kw in type_keywords:
        if kw in t:
            return True
    # 大题号模式: "一、" "二、" ...
    import re
    if re.match(r'^[一二三四五六七八九十]、', t):
        return True
    # 数字+顿号 + 至少2个中文字（排除纯小题如 "1. 3×5="）
    if re.match(r'^\d{1,2}[、.．]\s*[\u4e00-\u9fff]{2,}', t):
        return True
    return False


def _guess_block_title(blocks: List[dict], subject: str) -> str:
    """根据block内容猜测大块标题"""
    if not blocks:
        return ""
    first = blocks[0]["text"].strip()
    # 如果首行就是标题行，直接用作标题
    if _is_section_header(first):
        return first[:30]
    # 否则用首行前几个字
    return first[:20]


def _guess_block_type(blocks: List[dict], subject: str) -> str:
    """根据block内容猜测题型"""
    full = " ".join(b["text"] for b in blocks)
    if "口算" in full: return "口算"
    if "竖式" in full: return "竖式"
    if "填空" in full: return "填空"
    if "选择" in full: return "选择"
    if "判断" in full: return "判断"
    if "连线" in full: return "连线"
    if "阅读" in full: return "阅读"
    if "看拼音" in full: return "看拼音"
    if "完形" in full: return "完形填空"
    if "写作" in full or "作文" in full: return "写作"
    return "其他"
