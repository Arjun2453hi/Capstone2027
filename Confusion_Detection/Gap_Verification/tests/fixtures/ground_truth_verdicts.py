"""Hand-labeled ground truth, pulled from real ContextBundles in
Context_Window/context_windows_report.json — not a synthetic fixture.

Each entry references a real topic_id; the actual query/window_text is
loaded from the real report at test time (see load_ground_truth_bundles
below), so this file stays in sync with whatever the pipeline actually
produced rather than freezing a hardcoded snapshot that could drift.

Labels were assigned by reading each bundle's real window_text by eye
(see the "note" field for the reasoning) — this is exactly the kind of
judgment call claude.md Section 5 says isn't fully automatable.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

CONTEXT_WINDOWS_REPORT = (
    Path(__file__).resolve().parents[3] / "Context_Window" / "context_windows_report.json"
)

GROUND_TRUTH = [
    {
        "topic_id": 22,
        "expected_gap_type": "covered",
        "note": (
            "COCOMO, 44 backing questions. Window has the Basic COCOMO "
            "formula, all 3 project-type constants, and worked examples "
            "-- directly and clearly answers the query."
        ),
    },
    {
        "topic_id": 6,
        "expected_gap_type": "shallow_coverage",
        "note": (
            "Microservices, 40 backing questions. Window explains "
            "*reasons to break up a monolith* but never actually "
            "defines microservices or contrasts it with monolithic "
            "architecture -- the query's actual ask. Related, but thin."
        ),
    },
    {
        "topic_id": 8,
        "expected_gap_type": "shallow_coverage",
        "note": (
            "Therac-25 fail-safe design, 30 backing questions. Window "
            "describes what went wrong in detail but never states what "
            "a *proper fail-safe architecture* would have looked like "
            "-- the fix is implied, not stated."
        ),
    },
    {
        "topic_id": 33,
        "expected_gap_type": "complete_omission",
        "note": (
            "Risk management + project triangle, 33 backing questions. "
            "Window explains risk management and risk classification "
            "generally, but never once mentions the triangle, scope, "
            "time, or cost -- the specific connection asked about is "
            "absent, even though the general topic is present."
        ),
    },
    {
        "topic_id": 46,
        "expected_gap_type": "complete_omission",
        "note": (
            "Waterfall/Agile 'road trip analogy', 1 backing question "
            "(singleton). Window is just two disconnected slide titles "
            "('Agile and Architecture', 'The Zipper Model') -- no "
            "analogy, no Waterfall/Agile comparison at all."
        ),
    },
    {
        "topic_id": 9,
        "expected_gap_type": "fragmented_context",
        "note": (
            "Technical debt, 51 backing questions -- the deliberately "
            "hard case (see claude.md Section 3). Window is just the "
            "title 'Technical Debt' repeated twice; the real "
            "explanation ('A better analogy: Pollution') sits on the "
            "very next slide, one outside the radius-1 window. Correct "
            "behavior is fragmented_context (or at least low "
            "confidence), NOT a confident complete_omission -- a "
            "confident complete_omission here would mean the design "
            "implication in Section 3 didn't get implemented."
        ),
    },
]


def load_ground_truth_bundles() -> List[dict]:
    """Returns GROUND_TRUTH entries merged with each topic's real query
    and window_text, read live from context_windows_report.json."""
    data = json.loads(CONTEXT_WINDOWS_REPORT.read_text(encoding="utf-8"))
    bundles_by_id = {b["topic_id"]: b for b in data["bundles"]}

    merged = []
    for entry in GROUND_TRUTH:
        bundle = bundles_by_id[entry["topic_id"]]
        merged.append({**entry, "bundle": bundle})
    return merged
