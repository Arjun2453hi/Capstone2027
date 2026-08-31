"""TopicSegmenter orchestration tests: the cosine-dominant enforcement
test, minimum-segment-length merging, and a clean end-to-end sanity
check -- all deterministic, no real embedding model needed."""
from __future__ import annotations

import numpy as np

from ..src._upstream import Deck, Slide
from ..src.schema import Topic
from ..src.segmenter import TopicSegmenter


# Realistic length on purpose: a trivial one-char bullet like "x" is
# short enough to accidentally read as divider-like content (see
# structural.py's is_divider_like, threshold 120 chars) in tests that
# aren't actually testing divider behavior -- explicit short/empty
# bullets are still used deliberately where a test IS about dividers.
_REALISTIC_BULLET = (
    "This slide covers a real, substantial topic in meaningful explanatory "
    "depth, well past structural.py's is_divider_like threshold, so it is "
    "never mistaken for a bare divider or sign-off slide in these tests."
)


def _make_slide(slide_id, title, bullets=None, title_font_size=18.0):
    return Slide(
        slide_id=slide_id,
        slide_number=slide_id + 1,
        title=title,
        title_font_size=title_font_size,
        bullets=bullets if bullets is not None else [_REALISTIC_BULLET],
    )


class _FakeFlatEmbeddingModel:
    """Identical embedding for every text -- zero content signal
    anywhere, regardless of what the text says."""

    name = "fake-flat"

    def fit(self, corpus):
        pass

    def embed(self, texts):
        return np.tile([1.0, 0.0, 0.0, 0.0], (len(texts), 1))


class _FakeFixedEmbeddingModel:
    """Returns a pre-supplied embedding matrix regardless of the input
    text, for tests that need exact control over the content signal."""

    name = "fake-fixed"

    def __init__(self, vectors):
        self._vectors = np.array(vectors, dtype=float)

    def fit(self, corpus):
        pass

    def embed(self, texts):
        return self._vectors


def test_structural_boost_alone_cannot_create_a_boundary():
    # Every slide embeds identically (flat content signal) -- zero
    # variation anywhere -- but slide 5 is a title-only divider with a
    # locally-max font, the two structural cues Section 3 describes.
    # If a structural signal could act as an independent decision-maker,
    # this would produce a boundary. It must not: with zero content
    # variation, the deck's own depth-score range is 0, which forces the
    # bounded structural boost (a *fraction* of that range) to 0 too --
    # the boost is scaled to content variability, never independent of it.
    slides = [_make_slide(i, f"Slide {i}", ["content"]) for i in range(10)]
    slides[5] = _make_slide(5, "Divider", [], title_font_size=30.0)
    deck = Deck(source_pdf="fake.pdf", num_pages=10, slides=slides)

    segmenter = TopicSegmenter(_FakeFlatEmbeddingModel(), min_segment_length=1)
    topics, diagnostics = segmenter.segment(deck)

    print(f"topics found: {len(topics)}, threshold={diagnostics['threshold']}")
    assert len(topics) == 1  # the whole deck stayed one topic -- no spurious cut
    assert topics[0].slide_ids == list(range(10))


def test_merge_short_segments_merges_into_the_more_similar_neighbor():
    embeddings = np.array(
        [
            [1, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0],  # topic A block, slide_ids 0-3
            [0, 1, 0, 0],  # short 1-slide topic, slide_id 4 -- distinct from both neighbors
            [0, 0.3, 0.9, 0], [0, 0.3, 0.9, 0], [0, 0.3, 0.9, 0], [0, 0.3, 0.9, 0],  # topic C block, slide_ids 5-8
        ],
        dtype=float,
    )
    slides = [_make_slide(i, f"s{i}") for i in range(9)]

    topics = [
        Topic(topic_id=0, start_slide_id=0, end_slide_id=3, slide_ids=[0, 1, 2, 3], boundary_confidence=0.0),
        Topic(topic_id=1, start_slide_id=4, end_slide_id=4, slide_ids=[4], boundary_confidence=0.8),
        Topic(topic_id=2, start_slide_id=5, end_slide_id=8, slide_ids=[5, 6, 7, 8], boundary_confidence=0.9),
    ]

    segmenter = TopicSegmenter(model=None, min_segment_length=3)
    merged = segmenter._merge_short_segments(topics, embeddings, slides)

    print(f"merged topics: {[(t.start_slide_id, t.end_slide_id, t.slide_ids) for t in merged]}")
    assert len(merged) == 2
    assert all(len(t.slide_ids) >= 3 for t in merged)
    topic_with_4 = next(t for t in merged if 4 in t.slide_ids)
    assert 5 in topic_with_4.slide_ids  # merged toward the more-similar C block
    assert 3 not in topic_with_4.slide_ids  # not merged toward A


def test_divider_only_short_segment_merges_left_despite_higher_right_similarity():
    # Mirrors a real case found on the actual deck: a bare "THANK YOU"
    # divider slide measured LOWER cosine similarity to its left
    # neighbor than its right, which the old (pure-similarity) merge
    # logic used to merge right -- gluing a section-closing slide onto
    # the front of a completely unrelated following topic. A
    # divider-only short segment must now merge left regardless.
    embeddings = np.array(
        [
            [1, 0, 0], [1, 0, 0], [1, 0, 0],  # left topic (slide_ids 0-2)
            [0.3, 0.3, 0.3],  # "THANK YOU" -- deliberately closer to the right block numerically
            [0, 0.9, 0.1], [0, 0.9, 0.1], [0, 0.9, 0.1],  # right topic (slide_ids 4-6)
        ],
        dtype=float,
    )
    slides = [_make_slide(i, f"s{i}", ["x"]) for i in range(7)]
    slides[3] = _make_slide(3, "THANK YOU", [])  # divider: title, no bullets

    from ..src.similarity import cosine_similarity

    # Sanity-check the premise: the divider really is numerically closer
    # to the right block than the left one, so a plain-similarity merge
    # would have picked right.
    left_sim = cosine_similarity(embeddings[3], embeddings[0])
    right_sim = cosine_similarity(embeddings[3], embeddings[4])
    print(f"divider-to-left similarity={left_sim:.4f}, divider-to-right similarity={right_sim:.4f}")
    assert right_sim > left_sim

    topics = [
        Topic(topic_id=0, start_slide_id=0, end_slide_id=2, slide_ids=[0, 1, 2], boundary_confidence=0.0),
        Topic(topic_id=1, start_slide_id=3, end_slide_id=3, slide_ids=[3], boundary_confidence=0.9),
        Topic(topic_id=2, start_slide_id=4, end_slide_id=6, slide_ids=[4, 5, 6], boundary_confidence=0.8),
    ]
    segmenter = TopicSegmenter(model=None, min_segment_length=3)
    merged = segmenter._merge_short_segments(topics, embeddings, slides)

    print(f"merged: {[(t.start_slide_id, t.end_slide_id, t.slide_ids) for t in merged]}")
    assert len(merged) == 2
    topic_with_divider = next(t for t in merged if 3 in t.slide_ids)
    assert 2 in topic_with_divider.slide_ids  # merged left, with the preceding topic
    assert 4 not in topic_with_divider.slide_ids  # not merged right


def test_divider_only_segment_at_deck_start_merges_right_for_lack_of_a_left_neighbor():
    embeddings = np.array([[1, 0], [0, 1], [0, 1], [0, 1]], dtype=float)
    slides = [_make_slide(0, "THANK YOU", [])] + [_make_slide(i, f"s{i}", ["x"]) for i in range(1, 4)]
    topics = [
        Topic(topic_id=0, start_slide_id=0, end_slide_id=0, slide_ids=[0], boundary_confidence=0.0),
        Topic(topic_id=1, start_slide_id=1, end_slide_id=3, slide_ids=[1, 2, 3], boundary_confidence=0.9),
    ]
    segmenter = TopicSegmenter(model=None, min_segment_length=3)
    merged = segmenter._merge_short_segments(topics, embeddings, slides)
    assert len(merged) == 1
    assert merged[0].slide_ids == [0, 1, 2, 3]


def test_merge_preserves_topic_ids_are_sequential_afterward():
    embeddings = np.array([[1, 0], [1, 0], [0, 1], [0, 1], [0, 1]], dtype=float)
    slides = [_make_slide(i, f"s{i}", ["x"]) for i in range(5)]
    topics = [
        Topic(topic_id=0, start_slide_id=0, end_slide_id=0, slide_ids=[0], boundary_confidence=0.0),
        Topic(topic_id=1, start_slide_id=1, end_slide_id=1, slide_ids=[1], boundary_confidence=0.5),
        Topic(topic_id=2, start_slide_id=2, end_slide_id=4, slide_ids=[2, 3, 4], boundary_confidence=0.9),
    ]
    segmenter = TopicSegmenter(model=None, min_segment_length=3)
    merged = segmenter._merge_short_segments(topics, embeddings, slides)
    assert [t.topic_id for t in merged] == list(range(len(merged)))


def test_end_to_end_finds_a_clean_two_topic_boundary():
    a, b = [1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]
    vectors = [a] * 6 + [b] * 6
    slides = [_make_slide(i, f"s{i}") for i in range(12)]
    deck = Deck(source_pdf="fake.pdf", num_pages=12, slides=slides)

    segmenter = TopicSegmenter(_FakeFixedEmbeddingModel(vectors), min_segment_length=3)
    topics, diagnostics = segmenter.segment(deck)

    print(f"topics: {[(t.start_slide_id, t.end_slide_id, t.boundary_confidence) for t in topics]}")
    assert len(topics) == 2
    assert topics[0].slide_ids == list(range(6))
    assert topics[1].slide_ids == list(range(6, 12))
    assert topics[0].boundary_confidence == 0.0  # deck's first topic, no preceding cut
    assert topics[1].boundary_confidence > 0.0


def test_real_boundary_near_deck_end_is_detected_despite_weaker_scale_coverage():
    # Reproduces the real bug found on cc-unit2-slides.pdf: a genuine
    # topic shift sitting within round(sqrt(N)) slides of the deck's
    # end never gets the largest multi-scale window's contribution
    # (similarity.py's own documented edge-handling rule -- not enough
    # slides remain beyond it to compute that scale's block similarity)
    # -- so it can only ever show a smaller depth score than an
    # equally-real shift in the well-covered interior. A single global
    # threshold, calibrated mostly on interior positions that DO get
    # the larger scale's typically bigger separations, sets a bar this
    # boundary can never structurally clear even though it's real. n=64
    # -> scales [2, 3, 8]; block C (8 slides) sits entirely within the
    # last scale-8 window, moderately (not strongly) separated from
    # block B -- same shape as the real DevOps-sign-off -> Kubernetes
    # case (measured 0.55 against a 0.66 global threshold there).
    a, b = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]
    c = [0.0, 0.85, 0.53]  # moderately separated from b -- a real but weaker shift
    vectors = [a] * 32 + [b] * 24 + [c] * 8
    slides = [_make_slide(i, f"s{i}") for i in range(64)]
    deck = Deck(source_pdf="fake.pdf", num_pages=64, slides=slides)

    segmenter = TopicSegmenter(_FakeFixedEmbeddingModel(vectors), min_segment_length=3)
    topics, diagnostics = segmenter.segment(deck)

    print(f"topics: {[(t.start_slide_id, t.end_slide_id, t.boundary_confidence) for t in topics]}")
    print(f"global threshold: {diagnostics['threshold']:.4f}")

    # The interior A/B boundary (well-covered by every scale) must
    # still be found -- this fix must not weaken detection elsewhere.
    assert any(t.start_slide_id == 32 for t in topics)

    # The near-end B/C boundary: real, but only visible via the small
    # scales (k=8 is NaN there). Confirm it fails the OLD single global
    # threshold this fix replaces (the exact regression being guarded
    # against), then confirm the segmenter -- using the new
    # scale-coverage-bucketed threshold -- finds it anyway.
    combined_depth_at_56 = diagnostics["combined_depth"][55]
    assert combined_depth_at_56 < diagnostics["threshold"], (
        "test premise broken: boundary 55 should fail the plain global threshold"
    )
    assert any(t.start_slide_id == 56 for t in topics), "the near-end boundary was not detected"


def test_empty_slide_excluded_from_boundary_candidacy_but_stays_in_a_topic():
    # A blank (image-only) slide sits exactly at the true content
    # transition -- the only two positions with a real dip (4 and 5,
    # both flanking the blank slide) are exactly the ones excluded from
    # candidacy per the chosen design decision, so this deck correctly
    # stays a single topic rather than guessing at an untrustworthy cut
    # right next to content-free data. The invariants that actually
    # matter regardless of outcome: neither excluded position is ever
    # chosen, and the blank slide is never lost from the output.
    a, b = [1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]
    vectors = [a] * 5 + [[0.0, 0.0, 0.0, 0.0]] + [b] * 5  # slide 5 is the "blank" one (all-zero embedding)
    slides = [_make_slide(i, f"s{i}", ["x"]) for i in range(11)]
    slides[5] = _make_slide(5, None, [])  # actually blank per is_empty()
    deck = Deck(source_pdf="fake.pdf", num_pages=11, slides=slides)

    segmenter = TopicSegmenter(_FakeFixedEmbeddingModel(vectors), min_segment_length=1)
    topics, diagnostics = segmenter.segment(deck)

    print(f"topics: {[(t.start_slide_id, t.end_slide_id) for t in topics]}")
    # boundaries 4 (between slide4&5) and 5 (between slide5&6) must never
    # be chosen as cuts, since slide 5 is empty and flanks both.
    chosen_boundaries = {t.start_slide_id - 1 for t in topics[1:]}
    assert 4 not in chosen_boundaries
    assert 5 not in chosen_boundaries
    # slide 5 must still be accounted for in exactly one topic.
    all_slide_ids = [sid for t in topics for sid in t.slide_ids]
    assert sorted(all_slide_ids) == list(range(11))
