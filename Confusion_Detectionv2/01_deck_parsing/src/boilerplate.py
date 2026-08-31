"""boilerplate.py — generic repeated/near-duplicate content detection.

Two related but distinct uses share one core primitive (normalize text,
then group or count by normalized content):

1. **Line-level** (`detect_boilerplate_line_keys`): a line repeating
   across MANY PAGES (a course banner, a page-number footer) --
   frequency-threshold based, counted by *distinct pages*, not raw
   occurrences.
2. **Slide-level** (`find_near_duplicate_groups`): a whole slide's
   content repeating elsewhere in the deck (e.g. this deck's
   128-188/189-249 duplicate range) -- any 2+ occurrence is itself the
   signal, no frequency threshold needed, since a whole slide matching
   another verbatim is inherently notable regardless of how often it
   happens.

`02_topic_segmentation` reuses `find_near_duplicate_groups` at the
whole-slide level for its structural boost signal -- written generically
enough here for that reuse, rather than being reimplemented there
(claude.md Section 5).
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Sequence, Set

_DIGIT_RE = re.compile(r"\d+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_repetition(text: str) -> str:
    """Digit-normalized (so 'Page 3 of 45' / 'Page 4 of 45' count as one
    repeated pattern), whitespace-collapsed, lowercased -- incidental
    spacing/case differences shouldn't break an otherwise-exact match."""
    text = _DIGIT_RE.sub("#", text.strip().lower())
    return _WHITESPACE_RE.sub(" ", text)


def detect_boilerplate_line_keys(
    all_page_lines: Sequence[Sequence[dict]],
    num_pages: int,
    min_fraction: float = 0.30,
    min_absolute: int = 3,
) -> Set[str]:
    """A line is boilerplate if its normalized form appears on at least
    max(min_absolute, min_fraction * num_pages) DISTINCT pages.

    Both a fraction and an absolute floor are required: fraction alone
    breaks on small decks (2 pages out of 5 looks like 40%, but that's
    not enough evidence of structural repetition); absolute floor alone
    breaks on huge decks (3 occurrences out of 500 pages is noise).

    Counting distinct pages (not raw line occurrences) matters too: a
    line repeated twice on one page but nowhere else must not be
    flagged -- that's topical repetition on a single slide, not the
    deck-wide structural repetition this targets.
    """
    page_counts: Dict[str, int] = {}
    for page_lines in all_page_lines:
        seen_this_page = {
            normalize_for_repetition(line["text"])
            for line in page_lines
            if line["text"].strip()
        }
        for key in seen_this_page:
            page_counts[key] = page_counts.get(key, 0) + 1

    threshold = max(min_absolute, int(min_fraction * num_pages))
    return {key for key, count in page_counts.items() if count >= threshold}


def find_near_duplicate_groups(texts: Sequence[str], min_chars: int = 20) -> Dict[str, List[int]]:
    """Groups indices whose normalized text is identical (2+ members).

    `min_chars` guards against short, coincidentally-identical snippets
    (e.g. two unrelated slides that both just say "Questions?") counting
    as a meaningful near-duplicate -- a real duplicate worth flagging is
    substantial content repeating, not a short incidental phrase.
    Singleton groups (nothing else matches) are dropped from the result.
    """
    groups: Dict[str, List[int]] = defaultdict(list)
    for i, text in enumerate(texts):
        normalized = normalize_for_repetition(text)
        if len(normalized) >= min_chars:
            groups[normalized].append(i)
    return {key: idxs for key, idxs in groups.items() if len(idxs) >= 2}
