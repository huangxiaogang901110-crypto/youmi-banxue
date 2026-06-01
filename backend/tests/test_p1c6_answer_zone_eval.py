"""
Tests for P1-C-6 Phase A answer zone evaluator.

Covers:
  1. Horizontal: rightmost answer picks the rightmost candidate, not left distractor
  2. Horizontal: distant digit block outside zone is ignored
  3. Multi-column: adjacent columns don't borrow each other's bbox
  4. Multi-column: same answer digits in different columns = no cross_cell (bbox-in-zone check)
  5. Vertical: existing verticalmath02 results unchanged

All fixtures are synthetic — no real OCR/Qwen/DeepSeek calls.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eval.p1c6_answer_zone_eval import evaluate_fixture

# ── Fixture definitions (mirrors registry in evaluator) ──

FIXTURES = {
    "horizontal_math_synth": {
        "name": "horizontal_math_synth",
        "layout_type": "horizontal",
        "db_path": "/tmp/p1c6_horizontal_synth.db",
        "image_size": (800, 600),
        "synthetic": True,
    },
    "multi_column_dense_synth": {
        "name": "multi_column_dense_synth",
        "layout_type": "unknown",
        "db_path": "/tmp/p1c6_multicolumn_synth.db",
        "image_size": (800, 600),
        "synthetic": True,
    },
}

# ── Helpers ──

def _get_question_result(stats, question_id):
    """Get per_question entry by question_id."""
    for pq in stats["per_question"]:
        if pq["question_id"] == question_id:
            return pq
    return None


# ═══════════════════════════════════════════════════
# Phase A-2: Horizontal Math Fixture
# ═══════════════════════════════════════════════════

class TestHorizontalMathSynthetic:
    """5-questions horizontal arithmetic, answers to the right of each question."""

    @classmethod
    def setup_class(cls):
        cls.stats = evaluate_fixture(FIXTURES["horizontal_math_synth"])

    def test_all_anchors_found(self):
        assert self.stats["anchor_found"] == 5

    def test_all_zones_built(self):
        assert self.stats["zone_built"] == 5

    def test_all_answers_found(self):
        assert self.stats["answer_found"] == 5
        assert self.stats["question_count"] == 5

    def test_no_cross_cell(self):
        assert self.stats["cross_cell_suspected"] == 0

    def test_rightmost_wins_duplicate(self):
        """Q1: '12+34=' -> answer '46'. Two '46' at x=120 and x=150.
        Horizontal selector picks RIGHTMOST (x=150), not left distractor."""
        pq = _get_question_result(self.stats, "h1")
        assert pq is not None, "Q1 (h1) not in results"
        assert pq["has_bbox"] is True
        bbox = pq["bbox"]
        assert bbox[0] == 150, f"Expected x=150 (rightmost), got x={bbox[0]}"
        assert bbox[1] == 80, f"Expected y=80, got y={bbox[1]}"

    def test_distant_duplicate_not_picked(self):
        """Q3: '100-50=' -> answer '50'. One at (170,300) in zone,
        another at (50,500) outside zone. Must NOT pick distant one."""
        pq = _get_question_result(self.stats, "h3")
        assert pq is not None, "Q3 (h3) not in results"
        assert pq["has_bbox"] is True
        bbox = pq["bbox"]
        assert bbox[0] == 170, f"Expected x=170, got x={bbox[0]}"
        assert bbox[1] == 300, f"Expected y=300, got y={bbox[1]}"

    def test_simple_questions_correct(self):
        """Q2 '5+7=12', Q4 '6x8=48', Q5 '99/3=33' all find correct answers."""
        pq2 = _get_question_result(self.stats, "h2")
        assert pq2 is not None and pq2["has_bbox"] is True
        assert pq2["bbox"][0] == 150 and pq2["bbox"][1] == 175

        pq4 = _get_question_result(self.stats, "h4")
        assert pq4 is not None and pq4["has_bbox"] is True
        assert pq4["bbox"][0] == 140 and pq4["bbox"][1] == 418

        pq5 = _get_question_result(self.stats, "h5")
        assert pq5 is not None and pq5["has_bbox"] is True
        assert pq5["bbox"][0] == 150 and pq5["bbox"][1] == 518


# ═══════════════════════════════════════════════════
# Phase A-2: Multi-Column Dense Fixture
# ═══════════════════════════════════════════════════

class TestMultiColumnDenseSynthetic:
    """4 questions in 2 columns. Same answers '5' and '7' in both columns.
    Each question picks own column's block, not adjacent column's."""

    @classmethod
    def setup_class(cls):
        cls.stats = evaluate_fixture(FIXTURES["multi_column_dense_synth"])

    def test_all_anchors_found(self):
        assert self.stats["anchor_found"] == 4

    def test_all_zones_built(self):
        assert self.stats["zone_built"] == 4

    def test_all_answers_found(self):
        assert self.stats["answer_found"] == 4
        assert self.stats["question_count"] == 4

    def test_cross_cell_no_false_positive(self):
        """cross_cell=0: '5' shared by mc1+mc3 and '7' by mc2+mc4.
        With bbox-in-zone check, same digits in different columns are correctly
        NOT flagged because each bbox falls only in its own question's zone."""
        assert self.stats["cross_cell_suspected"] == 0

    def test_col_a_no_borrow_from_col_b(self):
        """Q1 (Col A, '2+3=5'): answer '5' at x=80. NOT Col B's at x=430."""
        pq = _get_question_result(self.stats, "mc1")
        assert pq is not None and pq["has_bbox"] is True
        bbox = pq["bbox"]
        assert bbox[0] == 80, f"Expected Col A '5' x=80, got x={bbox[0]}"
        assert bbox[1] == 100, f"Expected y=100, got y={bbox[1]}"

    def test_col_b_no_borrow_from_col_a(self):
        """Q3 (Col B, '3+2=5'): answer '5' at x=430. NOT Col A's at x=80."""
        pq = _get_question_result(self.stats, "mc3")
        assert pq is not None and pq["has_bbox"] is True
        bbox = pq["bbox"]
        assert bbox[0] == 430, f"Expected Col B '5' x=430, got x={bbox[0]}"
        assert bbox[1] == 100, f"Expected y=100, got y={bbox[1]}"

    def test_adjacent_columns_same_row_independent(self):
        """Q1 (Col A) and Q3 (Col B) same row (y=40) different columns.
        Zone for Q1 does NOT include Q3's answer area."""
        pq1 = _get_question_result(self.stats, "mc1")
        pq3 = _get_question_result(self.stats, "mc3")
        assert pq1["bbox"][0] == 80
        assert pq3["bbox"][0] == 430
        assert pq1["bbox"] != pq3["bbox"], "Col A and Col B must have different bboxes"

    def test_q2_q4_independent(self):
        """Q2 (Col A, '4+3=7') and Q4 (Col B, '5+2=7'). Same digits, different columns."""
        pq2 = _get_question_result(self.stats, "mc2")
        pq4 = _get_question_result(self.stats, "mc4")
        assert pq2["bbox"][0] == 80
        assert pq4["bbox"][0] == 430
        assert pq2["bbox"] != pq4["bbox"]


# ═══════════════════════════════════════════════════
# Regression: VerticalMath02 unchanged
# ═══════════════════════════════════════════════════

def test_verticalmath02_regression():
    """Existing vertical fixture results unchanged after Phase A-2 changes."""
    fixture = {
        "name": "verticalmath02",
        "layout_type": "vertical",
        "db_path": "/tmp/p1coverlayreal2.db",
        "image_size": (1280, 1280),
    }
    stats = evaluate_fixture(fixture)
    assert stats["status"] == "ok"
    assert stats["question_count"] == 20
    assert stats["anchor_found"] == 20
    assert stats["zone_built"] == 15
    assert stats["answer_found"] == 13
    assert stats["cross_cell_suspected"] == 0
    rej = dict(stats["rejected"])
    assert rej.get("zone_too_short", 0) == 5
    assert rej.get("no_candidate_in_zone", 0) == 2
