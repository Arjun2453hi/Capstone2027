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


def test_question_tied_between_topics_matches_both():
    """The real case: a deck that repeats a slide verbatim in two
    topics. Both copies are equally the question's home."""
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


def test_question_goes_only_to_its_best_topic_even_if_others_clear_the_threshold():
    """Regression for the measured failure of the old design: every
    topic scoring above the threshold got the question, so one real
    question landed in 10 topics. Clearing the threshold is necessary,
    not sufficient -- only the best topic (and exact ties) gets it."""
    deck, topics = _make_two_topic_deck()
    text_a, text_b = deck.get(0).raw_text, deck.get(1).raw_text
    question = "mostly about A"

    # cosine(question, A) ~= 0.78, cosine(question, B) ~= 0.62: both
    # above high_threshold=0.5, but not tied.
    model = _FakeEmbeddingModel({text_a: [1.0, 0.0], text_b: [0.0, 1.0], question: [1.0, 0.8]})
    mapper = QuestionMapper(model, resolver=None, high_threshold=0.5, low_threshold=0.3)
    result, _ = mapper.map_questions([question], topics, deck)

    matched_topic_ids = {t.topic_id for t in result.topics if t.matched_questions}
    assert matched_topic_ids == {0}


def test_question_matches_a_mixed_topic_through_its_one_relevant_slide():
    """A topic that blends two sub-concepts: the question is about only
    one of them. Scored per slide it matches that topic through the
    relevant slide; a single mean-pooled topic vector (the old design,
    kept here only as the comparison baseline) scores a cleaner but
    wrong topic higher."""
    slides = [
        Slide(slide_id=0, slide_number=1, title="Orchestration", title_font_size=18.0, bullets=["general orchestration"]),
        Slide(slide_id=1, slide_number=2, title="Pods", title_font_size=18.0, bullets=["kubernetes internals"]),
        Slide(slide_id=2, slide_number=3, title="DevOps", title_font_size=18.0, bullets=["devops culture"]),
    ]
    deck = Deck(source_pdf="fake.pdf", num_pages=3, slides=slides)
    topics = [
        Topic(topic_id=0, start_slide_id=0, end_slide_id=1, slide_ids=[0, 1], boundary_confidence=0.0),
        Topic(topic_id=1, start_slide_id=2, end_slide_id=2, slide_ids=[2], boundary_confidence=0.9),
    ]
    question = "what is a pod?"
    vectors = {
        deck.get(0).raw_text: [1.0, 0.0, 0.0],
        deck.get(1).raw_text: [0.0, 1.0, 0.0],
        deck.get(2).raw_text: [0.0, 0.8, 0.6],
        question: [0.0, 1.0, 0.1],
    }
    model = _FakeEmbeddingModel(vectors)
    mapper = QuestionMapper(model, resolver=None, high_threshold=0.5, low_threshold=0.3)
    result, diagnostics = mapper.map_questions([question], topics, deck)

    q = np.array(vectors[question]) / np.linalg.norm(vectors[question])
    pooled_topic0 = (np.array(vectors[deck.get(0).raw_text]) + np.array(vectors[deck.get(1).raw_text])) / 2
    pooled_score = float(q @ (pooled_topic0 / np.linalg.norm(pooled_topic0)))
    slide_level_score = float(diagnostics["scores"][0, 0])
    wrong_topic_score = float(diagnostics["scores"][0, 1])
    print(f"topic 0: slide-level {slide_level_score:.3f} vs mean-pooled {pooled_score:.3f}; topic 1: {wrong_topic_score:.3f}")
    assert pooled_score < wrong_topic_score < slide_level_score

    topic0 = next(t for t in result.topics if t.topic_id == 0)
    topic1 = next(t for t in result.topics if t.topic_id == 1)
    assert [m.best_slide_id for m in topic0.matched_questions] == [1]
    assert topic1.matched_questions == []


def test_adaptive_thresholds_flag_only_the_question_far_below_the_rest():
    """No fixed thresholds: the cutoff comes from this run's own best
    scores. Five questions sit close to their topic; one is far from
    everything and must be the only one left unmatched."""
    deck, topics = _make_two_topic_deck()
    text_a, text_b = deck.get(0).raw_text, deck.get(1).raw_text
    vectors = {text_a: [1.0, 0.0, 0.0], text_b: [0.0, 1.0, 0.0]}
    on_topic = [f"question {i}" for i in range(5)]
    for i, q in enumerate(on_topic):
        vectors[q] = [1.0, 0.0, 0.30 + 0.02 * i]  # cosine to A between ~0.94 and ~0.96
    off_topic = "unrelated to the whole deck"
    vectors[off_topic] = [0.3, 0.2, 1.0]  # best cosine ~0.28

    mapper = QuestionMapper(_FakeEmbeddingModel(vectors), resolver=None)
    result, _ = mapper.map_questions(on_topic + [off_topic], topics, deck)

    print(f"adaptive thresholds: high={result.high_threshold:.3f} low={result.low_threshold:.3f}")
    assert [u.question for u in result.unmatched_questions] == [off_topic]
    topic0 = next(t for t in result.topics if t.topic_id == 0)
    assert [m.question for m in topic0.matched_questions] == on_topic


def test_resolver_choosing_a_lower_scoring_candidate_assigns_only_that_topic():
    """The resolver may pick a candidate that isn't the top scorer; the
    higher-scoring topics it passed over must not come along with it."""
    deck, topics = _make_two_topic_deck()
    text_a, text_b = deck.get(0).raw_text, deck.get(1).raw_text
    question = "ambiguous, slightly closer to A"

    model = _FakeEmbeddingModel(
        {text_a: [1.0, 0.0, 0.0], text_b: [0.0, 1.0, 0.0], question: [0.45, 0.35, 0.90]}
    )
    resolver = _FakeResolver(answer=1)
    mapper = QuestionMapper(model, resolver=resolver, high_threshold=0.5, low_threshold=0.3)
    result, _ = mapper.map_questions([question], topics, deck)

    matched_topic_ids = {t.topic_id for t in result.topics if t.matched_questions}
    assert matched_topic_ids == {1}
