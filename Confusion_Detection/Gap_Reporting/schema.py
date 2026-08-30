
"""ReportEntry, GapReport — the final output contract of the whole
Gap Detection pipeline.

If a Step 6 (auto-revised deck) ever gets built, GapReport is its
input — this folder's job stops at producing the report, deliberately
not editing the deck itself (see claude.md Section 1).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ReportEntry:
    slide_ids: List[int]
    slide_range_label: str  # e.g. "Slides 11-13", from DeckDocument's slide_number -- never the raw 0-indexed slide_id
    module_id: Optional[int]  # None when module grouping is unavailable (currently: always, until Step 1's grouper exists)
    topic_id: int
    gap_type: str
    severity: float
    guidance: str
    suggested_addition: Optional[str]
    backed_by_questions: int
    confidence: float
    is_noise: bool


@dataclass
class GapReport:
    generated_at: str
    total_topics_considered: int
    total_gaps_reported: int  # excludes "covered" -- those aren't gaps
    module_grouping_available: bool
    entries: List[ReportEntry]  # sorted by severity, descending
