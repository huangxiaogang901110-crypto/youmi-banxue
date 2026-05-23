from __future__ import annotations

import re
from typing import Iterable

from pydantic import BaseModel, Field


ARITHMETIC_SIGNS = re.compile(r"[+\-×xX*/÷=]")
ENGLISH_WORD = re.compile(r"[A-Za-z]{3,}")


class DocumentClassification(BaseModel):
    doc_family: str = Field(default="unknown")
    subject: str = Field(default="unknown")
    support_level: str = Field(default="partial")
    route_hint: str = Field(default="general_review")
    reason: str = Field(default="")


def _merge_text(raw_text: str | None, question_texts: Iterable[str] | None) -> str:
    merged = []
    if raw_text:
        merged.append(raw_text)
    if question_texts:
        merged.extend([text for text in question_texts if text])
    return "\n".join(merged)


def classify_document(raw_text: str | None = None, question_texts: Iterable[str] | None = None) -> DocumentClassification:
    merged = _merge_text(raw_text, question_texts)
    text = merged.lower()
    sign_count = len(ARITHMETIC_SIGNS.findall(merged))

    if any(keyword in text for keyword in ("listen and order", "read and write", "match the", "cut", "english")):
        return DocumentClassification(
            doc_family="english_language",
            subject="english",
            support_level="unsupported",
            route_hint="language_review",
            reason="命中英语练习关键词，当前链路不做英语模板批改。",
        )

    if any(keyword in merged for keyword in ("拼音", "读音", "组词", "造句", "看拼音写词语", "照样子", "同音字")):
        return DocumentClassification(
            doc_family="chinese_language",
            subject="chinese",
            support_level="unsupported",
            route_hint="language_review",
            reason="命中语文/拼音练习关键词，当前链路仅做识别不做稳定批改。",
        )

    if "一半" in merged or "平均分" in merged:
        return DocumentClassification(
            doc_family="math_visual_concept",
            subject="math",
            support_level="partial",
            route_hint="math_visual_review",
            reason="命中图形/概念题关键词，当前链路可识别但不保证稳定判题。",
        )

    if any(keyword in merged for keyword in ("比大小", "万以内数", "单选题", "判断题", "填空题", "选择题")) and sign_count < 8:
        return DocumentClassification(
            doc_family="math_comparison_logic",
            subject="math",
            support_level="partial",
            route_hint="math_logic_review",
            reason="命中数学比较/选择/判断关键词，当前以识别为主。",
        )

    if any(keyword in merged for keyword in ("应用题", "答：", "答:", "多少本", "多少个", "多少元", "几年", "每天一练", "寒假作业")):
        return DocumentClassification(
            doc_family="math_word_problem",
            subject="math",
            support_level="partial",
            route_hint="math_word_problem",
            reason="命中应用题/作业页关键词，依赖 DeepSeek 兜底判题。",
        )

    if "竖式" in merged or "列竖式" in merged:
        return DocumentClassification(
            doc_family="math_vertical",
            subject="math",
            support_level="partial",
            route_hint="math_vertical_review",
            reason="命中列竖式关键词，当前规则判题覆盖有限。",
        )

    if sign_count >= 8 and any(keyword in merged for keyword in ("计算", "口算", "加减", "乘法", "除法", "以内")):
        return DocumentClassification(
            doc_family="math_arithmetic",
            subject="math",
            support_level="full",
            route_hint="math_rule_first",
            reason="算式符号密集且命中口算/计算关键词，适合数学主链路。",
        )

    if sign_count >= 12:
        return DocumentClassification(
            doc_family="math_arithmetic",
            subject="math",
            support_level="full",
            route_hint="math_rule_first",
            reason="算式符号密集，推断为算术练习页。",
        )

    if ENGLISH_WORD.search(merged) and sign_count == 0:
        return DocumentClassification(
            doc_family="english_language",
            subject="english",
            support_level="unsupported",
            route_hint="language_review",
            reason="命中大段英文词汇，当前链路不做英语模板批改。",
        )

    return DocumentClassification(
        doc_family="unknown",
        subject="unknown",
        support_level="partial",
        route_hint="general_review",
        reason="未命中稳定模板，保守按部分支持处理。",
    )
