from test_eval_repair2_cached import *  # noqa: F401,F403


def test_repo_no_paid_quality_gate_and_gap_report_snapshot():
    result = cached_eval.evaluate_cached_samples(
        db_path=None,
        sample_dirs=[],
        limit=50,
        only_json_fixtures=False,
    )

    summary = result["summary"]
    gate = summary["quality_gate"]
    report = result["fixture_gap_report"]
    categories = {
        item["category_id"]: item
        for item in report["category_gap_summary"]
    }
    gaps_by_origin = {
        item["sample_origin"]: item
        for item in report["ground_truth_gaps"]["samples"]
    }
    fixture_cache_gaps = [
        item
        for item in report["ground_truth_gaps"]["samples"]
        if item["source_kind"] == "fixture_cache"
    ]

    assert summary["effective_sample_count"] == 11
    assert summary["skipped_count"] == 18
    assert summary["source_kind_counts"] == {"json_fixture": 10, "fixture_cache": 1}
    assert summary["skipped_samples"]["source_kind_counts"] == {"fixture_only": 18}
    assert summary["filter_metadata"]["skipped_reason_counts"] == {
        cached_eval.SKIPPED_NO_CACHE_REASON: 18
    }
    assert summary["violation_total_count"] == 0
    assert summary["page_type_counts"].get("unknown", 0) == 0
    assert summary["metrics"]["no_paid"] is True
    assert summary["metrics"]["zero_question_completed_rate"]["value"] == 0.0
    assert summary["metrics"]["answer_bbox_false_positive_rate"]["value"] == 0.0
    assert summary["metrics"]["model_calls_per_image"] == {"value": 0, "no_paid": True}
    assert summary["metrics"]["cost_per_image"] == {
        "value": 0,
        "currency": "cny",
        "no_paid": True,
    }
    assert gate["mode"] == "no_paid"
    assert gate["passed"] is True
    assert gate["checks"]["fixture_only_effective_count"]["observed"] == 0
    assert gate["checks"]["zero_question_completed_rate"]["passed"] is True
    assert gate["checks"]["answer_bbox_false_positive_rate"]["passed"] is True
    assert gate["checks"]["no_paid"]["passed"] is True
    assert gate["checks"]["model_calls_per_image"]["passed"] is True
    assert gate["checks"]["cost_per_image"]["passed"] is True

    assert categories["mixed_pinyin_english"]["status"] == "covered"
    assert categories["complex_chinese_page"]["status"] == "covered"
    assert categories["standalone_multiple_choice_page"]["status"] == "covered"
    assert categories["non_homework_image"]["status"] == "covered"
    assert categories["cover_or_instruction_page"]["status"] == "covered"
    assert categories["teacher_markup"]["status"] == "covered"
    assert categories["landscape_or_tilted_photo"]["status"] == "gap"
    assert categories["dense_multi_column_page"]["status"] == "gap"

    assert gaps_by_origin[
        "local_eval_samples/a95fa987431b6f696d5f996124fd8903_origin(1).jpg"
    ]["missing_fields"] == ["document_classification.doc_family"]

    # Cache-backed vs fixture-only ground truth gaps depend on current local cache hits.
    sample_bmp_gap = gaps_by_origin["backend/tests/fixtures/sample.bmp"]
    assert sample_bmp_gap["source_kind"] == "fixture_only"
    assert sample_bmp_gap["missing_truth_artifacts"] == [
        "json_sidecar",
        "cache_payload",
    ]
    assert sample_bmp_gap["missing_fields"] == [
        "document_classification.page_type",
        "document_classification.doc_family",
        "questions",
    ]

    assert len(fixture_cache_gaps) == 1
    assert fixture_cache_gaps[0]["missing_truth_artifacts"] == [
        "json_sidecar",
        "human_verified_ground_truth",
    ]
    assert fixture_cache_gaps[0]["missing_fields"] == []

    assert gaps_by_origin[
        "local_eval_samples/04fb8059d392d3235e042e8c9303f5bf_origin(1).jpg"
    ]["candidate_gap_categories"] == [
        "dense_multi_column_page",
        "landscape_or_tilted_photo",
    ]
