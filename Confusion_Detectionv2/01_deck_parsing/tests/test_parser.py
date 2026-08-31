"""Regression tests against the synthetic fixture (banner-bigger-than-
title bug, footer leakage, image-only slide, legitimate repetition,
long ack paragraph). Run with `pytest -s` to see the printed tables."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from ..src.parser import extract_deck
from .fixtures.make_fixture import ACK_PARAGRAPH, BANNER_TEXT, PAGES, REPEATED_BULLET, build_fixture

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "sample_deck.pdf"


@pytest.fixture(scope="module")
def deck():
    if not FIXTURE_PDF.exists():
        build_fixture(FIXTURE_PDF)
    return extract_deck(str(FIXTURE_PDF))


def test_repeated_banner_not_treated_as_title(deck):
    for slide in deck.slides:
        print(f"slide {slide.slide_id}: title={slide.title!r}")
        assert slide.title != BANNER_TEXT
        assert slide.title != BANNER_TEXT.title()


def test_footer_not_leaked_into_bullets(deck):
    footer_pattern = re.compile(r"page \d+ of \d+", re.IGNORECASE)
    for slide in deck.slides:
        for bullet in slide.bullets:
            assert not footer_pattern.search(bullet), f"slide {slide.slide_id} leaked footer: {bullet!r}"


def test_real_titles_recovered_after_boilerplate_removed(deck):
    expected_titles = [p["title"] for p in PAGES]
    actual_titles = [s.title for s in deck.slides]
    for i, (expected, actual) in enumerate(zip(expected_titles, actual_titles)):
        print(f"slide {i}: expected={expected!r} actual={actual!r}")
    assert actual_titles == expected_titles


def test_title_font_size_populated_for_real_titles_and_none_for_blank_slide(deck):
    for slide in deck.slides:
        if slide.title is not None:
            assert slide.title_font_size is not None
            assert slide.title_font_size > 0
    blank_slide = next(s for s in deck.slides if s.slide_id == 4)
    assert blank_slide.title_font_size is None


def test_legitimately_repeated_bullet_kept_on_both_slides(deck):
    slides_with_bullet = [s.slide_id for s in deck.slides if REPEATED_BULLET in s.bullets]
    print(f"slides containing the legitimately-repeated bullet: {slides_with_bullet}")
    assert slides_with_bullet == [3, 7]


def test_image_only_slide_correctly_flagged_empty(deck):
    blank_slide = next(s for s in deck.slides if s.slide_id == 4)
    assert blank_slide.is_empty()
    assert blank_slide.title is None
    assert blank_slide.bullets == []
    # And no other slide is spuriously flagged empty.
    empty_ids = [s.slide_id for s in deck.slides if s.is_empty()]
    assert empty_ids == [4]


def test_long_acknowledgements_paragraph_not_treated_as_boilerplate(deck):
    # It appears exactly once in the deck (1 page out of 8, well below
    # the boilerplate threshold) and is long-form prose, not a short
    # repeated banner line -- it must show up as real bullet content,
    # not get stripped, and must not have swallowed the real title.
    slide0 = next(s for s in deck.slides if s.slide_id == 0)
    assert slide0.title == "Course Introduction"
    combined_bullets = " ".join(slide0.bullets)
    # The paragraph got word-wrapped across multiple bullet lines by the
    # fixture generator; check enough of its distinctive content survived.
    assert "acknowledgements" in combined_bullets.lower()
    assert "prescribed textbooks" in combined_bullets.lower() or "prescribed" in combined_bullets.lower()
