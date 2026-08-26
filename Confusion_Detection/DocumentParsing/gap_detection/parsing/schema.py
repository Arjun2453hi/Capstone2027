"""Slide / DeckDocument dataclasses — the permanent identity contract.

Why `slide_id` exists at all: a deck gets re-parsed (font-size heuristics
tweaked, a slide added), and its slides get re-ordered or re-numbered
along the way. Every downstream stage (similarity, clustering, gap
reporting) needs a stable handle to "the same slide" across those
re-parses, so `slide_id` is assigned once at parse time and never
re-derived from list position. `slide_number` is the human-facing
1-indexed page number and is allowed to drift; `slide_id` is not.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class Slide:
    slide_id: int
    slide_number: int
    title: Optional[str]
    bullets: List[str] = field(default_factory=list)
    notes: Optional[str] = None
    raw_text: str = ""
    char_count: int = 0
    module_id: Optional[int] = None

    def __post_init__(self):
        # raw_text/char_count are derived, but we accept them as given
        # (e.g. when loading from JSON) rather than recomputing, so a
        # hand-edited deck round-trips exactly.
        if not self.raw_text:
            parts = ([self.title] if self.title else []) + list(self.bullets)
            self.raw_text = "\n".join(parts)
        if not self.char_count:
            self.char_count = len(self.raw_text)

    @property
    def is_empty(self) -> bool:
        """No title and no bullets == nothing to embed.

        Similiarity_gen skips these when building a SlideIndex: embedding
        an empty string is noise that can spuriously rank high against
        short questions (see Similiarity_gen/claude.md, Section 3).
        """
        return not self.title and not self.bullets


@dataclass
class DeckDocument:
    source_pdf: str
    num_pages: int
    slides: List[Slide]
    extracted_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "source_pdf": self.source_pdf,
            "extracted_at": self.extracted_at,
            "num_pages": self.num_pages,
            "slides": [asdict(s) for s in self.slides],
        }

    def get(self, slide_id: int) -> Optional[Slide]:
        """Look up a slide by its permanent id (not list position)."""
        for s in self.slides:
            if s.slide_id == slide_id:
                return s
        return None
