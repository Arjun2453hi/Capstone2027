"""topic_text() tests -- specifically the regression for a real bug
found in practice: a long early slide (e.g. an acknowledgements
paragraph) that alone pushes the running total past max_chars used to
`break` and silently drop every remaining slide in the topic, no matter
how short. Confirmed against the real deck: topic 0's window_text was
740 chars of pure boilerplate (slide 0 only), while slides 1-11 held
the topic's actual substantive content and never made it in at all.
"""
from __future__ import annotations

from ..src._upstream import Deck, Slide, Topic
from ..src.mapper import topic_text


def _make_slide(slide_id, text_len, title="t"):
    # bullets sized to produce raw_text of exactly text_len characters
    # is fiddly given title/formatting overhead -- use a single bullet
    # whose own length we control and keep assertions on relative
    # ordering/inclusion, not exact character counts.
    return Slide(slide_id=slide_id, slide_number=slide_id + 1, title=title, title_font_size=18.0, bullets=["x" * text_len])


def test_a_long_early_slide_does_not_block_later_shorter_slides_from_being_included():
    slides = [
        _make_slide(0, 740),  # long, like a real acknowledgements slide
        _make_slide(1, 795),  # also long -- together with slide 0, exceeds a 1500-char budget
        _make_slide(2, 30),  # short -- must still be included, this is the regression check
    ]
    deck = Deck(source_pdf="fake.pdf", num_pages=3, slides=slides)
    topic = Topic(topic_id=0, start_slide_id=0, end_slide_id=2, slide_ids=[0, 1, 2], boundary_confidence=0.0)

    text = topic_text(topic, deck, max_chars=1500)

    assert deck.get(0).raw_text in text  # first slide always included
    assert deck.get(1).raw_text not in text  # too long to fit alongside slide 0 -- correctly skipped
    assert deck.get(2).raw_text in text  # short slide 2 must NOT be dropped just because slide 1 didn't fit


def test_budget_is_still_respected_overall():
    slides = [_make_slide(i, 200) for i in range(20)]
    deck = Deck(source_pdf="fake.pdf", num_pages=20, slides=slides)
    topic = Topic(topic_id=0, start_slide_id=0, end_slide_id=19, slide_ids=list(range(20)), boundary_confidence=0.0)

    text = topic_text(topic, deck, max_chars=1000)

    assert len(text) <= 1000 + 50  # small slack for the "\n\n" join separators


def test_empty_or_missing_slides_are_skipped_without_affecting_the_budget():
    slides = [
        Slide(slide_id=0, slide_number=1, title=None, title_font_size=18.0, bullets=[]),  # blank/image-only
        _make_slide(1, 50),
    ]
    deck = Deck(source_pdf="fake.pdf", num_pages=2, slides=slides)
    topic = Topic(topic_id=0, start_slide_id=0, end_slide_id=1, slide_ids=[0, 1], boundary_confidence=0.0)

    text = topic_text(topic, deck, max_chars=1500)

    assert deck.get(1).raw_text in text
