"""export.py tests: slide_range labeling and the final consolidated
dossier that gets handed to 04_gap_verification."""
from __future__ import annotations

from ..src._upstream import Deck, Slide, Topic
from ..src.export import _slide_range_label, build_gap_verification_input
from ..src.mapper import QuestionMapper


class _FakeEmbeddingModel:
    name = "fake"

    def __init__(self, vectors_by_text):
        self._vectors_by_text = vectors_by_text

    def fit(self, corpus):
        pass

    def embed(self, texts):
        return [self._vectors_by_text[t] for t in texts]

    def embed_query(self, texts):
        return self.embed(texts)


def _make_deck_and_topics():
    slides = [
        Slide(slide_id=0, slide_number=51, title="A", title_font_size=18.0, bullets=["Topic A content here."]),
        Slide(slide_id=1, slide_number=52, title="B", title_font_size=18.0, bullets=["Topic B content here."]),
    ]
    deck = Deck(source_pdf="fake.pdf", num_pages=2, slides=slides)
    topics = [
        Topic(topic_id=0, start_slide_id=0, end_slide_id=0, slide_ids=[0], boundary_confidence=0.42),
        Topic(topic_id=1, start_slide_id=1, end_slide_id=1, slide_ids=[1], boundary_confidence=0.9),
    ]
    return deck, topics


def test_slide_range_label_uses_human_readable_slide_numbers_not_slide_ids():
    deck, topics = _make_deck_and_topics()
    assert _slide_range_label([0], deck) == "Slide 51"
    assert _slide_range_label([0, 1], deck) == "Slides 51-52"


def test_slide_range_label_handles_empty_list():
    deck, _ = _make_deck_and_topics()
    assert _slide_range_label([], deck) == "(no slides)"


def test_build_gap_verification_input_carries_slide_range_and_content_and_matches():
    deck, topics = _make_deck_and_topics()
    text_a, text_b = deck.get(0).raw_text, deck.get(1).raw_text
    question = "clearly about A"

    model = _FakeEmbeddingModel({text_a: [1.0, 0.0], text_b: [0.0, 1.0], question: [0.95, 0.05]})
    mapper = QuestionMapper(model, resolver=None, high_threshold=0.5, low_threshold=0.3)
    result, _ = mapper.map_questions([question], topics, deck)

    gap_input = build_gap_verification_input(result, topics, deck)

    assert gap_input.total_topics == 2
    assert gap_input.source_deck == "fake.pdf"

    dossier_a = next(d for d in gap_input.topics if d.topic_id == 0)
    assert dossier_a.slide_ids == [0]
    assert dossier_a.slide_range == "Slide 51"
    assert dossier_a.boundary_confidence == 0.42
    assert dossier_a.window_text == text_a
    assert dossier_a.cluster_size == 1
    assert len(dossier_a.matched_questions) == 1
    assert dossier_a.matched_questions[0].question == question

    dossier_b = next(d for d in gap_input.topics if d.topic_id == 1)
    assert dossier_b.cluster_size == 0
    assert dossier_b.matched_questions == []

    assert gap_input.unmatched_questions == result.unmatched_questions
