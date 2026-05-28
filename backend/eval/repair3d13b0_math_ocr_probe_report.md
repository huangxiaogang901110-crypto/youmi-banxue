# Repair3D 13B-0 Math OCR Probe

- input_path: `/tmp/ocrblocks_verticalmath01_standalone.json`
- total_blocks: 142
- math_blocks: 110
- meta_blocks_filtered: 2
- candidate_questions: 29
- answer_bbox_count: 25
- answer_bbox_ratio: 0.8621
- suspected_meta_false_positive_count: 0
- ungrouped_math_blocks: 67
- header_not_treated_as_question: True
- quality_gate_passed: True

## Quality Gate

- candidate_questions >= 28: True
- suspected_meta_false_positive_count <= 2: True
- answer_bbox_ratio >= 80%: True
- header_not_treated_as_question: True

## Answer BBox Examples

- `4x5=/0` -> `equals_right` `[134.0, 457.0, 23.0, 35.0]`
- `2×9=` -> `right_neighbor` `[437.0, 422.0, 32.0, 41.0]`
- `3×2=6` -> `equals_right` `[127.0, 500.25, 22.0, 21.75]`
- `7×5=35` -> `equals_right` `[308.33, 468.0, 34.67, 26.0]`
- `5×3=` -> `right_neighbor` `[445.0, 460.0, 38.0, 31.0]`

## Candidate Notes

- candidate_questions only count question seeds with local answer-anchor clues.
