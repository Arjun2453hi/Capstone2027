"""schema.py — Slide / Deck, the permanent output contract of this stage.

Every later stage in this project reads decks through this schema, not
through pdfplumber directly — get it right here since changing it later
means re-touching every downstream stage (claude.md Section 1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Slide:
    slide_id: int  # 0-indexed, permanent identity, assigned once at parse time, never re-derived
    slide_number: int  # 1-indexed, human-readable
    title: Optional[str]
    # REQUIRED even though nothing in this module reads it back --
    # 02_topic_segmentation's structural signal needs it (a locally-max
    # title_font_size is a weak boundary cue). None when the slide has
    # no title line at all (e.g. an image-only slide).
    title_font_size: Optional[float]
    bullets: List[str] = field(default_factory=list)
    raw_text: str = ""
    char_count: int = 0

    def __post_init__(self):
        if not self.raw_text:
            parts = ([self.title] if self.title else []) + list(self.bullets)
            self.raw_text = "\n".join(parts)
        if not self.char_count:
            self.char_count = len(self.raw_text)

    def is_empty(self) -> bool:
        """True for a genuinely content-free slide (most commonly:
        image-only, no text layer) -- distinct from a parsing failure.
        Downstream stages must be able to tell the two apart, so this is
        a real, named check rather than callers eyeballing
        `title is None and not bullets` themselves."""
        return self.title is None and not self.bullets


@dataclass
class Deck:
    source_pdf: str
    num_pages: int
    slides: List[Slide]

    def get(self, slide_id: int) -> Optional[Slide]:
        """Look up a slide by its permanent id, never by list position."""
        for s in self.slides:
            if s.slide_id == slide_id:
                return s
        return None
