"""Deterministic boundary tests for ContextWindowStrategy — no model,
no embeddings, just slide_id arithmetic against deck bounds."""
from __future__ import annotations

import pytest

from .. import _paths  # noqa: F401
from ..window_strategy import FixedRadiusWindow, ModuleAwareWindow

from gap_detection.parsing.schema import DeckDocument, Slide


def _make_deck(n_slides: int, blank_ids: set = frozenset()) -> DeckDocument:
    slides = [
        Slide(
            slide_id=i,
            slide_number=i + 1,
            title=None if i in blank_ids else f"Slide {i} title",
            bullets=[] if i in blank_ids else [f"bullet for slide {i}"],
        )
        for i in range(n_slides)
    ]
    return DeckDocument(source_pdf="boundary_test_deck.pdf", num_pages=n_slides, slides=slides)


DECK = _make_deck(10)  # slide_ids 0..9


def test_anchor_in_middle_gives_symmetric_window():
    window = FixedRadiusWindow(radius=1).build_window(5, DECK)
    assert window == [4, 5, 6]


def test_anchor_at_start_does_not_go_negative():
    window = FixedRadiusWindow(radius=1).build_window(0, DECK)
    assert window == [0, 1]
    assert min(window) >= 0


def test_anchor_at_end_does_not_exceed_deck_max():
    window = FixedRadiusWindow(radius=1).build_window(9, DECK)
    assert window == [8, 9]
    assert max(window) <= 9


def test_larger_radius_is_still_clamped_at_both_ends():
    window = FixedRadiusWindow(radius=5).build_window(0, DECK)
    assert window == list(range(0, 6))  # clamped to [0, 5], not [-5, 5]

    window = FixedRadiusWindow(radius=5).build_window(9, DECK)
    assert window == list(range(4, 10))  # clamped to [4, 9], not [4, 14]


def test_radius_zero_is_just_the_anchor():
    window = FixedRadiusWindow(radius=0).build_window(5, DECK)
    assert window == [5]


def test_negative_radius_rejected():
    with pytest.raises(ValueError):
        FixedRadiusWindow(radius=-1)


def test_blank_slide_inside_window_is_still_included_positionally():
    # Design decision (claude.md Section 4): window_slide_ids reflects
    # the deck's real physical neighborhood regardless of content —
    # whether a blank slide's *text* is skipped is builder.py's concern
    # (see test_builder.py), not the strategy's.
    deck_with_blank = _make_deck(10, blank_ids={5})
    window = FixedRadiusWindow(radius=1).build_window(5, deck_with_blank)
    assert window == [4, 5, 6]


def test_module_aware_window_raises_clear_not_implemented_error():
    with pytest.raises(NotImplementedError, match="module_id"):
        ModuleAwareWindow().build_window(5, DECK)
