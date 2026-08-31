"""search_expanding_context's own radius-growth logic, against
constructed embedding sequences -- deterministic, no real model. Covers
all three outcomes: found, hit_topic_switch, and gave_up_at_max_radius.
"""
from __future__ import annotations

from ..src._upstream import Deck, Slide
from ..src.tools import build_tools
from .fixtures.mock_groq_responses import make_fake_context


def _make_uniform_deck(n: int, title_prefix: str = "s") -> Deck:
    slides = [
        Slide(slide_id=i, slide_number=i + 1, title=f"{title_prefix}{i}", title_font_size=18.0, bullets=[f"c{i}"])
        for i in range(n)
    ]
    return Deck(source_pdf="fake.pdf", num_pages=n, slides=slides)


def test_found_when_relevance_clears_the_found_threshold_before_any_switch():
    # Uniform content everywhere -- zero real topic variation, so the
    # switch signal never fires. The query is set to match that content
    # closely, so "found" should trigger on the very first radius.
    deck = _make_uniform_deck(20)
    texts = [s.raw_text for s in deck.slides]
    vectors_by_text = {t: [1.0, 0.0, 0.0] for t in texts}
    vectors_by_text["target content"] = [1.0, 0.0, 0.0]  # identical direction -- cosine 1.0

    ctx = make_fake_context(deck=deck, vectors_by_text=vectors_by_text, found_threshold=0.5, switch_threshold=0.5)
    tools = {t.name: t for t in build_tools(ctx)}

    result = tools["search_expanding_context"].invoke(
        {"anchor_slide_id": 10, "what_am_i_looking_for": "target content"}
    )

    assert result["status"] == "found"
    assert 10 in result["window_slide_ids"]


def test_hit_topic_switch_when_expansion_crosses_a_real_content_boundary():
    # A sharp topic shift between slide 9 (block A) and slide 10 (block
    # B), anchored right at the seam -- a real, strong dip, easily
    # clearing a low switch_threshold. what_am_i_looking_for is
    # deliberately orthogonal to both blocks so "found" never fires
    # first.
    n = 20
    slides = [
        Slide(slide_id=i, slide_number=i + 1, title=f"s{i}", title_font_size=18.0, bullets=[f"c{i}"])
        for i in range(n)
    ]
    deck = Deck(source_pdf="fake.pdf", num_pages=n, slides=slides)
    texts = [s.raw_text for s in deck.slides]
    vectors_by_text = {t: ([1.0, 0.0, 0.0] if i < 10 else [0.0, 1.0, 0.0]) for i, t in enumerate(texts)}
    vectors_by_text["something unrelated to either block"] = [0.0, 0.0, 1.0]

    ctx = make_fake_context(deck=deck, vectors_by_text=vectors_by_text, found_threshold=0.9, switch_threshold=0.5)
    tools = {t.name: t for t in build_tools(ctx)}

    result = tools["search_expanding_context"].invoke(
        {"anchor_slide_id": 9, "what_am_i_looking_for": "something unrelated to either block"}
    )

    assert result["status"] == "hit_topic_switch"


def test_gives_up_at_max_radius_when_neither_condition_is_ever_met():
    # Uniform content (no real switch signal anywhere) and a query
    # orthogonal to it (relevance never clears found_threshold either)
    # -- neither condition can ever trigger.
    deck = _make_uniform_deck(20)
    texts = [s.raw_text for s in deck.slides]
    vectors_by_text = {t: [1.0, 0.0, 0.0] for t in texts}
    vectors_by_text["completely unrelated query"] = [0.0, 1.0, 0.0]

    ctx = make_fake_context(deck=deck, vectors_by_text=vectors_by_text, found_threshold=0.9, switch_threshold=0.9)
    tools = {t.name: t for t in build_tools(ctx)}

    result = tools["search_expanding_context"].invoke(
        {"anchor_slide_id": 10, "what_am_i_looking_for": "completely unrelated query"}
    )

    assert result["status"] == "gave_up_at_max_radius"


def test_unknown_anchor_slide_id_gives_up_immediately():
    deck = _make_uniform_deck(5)
    ctx = make_fake_context(deck=deck)
    tools = {t.name: t for t in build_tools(ctx)}

    result = tools["search_expanding_context"].invoke({"anchor_slide_id": 999, "what_am_i_looking_for": "anything"})

    assert result["status"] == "gave_up_at_max_radius"
    assert result["window_slide_ids"] == []
