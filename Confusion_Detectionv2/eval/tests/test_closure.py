"""Gap closure: deck alignment, topic carry-over, closure arithmetic."""
import importlib

from eval.closure import align_decks, closure_table, evidence_support, remap_topics

schema = importlib.import_module("01_deck_parsing.src.schema")
Topic = importlib.import_module("02_topic_segmentation.src.schema").Topic


def deck(texts):
    return schema.Deck(source_pdf="x", num_pages=len(texts), slides=[
        schema.Slide(slide_id=i, slide_number=i + 1, title=t or None, title_font_size=None, raw_text=t)
        for i, t in enumerate(texts)])


def test_alignment_finds_inserted_slides_including_around_empty_ones():
    original = deck(["a", "", "b", "c", "", "d"])
    edited = deck(["a", "", "b", "NEW 1", "c", "", "d", "NEW 2"])
    m = align_decks(original, edited)
    assert m == {0: 0, 1: 1, 2: 2, 3: 4, 4: 5, 5: 6}
    assert set(range(8)) - set(m.values()) == {3, 7}


def test_alignment_reports_a_changed_slide_as_not_found():
    m = align_decks(deck(["a", "b", "c"]), deck(["a", "b plus bullets", "c"]))
    assert 1 not in m and m[0] == 0 and m[2] == 2


def test_added_slides_join_the_topic_of_the_slide_before_them():
    topics = [Topic(0, 0, 1, [0, 1], 0.0), Topic(1, 2, 3, [2, 3], 0.5)]
    mapping = {0: 0, 1: 1, 2: 3, 3: 4}  # a slide inserted after slide 1 -> edited id 2; one appended -> 5
    out = remap_topics(topics, mapping, 6)
    assert [t.slide_ids for t in out] == [[0, 1, 2], [3, 4, 5]]
    assert [(t.start_slide_id, t.end_slide_id) for t in out] == [(0, 2), (3, 5)]


def test_closure_rate_counts_only_targeted_gaps_and_failures_as_open():
    before = {1: {"gap_type": "shallow_coverage", "confidence": 0.8}, 2: {"gap_type": "fragmented_context", "confidence": 0.9},
              3: {"gap_type": "complete_omission", "confidence": 0.7}, 4: {"gap_type": "covered", "confidence": 0.9}}
    after = {1: {"gap_type": "covered", "confidence": 0.8}, 2: {"gap_type": "shallow_coverage", "confidence": 0.6},
             3: {"gap_type": "covered", "confidence": 0.0}, 4: {"gap_type": "covered", "confidence": 0.9}}
    c = closure_table([1, 2, 3, 4], before, after)
    assert c["targeted_gaps"] == 3          # topic 4 was not a gap
    assert c["closed"] == 1                 # topic 3's "covered" came from a failed re-investigation
    assert c["gap_closure_rate"] == 1 / 3
    assert c["reassessment_failed"] == 1


def test_evidence_support_compares_the_same_question_before_and_after():
    before = {"topics": [{"topic_id": 0, "matched_questions": [{"question": "Q one?", "score": 0.5, "best_slide_id": 1}]}],
              "unmatched_questions": []}
    after = {"topics": [{"topic_id": 0, "matched_questions": [{"question": "q  one?", "score": 0.7, "best_slide_id": 2}]}],
             "unmatched_questions": []}
    rows = evidence_support(before, after, [{"topic_id": 0, "addresses_questions": ["Q one?"]}], {2})
    assert rows[0]["score_before"] == 0.5 and rows[0]["score_after"] == 0.7 and rows[0]["best_slide_is_added"]
