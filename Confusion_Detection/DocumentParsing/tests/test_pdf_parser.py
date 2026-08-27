"""Regression tests for the boilerplate-artifact fix (DocumentParsing/claude.md).

Run with `pytest -s` to see the printed title/bullet tables — this is
the numeric-not-just-pass/fail standard used across every stage of this
project: a future reader should be able to see what got classified,
not just "PASSED".
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import DocParsing_1 as dp
from tests.fixtures.make_fixture import BANNER_TEXT, PAGES, build_fixture

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "sample_deck.pdf"


@pytest.fixture(scope="module")
def deck():
    if not FIXTURE_PDF.exists():
        build_fixture(FIXTURE_PDF)
    return dp.extract_deck(str(FIXTURE_PDF))


def test_repeated_banner_not_treated_as_title(deck):
    for slide in deck.slides:
        print(f"slide {slide.slide_id}: title={slide.title!r}")
        assert slide.title != BANNER_TEXT, (
            f"slide {slide.slide_id}'s title is the repeated banner text "
            f"— boilerplate stripping isn't running before title detection"
        )


def test_page_number_footer_not_leaked_into_bullets(deck):
    footer_pattern = re.compile(r"^page \d+ of \d+$", re.IGNORECASE)
    for slide in deck.slides:
        for bullet in slide.bullets:
            assert not footer_pattern.match(bullet), (
                f"slide {slide.slide_id} has a page-number footer leaked "
                f"into its bullets: {bullet!r}"
            )


def test_real_titles_recovered_after_boilerplate_removed(deck):
    expected_titles = [p["title"] for p in PAGES]
    actual_titles = [s.title for s in deck.slides]
    for i, (expected, actual) in enumerate(zip(expected_titles, actual_titles)):
        print(f"slide {i}: expected={expected!r} actual={actual!r}")
    assert actual_titles == expected_titles


def test_legitimate_infrequent_repetition_is_kept(deck):
    # "Mocking replaces real dependencies..." appears on slides 0 and 5
    # only — 2 of 8 pages, below the boilerplate threshold
    # (max(3, 0.3*8)=3) — so it must survive as real bullet content on
    # both, not get silently stripped as if it were structural noise.
    repeated_bullet = "Mocking replaces real dependencies with test doubles."
    slides_with_bullet = [s.slide_id for s in deck.slides if repeated_bullet in s.bullets]
    print(f"slides containing the legitimately-repeated bullet: {slides_with_bullet}")
    assert slides_with_bullet == [0, 5]


def test_blank_page_gets_no_title_after_banner_and_footer_stripped(deck):
    # Page 4 (slide_id 4) has only a banner and footer, no real content —
    # pre-fix this slide would have gotten the banner as a fake title
    # (see the module docstring's before/after reproduction). Post-fix
    # it must correctly resolve to a genuinely blank slide.
    blank_slide = next(s for s in deck.slides if s.slide_id == 4)
    print(f"slide 4: title={blank_slide.title!r} bullets={blank_slide.bullets}")
    assert blank_slide.title is None
    assert blank_slide.bullets == []


def test_boilerplate_detection_requires_both_fraction_and_absolute_floor():
    # A line on 2 of 8 pages is 25% -- below the 30% fraction AND below
    # the absolute floor of 3 -- must not be flagged.
    all_page_lines = [
        [{"text": "recurring line", "size": 10, "top": 0}] if i in (0, 1) else []
        for i in range(8)
    ]
    keys = dp._detect_boilerplate_line_keys(all_page_lines, num_pages=8)
    assert "recurring line" not in keys

    # The same line on 3 of 8 pages clears both the fraction (37.5%) and
    # the absolute floor (3) -- must be flagged.
    all_page_lines = [
        [{"text": "recurring line", "size": 10, "top": 0}] if i in (0, 1, 2) else []
        for i in range(8)
    ]
    keys = dp._detect_boilerplate_line_keys(all_page_lines, num_pages=8)
    assert "recurring line" in keys


def test_digit_normalization_treats_page_numbers_as_the_same_key():
    assert dp._normalize_for_boilerplate("Page 3 of 45") == dp._normalize_for_boilerplate("Page 4 of 45")
    assert dp._normalize_for_boilerplate("Page 3 of 45") == "page # of #"
