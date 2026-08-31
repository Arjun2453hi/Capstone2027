"""Unit tests for the bounded structural boost mask."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..src.structural import compute_structural_boost_mask


@dataclass
class _FakeSlide:
    title: Optional[str] = "Slide"
    bullets: Optional[List[str]] = None
    title_font_size: Optional[float] = 18.0

    def __post_init__(self):
        if self.bullets is None:
            self.bullets = ["some content"]


def test_title_only_slide_qualifies_the_boundary_before_it():
    slides = [
        _FakeSlide(),
        _FakeSlide(bullets=[]),  # title-only divider
        _FakeSlide(),
    ]
    mask = compute_structural_boost_mask(slides, duplicate_slide_indices=set())
    assert mask[0] is True  # boundary 0->1: slide 1 is title-only
    assert mask[1] is False  # boundary 1->2: slide 2 is a plain slide


def test_locally_max_title_font_qualifies():
    slides = [
        _FakeSlide(title_font_size=14.0),
        _FakeSlide(title_font_size=24.0),  # locally-max
        _FakeSlide(title_font_size=14.0),
    ]
    mask = compute_structural_boost_mask(slides, duplicate_slide_indices=set())
    assert mask[0] is True  # boundary 0->1: slide 1's font is a local max
    assert mask[1] is False  # boundary 1->2: slide 2's font (14) is not > slide 1's (24)


def test_near_duplicate_index_qualifies_both_adjacent_boundaries():
    slides = [_FakeSlide() for _ in range(4)]
    mask = compute_structural_boost_mask(slides, duplicate_slide_indices={2})
    assert mask[0] is False  # boundary 0->1: neither slide is the duplicate
    assert mask[1] is True  # boundary 1->2: slide 2 is the duplicate
    assert mask[2] is True  # boundary 2->3: slide 2 is the duplicate


def test_plain_slides_do_not_qualify():
    slides = [_FakeSlide(), _FakeSlide()]
    mask = compute_structural_boost_mask(slides, duplicate_slide_indices=set())
    assert mask == [False]


def test_image_only_neighbor_does_not_count_as_smaller_for_font_comparison():
    # A neighbor with no title at all (title_font_size=None) must not
    # be treated as "smaller" -- it's simply not a valid comparison
    # point, per structural.py's _is_locally_max_font docstring.
    slides = [
        _FakeSlide(title=None, bullets=[], title_font_size=None),  # image-only
        _FakeSlide(title_font_size=18.0),
        _FakeSlide(title=None, bullets=[], title_font_size=None),  # image-only
    ]
    mask = compute_structural_boost_mask(slides, duplicate_slide_indices=set())
    # slide 1 has no other real neighbor to compare against -- title-only
    # check: slide 1 HAS bullets (default fixture), so title-only doesn't
    # trigger; local-max font check has no valid neighbor sizes, so it
    # also doesn't trigger. Both boundaries should be False.
    assert mask == [False, False]
