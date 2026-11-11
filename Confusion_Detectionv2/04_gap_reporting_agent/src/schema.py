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

from pydantic import BaseModel, Field, model_validator

# off_topic: the question is about something this deck does not set out
# to teach (e.g. CSS in a cloud-computing deck). It is not a gap in the
# deck. Only the unmatched-questions investigation (topic_id -1) may use
# it: a topic's questions were matched to that topic's own slides.
GapType = Literal["complete_omission", "shallow_coverage", "fragmented_context", "covered", "off_topic"]
UNMATCHED_TOPIC_ID = -1


class QuestionVerdict(BaseModel):
    """One question's own verdict. A topic's questions differ: some are
    answered by its slides, some are not. One verdict per topic labelled
    them all alike (measured on a synthetic deck: 16 of 26 answered
    questions were called "thin" because their topic was), and for the
    unmatched group it called CSS and Java questions a "complete
    omission" of a cloud-computing deck. Every report now carries one
    verdict per question; the topic's gap_type is derived from them."""

    question: str
    verdict: GapType


class GapReport(BaseModel):
    topic_id: int
    assigned_slide_ids: List[int] = Field(default_factory=list)
    discovered_slide_ids: List[int] = Field(default_factory=list)
    gap_type: GapType
    confidence: float = Field(ge=0.0, le=1.0)
    report_text: str
    question_verdicts: List[QuestionVerdict] = Field(default_factory=list)
    # How report_text refers to slides: "number" (1-based, as a reader sees
    # them) in reports written since this field exists; "id" (0-based
    # slide_id) in older ones, which the editor converts for display.
    slide_refs: Literal["id", "number"] = "id"

    @model_validator(mode="after")
    def _off_topic_only_for_unmatched(self):
        # Reports written before question_verdicts existed still load. A
        # single QUESTION may be off-topic in any topic (stage 3 can map a
        # stray question to its nearest topic); a whole real topic cannot.
        if self.topic_id != UNMATCHED_TOPIC_ID and self.gap_type == "off_topic":
            raise ValueError("off_topic as a topic's gap_type is only for the unmatched-questions "
                             "investigation (topic_id -1); mark single questions off_topic in question_verdicts")
        return self


def group_gap_type(verdicts: List[QuestionVerdict], topic_id: int = UNMATCHED_TOPIC_ID) -> str:
    """A report's overall gap_type, derived from its per-question
    verdicts by plain code: the most severe verdict among in-scope
    questions. If every question is off-topic: off_topic for the
    unmatched group, covered for a real topic (nothing to fix there)."""
    from .severity import GAP_TYPE_WEIGHT

    in_scope = [v.verdict for v in verdicts if v.verdict != "off_topic"]
    if not in_scope:
        return "off_topic" if topic_id == UNMATCHED_TOPIC_ID else "covered"
    return max(in_scope, key=lambda g: GAP_TYPE_WEIGHT.get(g, 0.0))
