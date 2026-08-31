"""structural.py — the minor, bounded structural boost signal.

Per claude.md Section 3: a title-only slide, a locally-max title font
size, or a near-duplicate slide are all weak evidence of a boundary,
never independent decision-makers. Implemented as a single flat,
capped boost applied once if ANY qualifying condition holds at a
boundary -- not stacked/summed across multiple simultaneous triggers,
which would risk the total boost growing past "clearly secondary."
Converting "qualifies or not" into an actual score (the boost's
magnitude) is segmenter.py's job, since only it knows this deck's own
depth-score range to size the cap against.
"""
from __future__ import annotations

from typing import List, Set

from ._upstream import Slide


def _is_locally_max_font(slides: List["Slide"], idx: int) -> bool:
    """True if slides[idx]'s title_font_size is strictly bigger than
    its immediate neighbors' (only neighbors that themselves have a
    title are considered -- an image-only neighbor with no title
    shouldn't count as "smaller", it's simply not a comparison point)."""
    if slides[idx].title_font_size is None:
        return False
    size = slides[idx].title_font_size
    neighbor_sizes = []
    if idx > 0 and slides[idx - 1].title_font_size is not None:
        neighbor_sizes.append(slides[idx - 1].title_font_size)
    if idx < len(slides) - 1 and slides[idx + 1].title_font_size is not None:
        neighbor_sizes.append(slides[idx + 1].title_font_size)
    if not neighbor_sizes:
        return False
    return size > max(neighbor_sizes)


def is_divider_slide(slide: "Slide") -> bool:
    """Title present, zero bullets -- exactly claude.md Section 3's
    "a slide with a title but zero bullets" structural cue. Kept
    strict on purpose: broadening this would also broaden which
    boundaries qualify for the structural boost, which Section 3
    deliberately scopes narrowly. See `is_divider_like` below for the
    looser check segmenter.py's merge-bias logic uses instead -- a
    different question (does this slide carry any real topical
    content at all) with a different, deliberately separate answer."""
    return slide.title is not None and not slide.bullets


# A real divider slide found on the actual test deck ("THANK YOU") has
# 3 short "bullets" -- presenter name, department, email -- not zero.
# For merge-bias purposes (is this slide's content trustworthy enough
# to compare by embedding similarity), zero-bullets-only misses this
# case entirely. Total combined bullet length stays well under this
# even for a 3-line contact block (~90 chars observed); real topical
# content runs to hundreds of chars.
DIVIDER_LIKE_MAX_BULLET_CHARS = 120


def is_divider_like(slide: "Slide") -> bool:
    """Looser than `is_divider_slide`: a title with little to no real
    bullet content, e.g. a bare sign-off with a short presenter/
    contact-info block attached. Deliberately NOT used for the
    structural boost (see `is_divider_slide`'s docstring) -- only for
    segmenter.py's merge-bias logic, where the question is narrower:
    does this slide have enough real content for embedding similarity
    to be a trustworthy signal at all."""
    if slide.title is None:
        return False
    total_bullet_chars = sum(len(b) for b in slide.bullets)
    return total_bullet_chars <= DIVIDER_LIKE_MAX_BULLET_CHARS


def compute_structural_boost_mask(slides: List["Slide"], duplicate_slide_indices: Set[int]) -> List[bool]:
    """One bool per candidate boundary i (between slide i and slide
    i+1), i in [0, len(slides)-2] -- True if this boundary qualifies
    for the bounded boost.

    Checks the slide that would *start* the prospective new topic
    (slide i+1): a new topic conventionally opens with an intro/divider
    slide, which is exactly what title-only-no-bullets and a locally-max
    title font tend to mark. The near-duplicate check looks at either
    flanking slide, since a repeating template slide can mark either the
    end of one occurrence or the start of the next.
    """
    n = len(slides)
    mask = []
    for i in range(n - 1):
        next_idx = i + 1
        qualifies = (
            is_divider_slide(slides[next_idx])
            or _is_locally_max_font(slides, next_idx)
            or i in duplicate_slide_indices
            or next_idx in duplicate_slide_indices
        )
        mask.append(qualifies)
    return mask
