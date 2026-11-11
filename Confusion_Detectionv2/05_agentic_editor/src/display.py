"""display.py -- how topics and reports are shown to a person.

Two things the raw pipeline data gets wrong for a reader:

* Slide references. Stage 4 works with slide_id (0-based). Reports
  written before reports cited slide numbers say "slides 16-21" where a
  reader of the deck sees slides 17-22. Those references are shifted to
  slide numbers here; reports that already cite numbers
  (slide_refs == "number") are shown as written.
* Topic names. The first titled slide of a topic is often the previous
  lecture's closing slide ("THANK YOU") or a recap ("Summary ..."); the
  first real heading names it.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

_NUM_RANGE = r"\d+(?:\s*[-‐‑‒–]\s*\d+)?"
_SLIDE_REF = re.compile(
    r"(\b(?:[Ss]lides?|[Ss]lide (?:range|set))\s*\(?\s*)"           # "slide", "slides", "slide range (" ...
    rf"({_NUM_RANGE}(?:\s*(?:,|and|&)\s*{_NUM_RANGE})*)"            # "16", "16-21", "34, 36 and 54-56"
)


def _shift(match: re.Match) -> str:
    return match.group(1) + re.sub(r"\d+", lambda n: str(int(n.group()) + 1), match.group(2))


def report_text_for_display(report_text: str, slide_refs: Optional[str]) -> str:
    """Slide references as a reader numbers slides (1-based)."""
    if slide_refs == "number" or not report_text:
        return report_text
    return _SLIDE_REF.sub(_shift, report_text)


_GENERIC = re.compile(r"^(thank(s| you)|questions?|q\s*&\s*a|references?|summary\b.*|agenda|outline|contents?|"
                      r"recap\b.*|introduction|overview|definitions?|advantages|disadvantages|examples?)\W*$",
                      re.IGNORECASE)
# A generic heading the parser fused with the slide's first sentence
# ("Advantages Faster Time to Market - checkins ...").
_GENERIC_LEAD = re.compile(r"^(advantages|disadvantages|definition|summary|examples?|recap)\s+\S.{20,}$", re.IGNORECASE)
_CONT = re.compile(r"\s*\((cont\.?|contd\.?|continued)\)\s*", re.IGNORECASE)


def clean_title(title: str) -> str:
    title = _CONT.sub(" ", title or "").strip()
    # A heading fused with its first sentence ("Definition ▪ DevOps is ...") keeps the heading only.
    title = re.split(r"\s+[▪•●➢]\s+", title)[0].strip()
    return title


def topic_title(deck, slide_ids: Iterable[int], fallback: str = "Untitled topic") -> str:
    titles = []
    for sid in slide_ids:
        slide = deck.get(sid)
        t = clean_title(slide.title) if slide is not None and slide.title else ""
        if t and len(t) >= 3 and not _GENERIC.match(t) and not _GENERIC_LEAD.match(t):
            titles.append(t)
    # The first real heading: the section's own opening slide. (Tried the most
    # frequent heading: it picked repeated sub-headings over the section name.)
    return titles[0] if titles else fallback
