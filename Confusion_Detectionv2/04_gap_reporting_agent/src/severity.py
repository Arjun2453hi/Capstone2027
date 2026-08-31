"""severity.py — deterministic, non-agentic index/manifest scoring
(claude.md Section 10). The one deliberately non-LLM piece of this
whole stage: the actual per-topic content is fully agent-generated, but
ranking that content should not itself be subject to model variance
between runs.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import List

GAP_TYPE_WEIGHT = {
    "complete_omission": 3.0,
    "fragmented_context": 2.0,
    "shallow_coverage": 1.0,
    "covered": 0.0,
}


def severity(gap_type: str, backed_by_questions: int, confidence: float) -> float:
    """severity(report) = GAP_TYPE_WEIGHT[gap_type] * log1p(backed_by_questions) * confidence
    -- plain arithmetic, not an LLM opinion, so the ordering is
    reproducible and auditable (claude.md Section 10)."""
    weight = GAP_TYPE_WEIGHT.get(gap_type, 0.0)
    return weight * math.log1p(backed_by_questions) * confidence


def build_index(entries: List[dict]) -> List[dict]:
    """`entries` -- dicts with at least topic_id, gap_type, confidence,
    backed_by_questions, filename. Returns a new list of the same
    entries plus "severity", sorted worst-first."""
    scored = [{**e, "severity": severity(e["gap_type"], e["backed_by_questions"], e["confidence"])} for e in entries]
    scored.sort(key=lambda e: e["severity"], reverse=True)
    return scored


def save_index_json(index: List[dict], path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
