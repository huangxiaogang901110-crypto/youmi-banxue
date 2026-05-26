# Repair-3D Real Eval Runner

- dry_run: `True`
- run_real: `False`
- no_paid: `True`
- sample_dir: `/home/hermes_me/yomi-codex-r1/local_eval_samples/repair3d_user_samples`
- manifest_complete: `True`
- sidecar_complete: `True`
- call_source: `repair3d_real_eval`
- db_path: `/tmp/repair3d_real_eval.db`
- sample_ids: `["vertical_math_01", "english_text_01", "annotated_header_01", "tilted_01", "non_homework_01"]`
- qwen_timeout_seconds: `30`
- model_calls_total: `0`
- model_calls_per_image: `0.0`
- cost_total: `n/a`
- cost_per_image: `n/a`
- cost_available: `False`
- non_homework_result: `{"document_type": "unknown", "expected_document_type": "non_homework", "page_type": "unknown", "questions_count": 0, "sample_id": "non_homework_01", "status": "planned", "terminal_status": "needs_review"}`

| sample_id | filename | status | questions | model_calls | cost_cny | skipped_reason | terminal_status_distribution |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| vertical_math_01 | vertical_math_01.jpg | planned | 0 | 0 | n/a | - | {"needs_review": 1} |
| english_text_01 | english_text_01.jpg | planned | 0 | 0 | n/a | - | {"needs_review": 1} |
| annotated_header_01 | annotated_header_01.jpg | planned | 0 | 0 | n/a | - | {"needs_review": 1} |
| tilted_01 | tilted_01.jpg | planned | 0 | 0 | n/a | - | {"needs_review": 1} |
| non_homework_01 | non_homework_01.jpg | planned | 0 | 0 | n/a | - | {"needs_review": 1} |
