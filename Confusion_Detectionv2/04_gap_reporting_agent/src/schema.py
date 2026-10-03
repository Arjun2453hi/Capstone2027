"""schema.py — GapReport, the terminal output of one topic's agentic
investigation (claude.md Section 6's write_report tool schema).

topic_id == -1 is the synthetic "unmatched questions" investigation
(claude.md Section 9) -- a real GapReport like any other, not a
second-class side file.

assigned_slide_ids / discovered_slide_ids (remediation, replacing the
old single slide_ids_examined field): the model is no longer trusted
to self-report what it "examined" -- write_report's own tool schema no
longer even accepts a slide-ids argument. assigned_slide_ids is filled
in by agent.py from this topic's own known slide range (ctx, not the
model); discovered_slide_ids is filled in by agent.py from the ACTUAL
slide_ids returned by real search_expanding_context/search_similar_slides
tool calls made during this investigation, tracked as they happen --
never from the model's own claims. This closes a real gap: a model
could previously claim it "examined" slides it never actually looked
at, since slide_ids_examined was just another argument it filled in
itself.
"""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

GapType = Literal["complete_omission", "shallow_coverage", "fragmented_context", "covered"]


class GapReport(BaseModel):
    topic_id: int
    assigned_slide_ids: List[int] = Field(default_factory=list)
    discovered_slide_ids: List[int] = Field(default_factory=list)
    gap_type: GapType
    confidence: float = Field(ge=0.0, le=1.0)
    report_text: str
