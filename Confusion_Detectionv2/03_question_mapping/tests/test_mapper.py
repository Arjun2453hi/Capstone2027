"""QuestionMapper tests: threshold logic, multi-topic matches, unmatched
handling, and LLM-fallback wiring -- all deterministic (a fake
embedding model with hand-specified vectors, a fake resolver). The real
Groq integration is checked separately in test_llm_fallback.py, marked
slow."""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..src._upstream import Deck, Slide, Topic
from ..src.llm_fallback import AmbiguityResolver
from ..src.mapper import QuestionMapper


class _FakeEmbeddingModel:
    """Returns pre-supplied fixed vectors keyed by exact text, so tests
    can construct exact, predictable cosine scores."""

    name = "fake"

    def __init__(self, vectors_by_text):
        self._vectors_by_text = vectors_by_text

    def fit(self, corpus):
        pass

    def embed(self, texts):
        return np.array([self._vectors_by_text[t] for t in texts], dtype=float)

    def embed_query(self, texts):
        return self.embed(texts)


class _FakeResolver(AmbiguityResolver):
    def __init__(self, answer: Optional[int]):
        self.answer = answer
        self.calls = []

    def resolve(self, question, candidates):
        self.calls.append((question, candidates))
        return self.answer


def _make_two_topic_deck():
    slides = [
        Slide(slide_id=0, slide_number=1, title="A", title_font_size=18.0, bullets=["Topic A content here."]),
        Slide(slide_id=1, slide_number=2, title="B", title_font_size=18.0, bullets=["Topic B content here."]),
    ]
    deck = Deck(source_pdf="fake.pdf", num_pages=2, slides=slides)
    topics = [
        Topic(topic_id=0, start_slide_id=0, end_slide_id=0, slide_ids=[0], boundary_confidence=0.0),
        Topic(topic_id=1, start_slide_id=1, end_slide_id=1, slide_ids=[1], boundary_confidence=0.9),
    ]
    return deck, topics


def test_high_confidence_match_needs_no_llm_call():
    deck, topics = _make_two_topic_deck()
    text_a, text_b = deck.get(0).raw_text, deck.get(1).raw_text
    question = "clearly about A"

    model = _FakeEmbeddingModel({text_a: [1.0, 0.0], text_b: [0.0, 1.0], question: [0.95, 0.05]})
    resolver = _FakeResolver(answer=1)  # would prove the bug if this ever got called

    mapper = QuestionMapper(model, resolver=resolver, high_threshold=0.5, low_threshold=0.3)
    result, diagnostics = mapper.map_questions([question], topics, deck)

    assert diagnostics["n_llm_calls"] == 0
    assert resolver.calls == []
    topic0 = next(t for t in result.topics if t.topic_id == 0)
    assert len(topic0.matched_questions) == 1
    assert topic0.matched_questions[0].method == "semantic"
    assert result.unmatched_questions == []


def test_question_can_match_multiple_topics_above_high_threshold():
    deck, topics = _make_two_topic_deck()
    text_a, text_b = deck.get(0).raw_text, deck.get(1).raw_text
    question = "relevant to both"

    model = _FakeEmbeddingModel({text_a: [1.0, 0.0], text_b: [0.0, 1.0], question: [0.8, 0.8]})
    mapper = QuestionMapper(model, resolver=None, high_threshold=0.5, low_threshold=0.3)
    result, diagnostics = mapper.map_questions([question], topics, deck)

    matched_topic_ids = {t.topic_id for t in result.topics if t.matched_questions}
    assert matched_topic_ids == {0, 1}


def test_low_score_goes_to_unmatched_without_llm_call():
    deck, topics = _make_two_topic_deck()
    text_a, text_b = deck.get(0).raw_text, deck.get(1).raw_text
    question = "unrelated to everything"

    model = _FakeEmbeddingModel({text_a: [1.0, 0.0], text_b: [0.0, 1.0], question: [-1.0, -1.0]})
    resolver = _FakeResolver(answer=0)
    mapper = QuestionMapper(model, resolver=resolver, high_threshold=0.5, low_threshold=0.3)
    result, diagnostics = mapper.map_questions([question], topics, deck)

    assert diagnostics["n_llm_calls"] == 0
    assert len(result.unmatched_questions) == 1
    assert result.unmatched_questions[0].question == question


def test_ambiguous_score_calls_resolver_and_uses_its_answer():
    deck, topics = _make_two_topic_deck()
    text_a, text_b = deck.get(0).raw_text, deck.get(1).raw_text
    question = "somewhat related to A"

    # 3D so a "noise" component can dilute both cosines toward the
    # ambiguous zone without accidentally spiking topic_b too --
    # cosine(question, A) ~= 0.41 (ambiguous, in [0.3, 0.5)),
    # cosine(question, B) ~= 0.05 (comfortably low, doesn't interfere).
    model = _FakeEmbeddingModel(
        {text_a: [1.0, 0.0, 0.0], text_b: [0.0, 1.0, 0.0], question: [0.40, 0.05, 0.90]}
    )
    resolver = _FakeResolver(answer=0)
    mapper = QuestionMapper(model, resolver=resolver, high_threshold=0.5, low_threshold=0.3)
    result, diagnostics = mapper.map_questions([question], topics, deck)

    assert diagnostics["n_llm_calls"] == 1
    assert diagnostics["n_llm_resolved"] == 1
    topic0 = next(t for t in result.topics if t.topic_id == 0)
    assert len(topic0.matched_questions) == 1
    assert topic0.matched_questions[0].method == "llm_fallback"
    assert result.unmatched_questions == []


def test_ambiguous_score_with_no_resolver_configured_goes_unmatched():
    deck, topics = _make_two_topic_deck()
    text_a, text_b = deck.get(0).raw_text, deck.get(1).raw_text
    question = "somewhat related to A"

    # 3D so a "noise" component can dilute both cosines toward the
    # ambiguous zone without accidentally spiking topic_b too --
    # cosine(question, A) ~= 0.41 (ambiguous, in [0.3, 0.5)),
    # cosine(question, B) ~= 0.05 (comfortably low, doesn't interfere).
    model = _FakeEmbeddingModel(
        {text_a: [1.0, 0.0, 0.0], text_b: [0.0, 1.0, 0.0], question: [0.40, 0.05, 0.90]}
    )
    mapper = QuestionMapper(model, resolver=None, high_threshold=0.5, low_threshold=0.3)
    result, diagnostics = mapper.map_questions([question], topics, deck)

    assert diagnostics["n_llm_calls"] == 0
    assert len(result.unmatched_questions) == 1


def test_resolver_returning_none_goes_unmatched():
    deck, topics = _make_two_topic_deck()
    text_a, text_b = deck.get(0).raw_text, deck.get(1).raw_text
    question = "somewhat related to A"

    # 3D so a "noise" component can dilute both cosines toward the
    # ambiguous zone without accidentally spiking topic_b too --
    # cosine(question, A) ~= 0.41 (ambiguous, in [0.3, 0.5)),
    # cosine(question, B) ~= 0.05 (comfortably low, doesn't interfere).
    model = _FakeEmbeddingModel(
        {text_a: [1.0, 0.0, 0.0], text_b: [0.0, 1.0, 0.0], question: [0.40, 0.05, 0.90]}
    )
    resolver = _FakeResolver(answer=None)
    mapper = QuestionMapper(model, resolver=resolver, high_threshold=0.5, low_threshold=0.3)
    result, diagnostics = mapper.map_questions([question], topics, deck)

    assert diagnostics["n_llm_calls"] == 1
    assert diagnostics["n_llm_resolved"] == 0
    assert len(result.unmatched_questions) == 1


def test_llm_candidate_shortlist_is_bounded_and_sorted_by_score():
    slides = [
        Slide(slide_id=i, slide_number=i + 1, title=f"T{i}", title_font_size=18.0, bullets=[f"content {i}"])
        for i in range(4)
    ]
    deck = Deck(source_pdf="fake.pdf", num_pages=4, slides=slides)
    topics = [
        Topic(topic_id=i, start_slide_id=i, end_slide_id=i, slide_ids=[i], boundary_confidence=0.0) for i in range(4)
    ]
    texts = [deck.get(i).raw_text for i in range(4)]
    question = "ambiguous question"

    # Orthogonal per-topic basis vectors; the query's relative component
    # per axis directly controls relative cosine ranking after
    # normalization (topic 2 highest, then 0, then 3, then 1), all
    # comfortably under high_threshold=0.9 so this lands in the
    # ambiguous zone.
    vectors = {
        texts[0]: [1, 0, 0, 0],
        texts[1]: [0, 1, 0, 0],
        texts[2]: [0, 0, 1, 0],
        texts[3]: [0, 0, 0, 1],
        question: [0.30, 0.02, 0.35, 0.20],
    }
    model = _FakeEmbeddingModel(vectors)
    resolver = _FakeResolver(answer=None)
    mapper = QuestionMapper(model, resolver=resolver, high_threshold=0.9, low_threshold=0.05, llm_candidate_count=2)
    result, diagnostics = mapper.map_questions([question], topics, deck)

    assert diagnostics["n_llm_calls"] == 1
    called_question, called_candidates = resolver.calls[0]
    candidate_ids = [c["topic_id"] for c in called_candidates]
    print(f"candidates shown to resolver: {candidate_ids}")
    assert len(candidate_ids) == 2
    assert candidate_ids == [2, 0]  # highest score first: topic2 > topic0 > topic3 > topic1
