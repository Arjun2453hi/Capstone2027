"""schema.py — the editor's contracts.

A SlideEdit is the only thing the applier acts on. The planners propose
them, the checker and the fact-check accept or reject them, and nothing
free-form from an LLM ever reaches the deck: an edit is one of a fixed
set of operations with typed fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# A new slide after `after_slide_id`, for content the deck is missing.
INSERT_SLIDE_AFTER = "insert_slide_after"
# Bullets added to the end of `after_slide_id`'s own body text, for one
# or two missing points that belong on a slide that has room for them.
APPEND_BULLETS = "append_bullets"
# A clean rebuild of `after_slide_id`, a slide whose layout is broken,
# made only from that slide's own text and pictures. The rebuild goes
# right after the original, and the original is hidden from the
# slideshow -- kept in the file, never deleted.
REPAIR_SLIDE = "repair_slide"
OPERATIONS = (INSERT_SLIDE_AFTER, APPEND_BULLETS, REPAIR_SLIDE)
REPAIR_TOPIC_ID = -2  # a repair answers no gap report


@dataclass
class SlideEdit:
    topic_id: int
    after_slide_id: int  # the anchor: the slide inserted after, added to, or repaired
    title: str  # for APPEND_BULLETS, the anchor slide's own title (not changed)
    bullets: List[str]
    addresses_questions: List[str]  # the student questions this answers, copied from the mapping
    operation: str = INSERT_SLIDE_AFTER
    gap_type: Optional[str] = None  # carried from the report, for the change log
    report_confidence: Optional[float] = None
    source_text: Optional[str] = None  # REPAIR_SLIDE only: the broken slide's own text, which the rebuild may not go beyond


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
    new_slide_positions: List[int]  # 0-indexed positions in the OUTPUT deck of the slides this edit added or changed
    mirrored_after_slide_ids: List[int]  # anchors beyond after_slide_id that got the same edit (verbatim duplicates)
    hidden_slide_positions: List[int] = field(default_factory=list)  # REPAIR_SLIDE: the original, now hidden from the show
    note: Optional[str] = None  # anything the applier did differently from the plan, and why
