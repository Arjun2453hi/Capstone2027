"""grounding.py — the deck text an LLM sees when writing or checking a slide.

The first real planning run (topics 6, 12, 20) saw only a 140-char
excerpt per slide, and 3 of its 4 new slides then got something wrong
that the deck itself states correctly: one had the VMM run the emulated
kernel routine where the deck has it vector control to the guest OS,
one overstated PUSHF, one invented 95% / 5% thresholds. A model cannot
stay consistent with text it was never shown, so both the planner and
the fact-check get the slides' full text, within a budget that keeps a
request under Groq's free-tier 8000 tokens/minute (the largest topic is
~16,000 chars; ~4 chars per token).
"""
from __future__ import annotations

from typing import Iterable, Optional

MAX_CONTEXT_CHARS = 10_000
MAX_SLIDE_CHARS = 700
MIN_SLIDE_CHARS = 200


def _clean(text: str) -> str:
    return " ".join(text.split())


def slide_context(deck, slide_ids: Iterable[int], focus_slide_id: Optional[int] = None, max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """One line per slide, in deck order: `[slide <id>] <text>`.

    Without a focus every slide gets an equal share of the budget. With
    one, slides nearest the focus are given in full first and slides the
    budget does not reach are listed by title only -- what sits right
    around a new slide matters most when checking it."""
    slides = [deck.get(i) for i in slide_ids]
    slides = [s for s in slides if s is not None]
    if not slides:
        return ""

    allowance = {}
    if focus_slide_id is None:
        share = max(MIN_SLIDE_CHARS, min(MAX_SLIDE_CHARS, max_chars // len(slides)))
        allowance = {s.slide_id: share for s in slides}
    else:
        remaining = max_chars
        for s in sorted(slides, key=lambda s: (abs(s.slide_id - focus_slide_id), s.slide_id)):
            take = min(MAX_SLIDE_CHARS, len(_clean(s.raw_text)), remaining)
            allowance[s.slide_id] = take
            remaining -= take

    lines = []
    for s in slides:
        text = _clean(s.raw_text)
        if not text:
            lines.append(f"[slide {s.slide_id}] (image only)")
        elif allowance[s.slide_id] >= MIN_SLIDE_CHARS or allowance[s.slide_id] >= len(text):
            cut = text[: allowance[s.slide_id]]
            lines.append(f"[slide {s.slide_id}] {cut}{' ...' if len(cut) < len(text) else ''}")
        else:
            lines.append(f"[slide {s.slide_id}] {s.title or '(untitled)'} (text not shown)")
    return "\n".join(lines)
