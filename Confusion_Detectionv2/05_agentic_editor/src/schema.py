"""schema.py — the editor's contracts.

A SlideEdit is the only thing the applier acts on. The planner proposes
them, the checker and the fact-check accept or reject them, and nothing
free-form from an LLM ever reaches the deck: an edit is a typed
operation with typed fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

INSERT_SLIDE_AFTER = "insert_slide_after"


@dataclass
class SlideEdit:
    topic_id: int
    after_slide_id: int  # the new slide goes directly after this one
    title: str
    bullets: List[str]
    addresses_questions: List[str]  # the student questions this answers, copied from the mapping
    operation: str = INSERT_SLIDE_AFTER
    gap_type: Optional[str] = None  # carried from the report, for the change log
    report_confidence: Optional[float] = None


@dataclass
class CheckedEdit:
    edit: SlideEdit
    problems: List[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not self.problems


@dataclass
class AppliedEdit:
    edit: SlideEdit
    new_slide_positions: List[int]  # 0-indexed positions in the OUTPUT deck, one per copy inserted
    mirrored_after_slide_ids: List[int]  # anchors beyond after_slide_id that got the same slide (verbatim duplicates)
