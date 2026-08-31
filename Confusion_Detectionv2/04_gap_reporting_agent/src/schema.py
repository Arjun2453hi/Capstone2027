"""schema.py — GapReport, the terminal output of one topic's agentic
investigation (claude.md Section 6's write_report tool schema).

topic_id == -1 is the synthetic "unmatched questions" investigation
(claude.md Section 9) -- a real GapReport like any other, not a
second-class side file.
"""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

GapType = Literal["complete_omission", "shallow_coverage", "fragmented_context", "covered"]


class GapReport(BaseModel):
    topic_id: int
    slide_ids_examined: List[int] = Field(default_factory=list)
    gap_type: GapType
    confidence: float = Field(ge=0.0, le=1.0)
    report_text: str
