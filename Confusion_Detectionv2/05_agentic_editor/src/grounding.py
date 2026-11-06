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

import re
from typing import Iterable, Optional

MAX_CONTEXT_CHARS = 6000
MAX_SLIDE_CHARS = 900
MIN_SLIDE_CHARS = 200


def _clean(text: str) -> str:
    return " ".join(text.split())


def slide_context(deck, slide_ids: Iterable[int], focus_slide_id: Optional[int] = None,
                  max_chars: int = MAX_CONTEXT_CHARS, query: str = "") -> str:
    """Extractive working memory with a hard serialized-character budget.

    Prefer relevant slides or anchor neighbours. Collapse exact duplicates,
    preserve source IDs, and rebuild for each task instead of growing a chat.
    """
    if max_chars <= 0:
        return ""
    slides = [deck.get(i) for i in dict.fromkeys(slide_ids)]
    slides = [s for s in slides if s is not None]
    terms = set(re.findall(r"[a-z0-9_]{3,}", query.lower()))
    texts = {s.slide_id: _clean(s.raw_text) for s in slides}

    def priority(slide):
        relevance = len(terms & set(re.findall(r"[a-z0-9_]{3,}", texts[slide.slide_id].lower())))
        return ((abs(slide.slide_id - focus_slide_id), -relevance, slide.slide_id) if focus_slide_id is not None
                else (-relevance, slide.slide_id))

    selected, identical, remaining, expandable = {}, {}, max_chars, []
    ranked = sorted(slides, key=priority)
    # Reserve source labels first: absence of visible text must be explicit,
    # rather than mistaken for evidence that the deck lacks an explanation.
    for slide in ranked:
        text = texts[slide.slide_id]
        label = f"[slide {slide.slide_id}] "
        if text and text in identical:
            excerpt = f"(same text as slide {identical[text]})"
        elif not text:
            excerpt = "(image only)"
        else:
            excerpt = f"{_clean(slide.title or '(untitled)')[:65]} (text not shown)"
        line = label + excerpt
        if len(line) + 1 > remaining:
            continue
        selected[slide.slide_id] = line
        remaining -= len(line) + 1
        if text and text not in identical:
            identical[text] = slide.slide_id
            expandable.append(slide)
    share = remaining // (len(expandable) or 1)
    for slide in expandable:
        original = selected[slide.slide_id]
        label = f"[slide {slide.slide_id}] "
        extra = remaining if focus_slide_id is not None or terms else min(remaining, share)
        if extra <= 0:
            continue
        allowance = min(MAX_SLIDE_CHARS, extra + len(original) - len(label))
        text = texts[slide.slide_id]
        excerpt = text if len(text) <= allowance else text[:max(0, allowance - 4)] + " ..."
        line = label + excerpt
        if len(line) <= len(original) + remaining:
            selected[slide.slide_id] = line
            remaining -= len(line) - len(original)
    output = "\n".join(selected[s.slide_id] for s in slides if s.slide_id in selected)
    omitted = len(slides) - len(selected)
    marker = f"\n[{omitted} slides omitted by context budget]" if omitted else ""
    if marker and len(output) + len(marker) <= max_chars:
        output += marker
    return output[:max_chars]
