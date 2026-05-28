import base64
import gzip
import json

from math_ocr_first import (
    _answer_tail_bbox_from_block,
    _find_row_value_answer,
    _infer_answer_bbox,
    _is_instruction_block,
    _is_meta_block,
    _make_question,
    _normalize_blocks,
    math_ocr_first_extract,
)


_SMOKE_FIXTURE_GZ_B64 = (
    "H4sIALAcFmoC/61bTYucRRC++yuWnCLjJN1VXdXdgfEgePPoSZGwJguJRgO66IoIfuEXasBEx0RBRMSAn2AwyUb0kJ/izmxy8i/4"
    "zi6EreddqtPEy8JkZ6u66+Opqqc6rz+0tnbkufPPnjx7+siJtSOvbLy0efbU+rkX1jfPhHjy5c31F0+vnzv/4saRR1ZfPPXSxvrm"
    "xumT65urL1MgnQaZkj5J8QTxCapP7X/v2XPnTz3/8vCdp4dPa2uv7/0c/n1zY2v/Lxc3r+1u//DYzo2P9v5g75dbw29I7318bfUx"
    "5HufXx0+x3Lv45nhY+a9T288criW3Z9+4ju//rWc/wFKSrRaVI0WykZLDOqqGVTcnV9bXru1+OiqVcTKRhEHsdcJVhNlV9Hyw4uL"
    "T761KmKwGojtVYJRkMhVsHNruAMoKMUq4GgVJKNAgq/gxptw/Ggdzsk6/MB19gwUfYffemdx4bvdX+agpJBvJHuHXF0lR5e/X12+"
    "e2HxwWeL97d3r7y7/PLi8vr14efdS5cfBv9HgUArVjHZQKvF9//8++XX31gV2d4MFMRqFHDqdr+yzRXO1kHJXoF8/+9u/w3HDyie"
    "vQtEPxOfeBwS3aZfCuqhSUP4YH3wbgDpYHtAET/14rHdr64tP/1+8d7lIawWf82Xn//2z5tvQyAnwBOKbjxpuF+dvtNTtHoKGM5P"
    "mLQls+MBbsLJ3qS4eR+D+De5PafZcSwlCv6xl2ALjcXPDbo9rzPwP8BKInsHtbDCvjOmsVjxiTOIJ3t+6+zkQyNNshUvCc1jxR+o"
    "xHugqC0HMJhHxbo4JZsfAvnh1z5eeVghiAgsxOKlQ/E15NtzmbEgglSrAgE2WCeTbyYZm4kLwEi2ftCuCrhz40OMouiKh9oX/Qp0"
    "FMyTkoB5go1RCxQcW9ah2TlAChH5H51M9xGnhR8gTncvXR31mwXk+zU6t2ykM+yhIEaLeD2g+iGqM0GsFiijiNX2AuJXgzxBB5A2"
    "5FsPqy9fp9TAaQns4rRfqFMDpavtMTj1oPTg3fLvn1cQqQEhSvX8K6nn/NIwDsxCzC2UHqeXzV+J9vSiPQC0c+NnK34Kxz9g7/4m"
    "bECHPIvJb1UEhjiSrlaFViAHCQAOFrEoqqWnBMiqjmEG2CIgyfpAqUfBjLAKsIJ46LSkq1NZIVwkCFNo5SVZPxfpuUE6LEwz+MB6"
    "WVJPFRiV4RzIFR8hC1J3kbE1QHLxRh32syxuYQRFtL+4MFH8PIP4iQoQATMmweGlFf91lrCPgy5LkGfAJPZxrkxoFoPfyEmxlaBC"
    "p8itbpRGUCRUwFBWRQE6qTabUUwCiKJqE1liD1bPMAUghGpwodpvUxYfvAeFwEKcwmiM0hvM4Q+fgXMJpPtnbzahMgqfKqCBYNCA"
    "OaDRpUwqJIDADaLbRiffPnXUpFBWOD8kWOzhiWaRMX/BPNFGfoo9PCe1xzAla59sAZp8gOYJkgUHC9Z+9BdvDtNGao3qI1R4jeyR"
    "BdzooR8FXNDsOjd2NShj2EHnYnbVnikyj+UDE6Rqm4eUeuis5aWby5+/c1l4VXKrb70PGnZn+2Mc80AJuyx2aVLluz9+sfjzwpgt"
    "HyERP0ip7KDLcRBU2MvEXHvijI4NfdL+Je/88u1wzzGfCuyOJvFAKzYcV5qRl4ON7CQ9kcdpknRWcDwniywZWuMYqadyCE0i1g7o"
    "z1CDdtHBIxqVUDp5422jcgDLyTBaZUgajj3jrcYp4/wPjT2eXqXn9Alsk5T844eezpjAOKlA5FBxGWb/7Mc3oCQFiH2uLvPYoI3K"
    "VGVMjahN4AxtAcRO9m9Q/aqX2SXuGhe4Pceih5GZPemNwNlC4KkYOO7qqyGdI+6hIGWzxU3mns3HiHBRCMscPUqtRZgSbu3A7uru"
    "PRpkRcWFL5i9+mutFt4jz8Ig3eVZWBsZBbmU0ezWMCn0SB8VwupGDK5im8uggtkEYNNYhHfxEwcn1ZXwAptErFC+8MUnb/n8fYlw"
    "du7hhgbx2sinyg+QT6PjkxQ4Prg29Rz/0DcIAMQj+8Pw52so59E8NqsKTq52tk+NrAKopGqNX/z3M43ZZlTBgRAqwBrgaib15CzO"
    "lIWq23zofXXj/vKtSPR2My1el6ecJ6ligwz5hdgTtKcYHsJ9QAdbcMdKPbSZIHUJi3o0EW6Y/B4w65RkGvMMi8vBhz37SabuLNR4"
    "6SVp5QoqDRqkUHK1tAqB/5qoANJhlWlwaOzjaAEKh7ve2mVAaRb/7J0kODLH8M4Oz54syKl/ds7+u6QKXhXbmKjv1CmKlwji2UVo"
    "9U2TCh7eurVCQyjcc/hRWsHyvNbkvpBovELCR0JCEcSL1/c0aN1S/NVnDKTukOIjG6U6iRg6MlIBm72u3WQ+BJwVFbALOQ10VkTn"
    "Ag6oHjpLo/HUCadh0MUYstBcS3BhLfhBhLu9BMTiYCF5AO5+/AQJYnQURVgfG9uT0fZZQDw0oNjfNt6/JKE0YcwzRLjivrOOsTPR"
    "oMMdXGghjrWHQcYgZQjSGOARFVsEFWmYCJme0eldlk1844z6t1RBfA7eCy3tHGA4IgTl6mF0A+WmhGN7xfwCQkO6dj88Mg84N8Lu"
    "B153qG992kB6VlG8tU62zs2Nl7op+CRkPMhXHzICNKwzeDf6zUk8OPAdhg9+bp3BApYJxMPTCEhd6WQHEoD/0BLDG8vatVzC/3Ez"
    "Ng+0ENz3PlH9tx2D+OyJb3QoVDA64fQUqkvS+qd/alR5yT993/i++Hgbg78Rm33Bjy/IS0bjwMOycGjjPPx85qE3/gNWnssPGjYA"
    "AA=="
)


def _annotate_blocks(raw_blocks):
    blocks, _, page_height = _normalize_blocks(raw_blocks)
    for block in blocks:
        block["is_meta"] = _is_meta_block(block, page_height)
        block["is_instruction"] = _is_instruction_block(block)
    return blocks, page_height


def _load_smoke_blocks():
    payload = gzip.decompress(base64.b64decode(_SMOKE_FIXTURE_GZ_B64)).decode("utf-8")
    return json.loads(payload)["blocks"]


def test_meta_filter_blocks_header():
    blocks, page_height = _annotate_blocks(
        [
            {"text": "2年级B上", "x": 0, "y": 10, "w": 20, "h": 20},
            {"text": "第3课时", "x": 30, "y": 10, "w": 20, "h": 20},
        ]
    )

    assert _is_meta_block(blocks[0], page_height) is True
    assert _is_meta_block(blocks[1], page_height) is True


def test_equals_right_answer_bbox():
    blocks, _ = _annotate_blocks([{"text": "34+46=80", "x": 10, "y": 20, "w": 120, "h": 24}])

    answer = _answer_tail_bbox_from_block(blocks[0])

    assert answer is not None
    assert answer["bbox"][0] > blocks[0]["center_x"]


def test_right_neighbor_answer_bbox():
    blocks, _ = _annotate_blocks(
        [
            {"text": "52+19", "x": 10, "y": 10, "w": 60, "h": 20},
            {"text": "71", "x": 80, "y": 10, "w": 20, "h": 20},
        ]
    )
    seed = blocks[0]

    answer = _find_row_value_answer(seed, blocks, [seed])

    assert answer is not None
    assert answer["source"] == "right_neighbor"
    assert answer["bbox"] == blocks[1]["bbox"]


def test_no_answer_bbox_needs_review():
    blocks, _ = _annotate_blocks([{"text": "52+19", "x": 10, "y": 10, "w": 60, "h": 20}])
    seed = blocks[0]
    seed["question_index"] = 0

    answer_bbox, source, needs_review = _infer_answer_bbox({"seed": seed}, blocks, [seed])

    assert answer_bbox is None
    assert source is None
    assert needs_review is True


def test_needs_review_not_correct():
    seed = {
        "question_index": 0,
        "text": "52+19",
        "bbox": [10.0, 10.0, 60.0, 20.0],
        "student_answer": "",
    }

    question = _make_question(seed, None, True, 0.0, {})

    assert question["needs_review"] is True
    assert question["is_correct"] is None


def test_math_ocr_first_extract_smoke():
    result = math_ocr_first_extract(
        _load_smoke_blocks(),
        {"doc_family": "math_vertical", "page_type": "math_homework"},
    )

    assert result["quality_gate_passed"] is True
    assert result["questions"]
    assert result["stats"]["answer_bbox_ratio"] >= 0.8


def test_non_math_blocks_skipped():
    result = math_ocr_first_extract(
        [
            {"text": "语文阅读理解", "x": 10, "y": 10, "w": 120, "h": 20},
            {"text": "写出中心思想", "x": 10, "y": 40, "w": 140, "h": 20},
        ],
        {"doc_family": "chinese", "page_type": "unknown"},
    )

    assert result["stats"]["candidate_seed_count"] == 0
    assert result["quality_gate_passed"] is False
    assert result["questions"] == []


def test_adjacent_page_roi_excludes_right_column_noise():
    result = math_ocr_first_extract(
        [
            {"text": "1.直接写出得数。", "x": 100, "y": 100, "w": 30, "h": 120},
            {"text": "2+7", "x": 120, "y": 220, "w": 40, "h": 24},
            {"text": "9", "x": 178, "y": 220, "w": 18, "h": 24},
            {"text": "1.直接写", "x": 650, "y": 110, "w": 60, "h": 22},
            {"text": "1×3=", "x": 655, "y": 220, "w": 50, "h": 20},
        ],
        {"doc_family": "math_vertical", "page_type": "math_homework"},
    )

    assert result["quality_gate_passed"] is False
    assert result["stats"]["roi_bbox"][0] == 88.0
    assert result["stats"]["roi_bbox"][2] < 560.0
    assert result["stats"]["roi_result"]["reject_rules"] == ["exclude_right_adjacent_page_cluster"]


def test_vertical_group_stays_in_same_column_result_zone():
    result = math_ocr_first_extract(
        [
            {"text": "2.用竖式计算。", "x": 100, "y": 620, "w": 24, "h": 120},
            {"text": "34+46=80", "x": 120, "y": 742, "w": 96, "h": 20},
            {"text": "52+19", "x": 240, "y": 740, "w": 68, "h": 19},
            {"text": "7", "x": 330, "y": 736, "w": 18, "h": 24},
            {"text": "61-34", "x": 408, "y": 742, "w": 66, "h": 20},
            {"text": "27", "x": 484, "y": 740, "w": 28, "h": 24},
            {"text": "52", "x": 292, "y": 760, "w": 28, "h": 36},
            {"text": "9", "x": 308, "y": 796, "w": 20, "h": 20},
            {"text": "71", "x": 286, "y": 828, "w": 28, "h": 30},
            {"text": "43-37+49", "x": 132, "y": 870, "w": 102, "h": 22},
            {"text": "55", "x": 248, "y": 868, "w": 28, "h": 24},
            {"text": "8+1=9", "x": 360, "y": 904, "w": 64, "h": 22},
        ],
        {"doc_family": "math_vertical", "page_type": "math_homework"},
    )

    assert result["quality_gate_passed"] is True
    target = next(question for question in result["questions"] if question["question_text"] == "52+19")
    assert target["answer_bbox_source"] == "vertical_result"
    assert target["answer_bbox"] == [286.0, 828.0, 28.0, 30.0]
