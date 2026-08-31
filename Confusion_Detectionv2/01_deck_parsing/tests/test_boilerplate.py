"""Tests for the generic repetition-detection primitives (boilerplate.py)."""
from __future__ import annotations

from ..src.boilerplate import (
    detect_boilerplate_line_keys,
    find_near_duplicate_groups,
    normalize_for_repetition,
)


def test_digit_normalization_treats_page_numbers_as_the_same_key():
    assert normalize_for_repetition("Page 3 of 45") == normalize_for_repetition("Page 4 of 45")
    assert normalize_for_repetition("Page 3 of 45") == "page # of #"


def test_boilerplate_requires_both_fraction_and_absolute_floor():
    # 2 of 8 pages = 25%: below the 30% fraction AND below the absolute
    # floor of 3 -- must not be flagged.
    all_page_lines = [
        [{"text": "recurring line"}] if i in (0, 1) else [] for i in range(8)
    ]
    keys = detect_boilerplate_line_keys(all_page_lines, num_pages=8)
    assert "recurring line" not in keys

    # 3 of 8 pages clears both the fraction (37.5%) and the absolute
    # floor (3) -- must be flagged.
    all_page_lines = [
        [{"text": "recurring line"}] if i in (0, 1, 2) else [] for i in range(8)
    ]
    keys = detect_boilerplate_line_keys(all_page_lines, num_pages=8)
    assert "recurring line" in keys


def test_boilerplate_counts_distinct_pages_not_raw_occurrences():
    # Same line appears twice on ONE page, nowhere else -- must not be
    # flagged even though the raw occurrence count (2) might otherwise
    # look meaningful; only 1 distinct page is involved.
    all_page_lines = [
        [{"text": "dup line"}, {"text": "dup line"}],
        [], [], [], [], [], [], [],
    ]
    keys = detect_boilerplate_line_keys(all_page_lines, num_pages=8)
    assert "dup line" not in keys


def test_find_near_duplicate_groups_finds_the_group():
    texts = [
        "Introduction to the course and syllabus overview details here",
        "Some unrelated content about hypervisors and virtualization",
        "Introduction to the course and syllabus overview details here",  # exact dup of index 0
        "Yet another unrelated slide about containers and namespaces",
    ]
    groups = find_near_duplicate_groups(texts)
    assert len(groups) == 1
    (indices,) = groups.values()
    assert sorted(indices) == [0, 2]


def test_find_near_duplicate_groups_ignores_short_coincidental_matches():
    texts = ["Questions?", "Questions?", "A real unique slide with substantial content here"]
    groups = find_near_duplicate_groups(texts, min_chars=20)
    assert groups == {}  # both "Questions?" instances are below min_chars


def test_find_near_duplicate_groups_drops_singletons():
    texts = ["A perfectly unique slide with plenty of real content", "Another unique one with content"]
    groups = find_near_duplicate_groups(texts)
    assert groups == {}
