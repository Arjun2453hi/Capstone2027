"""tools.py tests: get_topic_slides, get_matched_questions,
search_similar_slides, and write_report -- all deterministic, no real
LLM or embedding model. search_expanding_context's own radius-growth
logic gets its own dedicated file (test_search_expanding_context.py)."""
from __future__ import annotations

from ..src._upstream import QuestionMatch, UnmatchedQuestion
from ..src.tools import build_tools
from .fixtures.mock_groq_responses import make_dossier, make_fake_context, make_fake_deck


def test_get_topic_slides_returns_the_dossiers_window_text_and_its_slide_ids():
    # Exposing slide_ids alongside the text is what lets the model
    # honestly report slide_ids_examined in write_report -- a real bug
    # found in practice: a bare text string gave the model no way to
    # attribute content back to specific slide_ids, so it always
    # reported an empty list even when it had genuinely read content.
    dossier = make_dossier(topic_id=0, slide_ids=[0, 1], window_text="real slide content here")
    ctx = make_fake_context(dossiers=[dossier])
    tools = {t.name: t for t in build_tools(ctx)}

    result = tools["get_topic_slides"].invoke({"topic_id": 0})
    assert result == {"slide_ids": [0, 1], "text": "real slide content here"}


def test_get_topic_slides_returns_empty_for_unmatched_synthetic_topic():
    ctx = make_fake_context(dossiers=[])
    tools = {t.name: t for t in build_tools(ctx)}

    result = tools["get_topic_slides"].invoke({"topic_id": -1})
    assert result == {"slide_ids": [], "text": ""}


def test_get_topic_slides_returns_empty_for_unknown_topic_id():
    ctx = make_fake_context(dossiers=[])
    tools = {t.name: t for t in build_tools(ctx)}

    result = tools["get_topic_slides"].invoke({"topic_id": 99})
    assert result == {"slide_ids": [], "text": ""}


def test_get_matched_questions_returns_the_dossiers_matches():
    matches = [QuestionMatch(question="what is X?", score=0.8, method="semantic")]
    dossier = make_dossier(topic_id=0, slide_ids=[0], window_text="content", matched_questions=matches)
    ctx = make_fake_context(dossiers=[dossier])
    tools = {t.name: t for t in build_tools(ctx)}

    result = tools["get_matched_questions"].invoke({"topic_id": 0})
    assert result == [{"question": "what is X?", "score": 0.8, "method": "semantic"}]


def test_get_matched_questions_for_unmatched_topic_returns_unmatched_questions():
    unmatched = [UnmatchedQuestion(question="orphan question", best_score=0.2, best_topic_id=3)]
    ctx = make_fake_context(dossiers=[], unmatched_questions=unmatched)
    tools = {t.name: t for t in build_tools(ctx)}

    result = tools["get_matched_questions"].invoke({"topic_id": -1})
    assert result == [{"question": "orphan question", "score": 0.2}]


def test_search_similar_slides_ranks_by_cosine_similarity_and_respects_top_k():
    deck = make_fake_deck(n_slides=4)
    texts = [s.raw_text for s in deck.slides]
    vectors_by_text = {
        texts[0]: [1.0, 0.0, 0.0],
        texts[1]: [0.0, 1.0, 0.0],
        texts[2]: [0.9, 0.1, 0.0],  # close to slide 0
        texts[3]: [0.0, 0.0, 1.0],
        "query about topic 0": [1.0, 0.0, 0.0],
    }
    ctx = make_fake_context(deck=deck, vectors_by_text=vectors_by_text)
    tools = {t.name: t for t in build_tools(ctx)}

    result = tools["search_similar_slides"].invoke({"query_text": "query about topic 0", "top_k": 2})

    assert len(result) == 2
    assert result[0]["slide_id"] == 0  # exact match, highest score
    assert result[1]["slide_id"] == 2  # next closest
    assert result[0]["score"] >= result[1]["score"]


def test_write_report_returns_its_arguments_as_a_dict():
    ctx = make_fake_context()
    tools = {t.name: t for t in build_tools(ctx)}

    result = tools["write_report"].invoke(
        {
            "topic_id": 5,
            "slide_ids_examined": [10, 11, 12],
            "gap_type": "shallow_coverage",
            "confidence": 0.7,
            "report_text": "This topic briefly mentions X but never explains Y.",
        }
    )

    assert result["topic_id"] == 5
    assert result["gap_type"] == "shallow_coverage"
    assert result["confidence"] == 0.7
    assert result["slide_ids_examined"] == [10, 11, 12]
