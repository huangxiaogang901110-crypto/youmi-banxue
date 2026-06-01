#!/usr/bin/env python3
"""P1-C-6 Phase A: Offline answer zone evaluator.

Validates answer zone strategy without modifying pipeline main path.
Zero real API calls — uses cached DB fixtures only.
"""
from __future__ import annotations
import sqlite3, json, sys, math
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import _extract_digits, _fuzzy_match_digits, _classify_layout

# ── Config ──
LAYOUT_PARAMS = {
    "vertical":   {"conservative_depth": 250, "min_zone_height": 60, "margin": 4},
    "horizontal": {"conservative_depth": 120, "min_zone_height": 20, "margin": 2},
    "unknown":    {"conservative_depth": 180, "min_zone_height": 30, "margin": 2},
}
COL_TOLERANCE = 150  # px, center_x distance for same-column判定

# ── Fixture Registry ──
FIXTURES = [
    {
        "name": "verticalmath02",
        "layout_type": "vertical",
        "db_path": "/tmp/p1coverlayreal2.db",
        "image_size": (1280, 1280),
    },
    # Skeleton fixtures — NEEDS_REAL_DATA
    # {"name": "horizontal_math", "layout_type": "horizontal", "db_path": None, "image_size": (1280, 1280)},
    # {"name": "multi_column_dense", "layout_type": "unknown", "db_path": None, "image_size": (1280, 1280)},
]

# ── Core Logic ──

def _is_expression_block(text: str) -> bool:
    """Anchor block must contain arithmetic operators, not be pure digits."""
    return any(c in text for c in "+-x*X/÷=")


def find_question_anchor(question_text: str, ocr_blocks: list[dict]) -> dict | None:
    """Find OCR block containing question_text."""
    q_clean = question_text.replace(" ", "").rstrip("=")
    if not q_clean:
        return None
    for b in ocr_blocks:
        bt = b.get("text", "").replace(" ", "").rstrip("=")
        if q_clean in bt and _is_expression_block(bt):
            return b
    return None


def find_next_in_column(
    current_anchor: dict,
    all_questions: list[dict],
    ocr_blocks: list[dict],
    col_tolerance: int = COL_TOLERANCE,
) -> dict | None:
    """Find next question anchor in same column (strictly below current)."""
    ax, ay, aw, ah = current_anchor["x"], current_anchor["y"], current_anchor["w"], current_anchor["h"]
    ax2 = ax + aw
    cx = ax + aw / 2

    best_anchor = None
    best_y = None

    for q in all_questions:
        qt = (q.get("question_text") or "").replace(" ", "").rstrip("=")
        if not qt:
            continue
        other = find_question_anchor(qt, ocr_blocks)
        if other is None:
            continue
        # Must be below, and not the same anchor
        if other["y"] <= ay:
            continue
        if other["x"] == ax and other["y"] == ay:
            continue

        ox, ow = other["x"], other["w"]
        ox2 = ox + ow
        ocx = ox + ow / 2

        x_overlap = ox < ax2 and ox2 > ax
        center_near = abs(cx - ocx) < col_tolerance

        if x_overlap or center_near:
            if best_y is None or other["y"] < best_y:
                best_y = other["y"]
                best_anchor = other

    return best_anchor


def build_answer_zone(
    anchor: dict,
    next_anchor: dict | None,
    layout: str,
    img_w: int = 1280,
    img_h: int = 1280,
) -> dict | None:
    """Build answer zone bbox. Returns None if zone too short (low confidence)."""
    params = LAYOUT_PARAMS.get(layout, LAYOUT_PARAMS["unknown"])
    ax, ay, aw, ah = anchor["x"], anchor["y"], anchor["w"], anchor["h"]

    x1 = max(0, ax - 120)
    x2 = min(img_w, ax + aw + 120)
    y1 = ay + ah + 8  # start just below question anchor

    if next_anchor is not None:
        y2 = next_anchor["y"] - params["margin"]
    else:
        y2 = min(img_h, ay + params["conservative_depth"])

    zone_h = y2 - y1
    if zone_h < params["min_zone_height"]:
        return None  # Low confidence — reject

    return {"x": int(x1), "y": int(y1), "w": int(x2 - x1), "h": int(zone_h)}


def find_answer_in_zone(
    zone: dict,
    digit_blocks: list[dict],
    answer_digits: str,
    layout: str,
    anchor: dict | None = None,
) -> dict | None:
    """Find best answer block within zone. Uses fuzzy digit match."""
    if not answer_digits:
        return None

    zx, zy, zw, zh = zone["x"], zone["y"], zone["w"], zone["h"]

    candidates = [
        b for b in digit_blocks
        if (zx <= b["x"] < zx + zw and zy <= b["y"] < zy + zh
            and _fuzzy_match_digits(_extract_digits(b.get("text", "")), answer_digits))
    ]
    if not candidates:
        return None

    if layout == "vertical":
        return max(candidates, key=lambda b: b["y"])
    if layout == "horizontal":
        max_x = max(b["x"] for b in candidates)
        tied = [b for b in candidates if b["x"] == max_x]
        if len(tied) == 1:
            return tied[0]
        q_cy = (anchor["y"] + anchor["h"] / 2) if anchor else 0
        return min(tied, key=lambda b: abs(b["y"] - q_cy))
    if len(candidates) == 1:
        return candidates[0]
    qcx = (anchor["x"] + anchor["w"] / 2) if anchor else 0
    qcy = (anchor["y"] + anchor["h"] / 2) if anchor else 0
    return min(candidates, key=lambda b: abs(b["x"] - qcx) + abs(b["y"] - qcy))


def evaluate_fixture(fixture: dict) -> dict:
    """Run evaluator on one fixture. Returns stats dict."""
    db_path = fixture["db_path"]
    layout_type = fixture["layout_type"]
    img_w, img_h = fixture.get("image_size", (1280, 1280))

    if db_path is None:
        return {"name": fixture["name"], "status": "NEEDS_REAL_DATA", "error": "No DB path"}

    try:
        conn = sqlite3.connect(db_path)
        data = json.loads(conn.execute("SELECT data FROM parse_jobs").fetchone()[0])
        conn.close()
    except Exception as e:
        return {"name": fixture["name"], "status": "ERROR", "error": str(e)}

    questions_raw = data.get("questions", [])
    ocr_blocks = data.get("ocr_blocks", [])

    stats = {
        "name": fixture["name"],
        "status": "ok",
        "layout_type": layout_type,
        "question_count": len(questions_raw),
        "anchor_found": 0,
        "zone_built": 0,
        "answer_found": 0,
        "rejected": defaultdict(int),
        "cross_cell_suspected": 0,
        "per_question": [],
    }

    # Build digit block index (exclude stem area later per-question)
    all_digit_blocks = [
        b for b in ocr_blocks if _extract_digits(b.get("text", ""))
    ]

    # Track answer_digits → question_ids for cross-cell check
    answer_to_questions = defaultdict(list)

    for q_raw in questions_raw:
        q = dict(q_raw)
        qt = (q.get("question_text") or "").replace(" ", "").rstrip("=")
        sa = q.get("student_answer") or ""
        ad = _extract_digits(sa)

        if not ad:
            stats["rejected"]["no_student_answer"] += 1
            continue

        # Step 1: find anchor
        anchor = find_question_anchor(qt, ocr_blocks)
        if anchor is None:
            stats["rejected"]["no_anchor"] += 1
            continue
        stats["anchor_found"] += 1

        # Step 2: find next in column
        next_anchor = find_next_in_column(anchor, questions_raw, ocr_blocks)

        # Step 3: build zone
        zone = build_answer_zone(anchor, next_anchor, layout_type, img_w, img_h)
        if zone is None:
            stats["rejected"]["zone_too_short"] += 1
            continue
        stats["zone_built"] += 1

        # Step 4: find answer in zone
        # Exclude stem area (top-left 30% of anchor)
        ax, ay, aw, ah = anchor["x"], anchor["y"], anchor["w"], anchor["h"]
        stem_right = ax + aw * 0.3
        stem_bottom = ay + ah * 0.3
        digit_blocks = [
            b for b in all_digit_blocks
            if not (b["x"] < stem_right and b["y"] < stem_bottom)
        ]

        best = find_answer_in_zone(zone, digit_blocks, ad, layout_type, anchor)
        if best is None:
            stats["rejected"]["no_candidate_in_zone"] += 1
            continue
        stats["answer_found"] += 1

        # Track for cross-cell check
        answer_to_questions[ad].append(q.get("question_id", "?"))

        stats["per_question"].append({
            "question_id": q.get("question_id"),
            "has_bbox": True,
            "bbox": [best["x"], best["y"], best["w"], best["h"]],
        })

    # Cross-cell check
    for ad, qids in answer_to_questions.items():
        if len(qids) > 1:
            # Check if multiple questions found the SAME answer digits in different zones
            stats["cross_cell_suspected"] += len(qids) - 1

    return stats


# ── Report ──

def print_report(all_stats: list[dict]):
    """Print formatted report for all fixtures."""
    print("=" * 70)
    print("P1-C-6 Phase A: Answer Zone Evaluator")
    print("=" * 70)

    total_q = 0
    total_anchor = 0
    total_zone = 0
    total_answer = 0
    total_rejected = defaultdict(int)
    total_cross = 0
    fixtures_ok = 0
    fixtures_needs_data = 0

    for stats in all_stats:
        name = stats["name"]
        status = stats.get("status", "error")

        if status == "NEEDS_REAL_DATA":
            fixtures_needs_data += 1
            print(f"\n  [{name}] NEEDS_REAL_DATA — skeleton only")
            continue
        if status == "ERROR":
            print(f"\n  [{name}] ERROR: {stats.get('error')}")
            continue

        fixtures_ok += 1
        qc = stats["question_count"]
        af = stats["anchor_found"]
        zb = stats["zone_built"]
        ans = stats["answer_found"]
        rej = dict(stats["rejected"])
        cross = stats["cross_cell_suspected"]

        total_q += qc
        total_anchor += af
        total_zone += zb
        total_answer += ans
        for k, v in rej.items():
            total_rejected[k] += v
        total_cross += cross

        print(f"\n── {name} ({stats['layout_type']}) ──")
        print(f"  questions:            {qc}")
        print(f"  anchor_found:         {af}")
        print(f"  zone_built:           {zb}")
        print(f"  answer_bbox_found:    {ans}")
        print(f"  coverage:             {ans}/{qc} = {ans/qc*100:.0f}%" if qc > 0 else "  coverage: N/A")
        if rej:
            print(f"  rejected:")
            for reason, count in sorted(rej.items(), key=lambda x: -x[1]):
                print(f"    {reason}: {count}")
        if cross > 0:
            print(f"  ⚠ cross_cell_suspected: {cross}")

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(f"  fixtures_ok:          {fixtures_ok}")
    print(f"  fixtures_needs_data:  {fixtures_needs_data}")
    print(f"  total_questions:      {total_q}")
    print(f"  total_answer_found:   {total_answer}")
    if total_q > 0:
        print(f"  overall_coverage:     {total_answer}/{total_q} = {total_answer/total_q*100:.0f}%")
    if total_rejected:
        print(f"  rejected_by_reason:")
        for reason, count in sorted(total_rejected.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count}")
    print(f"  cross_cell_suspected: {total_cross}")

    # Verdict
    print(f"\n{'=' * 70}")
    if fixtures_ok == 0:
        print("VERDICT: NO_DATA — need real fixtures")
    elif total_cross > 0:
        print("VERDICT: No-Go — cross-cell borrowing detected")
    elif fixtures_needs_data > 0 and fixtures_ok > 0:
        print(f"VERDICT: Go with follow-up — {fixtures_ok} fixtures ok, {fixtures_needs_data} need real data")
    else:
        print("VERDICT: Go — all fixtures pass")

    print(f"{'=' * 70}")


# ── Main ──

if __name__ == "__main__":
    all_stats = [evaluate_fixture(f) for f in FIXTURES]
    print_report(all_stats)
