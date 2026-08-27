"""Budget-enforcement tests (deterministic, no model) + an integration
test against the real deck, real questions, and real Global_Context
topology. Run with `pytest -s` to see the printed numbers."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from .. import _paths  # noqa: F401
from ..builder import TRUNCATION_MARKER, ContextWindowBuilder
from ..window_strategy import FixedRadiusWindow

from gap_detection.parsing.schema import DeckDocument, Slide


def _safe_print(text: str) -> None:
    """Print without crashing on a Windows console's cp1252 encoding.

    Real deck text extracted from the PDF occasionally contains font-
    substitution artifacts (stray glyphs pdfplumber couldn't map to a
    real character) that cp1252 can't encode — not a bug in this
    package, just a reality of scraping text out of a real PDF. This
    only affects how the diagnostic table below prints, never the
    actual window_text data returned to callers.
    """
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))

SIMILIARITY_GEN_DIR = Path(__file__).resolve().parents[2] / "Similiarity_gen"


class _FakeMatch:
    def __init__(self, slide_id):
        self.slide_id = slide_id


class _FakeRetriever:
    """Returns a fixed anchor slide_id regardless of the query — lets
    the budget-enforcement tests be deterministic without a real
    embedding model."""

    def __init__(self, anchor_slide_id: int):
        self._anchor_slide_id = anchor_slide_id

    def retrieve(self, question, top_k=1):
        return [_FakeMatch(self._anchor_slide_id)]


class _FakeTopic:
    def __init__(self, topic_id, representative_query, source_questions):
        self.topic_id = topic_id
        self.representative_query = representative_query
        self.source_questions = source_questions

    @property
    def size(self):
        return len(self.source_questions)


def _long_deck(segment_len: int = 500) -> DeckDocument:
    """3 slides, each with a long bullet so their combined text
    comfortably exceeds a small budget."""
    slides = [
        Slide(
            slide_id=i,
            slide_number=i + 1,
            title=f"Slide {i}",
            bullets=[("word " * (segment_len // 5))],
        )
        for i in range(3)
    ]
    return DeckDocument(source_pdf="budget_test_deck.pdf", num_pages=3, slides=slides)


def test_budget_shrinks_window_text_deterministically():
    deck = _long_deck()
    retriever = _FakeRetriever(anchor_slide_id=1)  # middle slide -> window [0, 1, 2]
    topic = _FakeTopic(0, "irrelevant query", ["q1"])

    unbudgeted = ContextWindowBuilder(retriever, deck, FixedRadiusWindow(1), max_context_chars=10_000)
    generous_bundle = unbudgeted.build(topic)

    tight_budget = len(generous_bundle.window_text) // 2
    budgeted = ContextWindowBuilder(retriever, deck, FixedRadiusWindow(1), max_context_chars=tight_budget)
    tight_bundle = budgeted.build(topic)

    print(f"before: {len(generous_bundle.window_text)} chars, budget={tight_budget}, "
          f"after: {len(tight_bundle.window_text)} chars")

    assert len(tight_bundle.window_text) <= tight_budget
    assert len(tight_bundle.window_text) < len(generous_bundle.window_text)
    # Positional truth is preserved even though content was dropped —
    # all 3 slide_ids are still geometrically "in the window."
    assert tight_bundle.window_slide_ids == generous_bundle.window_slide_ids == [0, 1, 2]


def test_budget_prioritizes_anchor_over_distant_neighbors():
    deck = _long_deck()
    retriever = _FakeRetriever(anchor_slide_id=1)
    topic = _FakeTopic(0, "irrelevant query", ["q1"])

    anchor_segment_len = len(f"[Slide 2] {deck.get(1).raw_text}")
    # Budget fits the anchor's own segment but not a second one.
    budget = anchor_segment_len + 10
    bundle = ContextWindowBuilder(retriever, deck, FixedRadiusWindow(1), max_context_chars=budget).build(topic)

    print(f"anchor-only budget={budget}, window_text={bundle.window_text[:60]!r}...")
    assert "[Slide 2]" in bundle.window_text  # slide_number for slide_id=1
    assert "[Slide 1]" not in bundle.window_text
    assert "[Slide 3]" not in bundle.window_text
    assert bundle.window_slide_ids == [0, 1, 2]  # still the full geometric window


def test_extremely_tight_budget_hard_truncates_with_marker():
    deck = _long_deck()
    retriever = _FakeRetriever(anchor_slide_id=1)
    topic = _FakeTopic(0, "irrelevant query", ["q1"])

    tiny_budget = 50  # smaller than any single slide's segment
    bundle = ContextWindowBuilder(retriever, deck, FixedRadiusWindow(1), max_context_chars=tiny_budget).build(topic)

    print(f"tiny budget={tiny_budget}, result={bundle.window_text!r} (len={len(bundle.window_text)})")
    assert len(bundle.window_text) <= tiny_budget
    assert TRUNCATION_MARKER.strip() in bundle.window_text


def test_blank_slide_in_window_contributes_no_text():
    slides = [
        Slide(slide_id=0, slide_number=1, title="Intro", bullets=["some content"]),
        Slide(slide_id=1, slide_number=2, title=None, bullets=[]),  # blank
        Slide(slide_id=2, slide_number=3, title="Outro", bullets=["more content"]),
    ]
    deck = DeckDocument(source_pdf="blank_test_deck.pdf", num_pages=3, slides=slides)
    retriever = _FakeRetriever(anchor_slide_id=1)  # anchor is the blank slide itself
    topic = _FakeTopic(0, "irrelevant query", ["q1"])

    bundle = ContextWindowBuilder(retriever, deck, FixedRadiusWindow(1), max_context_chars=10_000).build(topic)

    assert bundle.window_slide_ids == [0, 1, 2]  # blank slide_id kept positionally
    assert "[Slide 2]" not in bundle.window_text  # but contributes no text
    assert "Intro" in bundle.window_text and "some content" in bundle.window_text
    assert "Outro" in bundle.window_text and "more content" in bundle.window_text


# ----------------------------------------------------------------------
# Integration test against the real deck + real questions + real topology
# ----------------------------------------------------------------------
@pytest.mark.slow
def test_integration_against_real_deck_and_topics():
    from Global_Context.clustering import HDBSCANClusterer
    from Global_Context.distillation import CentroidClosestDistiller
    from Global_Context.topology_builder import QuestionTopologyBuilder
    from Similiarity_gen.embedding_models import SentenceTransformerEmbeddingModel
    from Similiarity_gen.retriever import QuestionSlideRetriever
    from Similiarity_gen.slide_index import SlideIndex
    from gap_detection.parsing.storage import load_deck_json

    deck = load_deck_json(SIMILIARITY_GEN_DIR / "se-u2-slides.json")
    questions = [
        line.strip()
        for line in (SIMILIARITY_GEN_DIR / "u2_questions.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    model = SentenceTransformerEmbeddingModel()  # one instance, reused for slides and questions
    index = SlideIndex.build(deck, model)
    retriever = QuestionSlideRetriever(index)

    topology = QuestionTopologyBuilder(
        model, HDBSCANClusterer(min_cluster_size=3), CentroidClosestDistiller()
    ).build(questions)

    strategy = FixedRadiusWindow(radius=1)
    budget = 4000
    budgeted_builder = ContextWindowBuilder(retriever, deck, strategy, max_context_chars=budget)
    unbudgeted_builder = ContextWindowBuilder(retriever, deck, strategy, max_context_chars=10**9)

    bundles = budgeted_builder.build_all(topology.topics)
    full_bundles = unbudgeted_builder.build_all(topology.topics)  # cheap: same retrieval, no re-embedding

    n_slides_per_window = [len(b.window_slide_ids) for b in bundles]
    n_chars_per_window = [len(b.window_text) for b in bundles]
    n_truncated = sum(
        1 for b, fb in zip(bundles, full_bundles) if len(fb.window_text) > budget
    )

    print(f"\n{len(bundles)} context bundles built (max_context_chars={budget})")
    print(
        f"window size (slides): min={min(n_slides_per_window)}, "
        f"avg={sum(n_slides_per_window)/len(n_slides_per_window):.2f}, "
        f"max={max(n_slides_per_window)}"
    )
    print(
        f"window size (chars): min={min(n_chars_per_window)}, "
        f"avg={sum(n_chars_per_window)/len(n_chars_per_window):.2f}, "
        f"max={max(n_chars_per_window)}"
    )
    print(f"windows truncated to fit budget: {n_truncated}/{len(bundles)}")

    real_examples = [b for b in bundles if not next(t for t in topology.topics if t.topic_id == b.topic_id).is_noise]
    real_examples.sort(key=lambda b: -b.cluster_size)
    print("\n--- example ContextBundles ---")
    for b in real_examples[:4]:
        _safe_print(f"\ntopic_id={b.topic_id} cluster_size={b.cluster_size} anchor_slide_id={b.anchor_slide_id}")
        _safe_print(f"representative_query: {b.representative_query!r}")
        _safe_print(f"window_slide_ids: {b.window_slide_ids}")
        _safe_print(f"source_questions ({len(b.source_questions)}): {b.source_questions[:3]}")
        _safe_print(f"window_text ({len(b.window_text)} chars):\n{b.window_text}")

    assert len(bundles) == len(topology.topics)
    for b in bundles:
        assert len(b.window_text) <= budget
        assert b.anchor_slide_id in b.window_slide_ids
