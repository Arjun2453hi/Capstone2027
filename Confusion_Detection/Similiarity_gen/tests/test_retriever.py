"""Numeric ground-truth accuracy tests (claude.md Section 4).

The point of this file isn't "pass/fail" — it's to know how good the
similarity signal actually is, run against a real model, with the
scores printed so a human (or a future Claude session) can see the
distribution. Run with `pytest -s` to see the printed tables.
"""
from __future__ import annotations

import statistics

import pytest

from ..embedding_models import SentenceTransformerEmbeddingModel, TfidfEmbeddingModel
from ..retriever import QuestionSlideRetriever
from ..slide_index import SlideIndex
from .fixtures.ground_truth_questions import DECK, GROUND_TRUTH, HARD_NEGATIVE_QUESTION

# Parametrizing over both concrete models (rather than just TfidfEmbeddingModel)
# is what actually proves the DI works end-to-end — QuestionSlideRetriever's
# code doesn't change at all between the two runs below.
MODEL_FACTORIES = {
    "tfidf": TfidfEmbeddingModel,
    "sentence-transformer": pytest.param(
        SentenceTransformerEmbeddingModel, marks=pytest.mark.slow
    ),
}


@pytest.fixture(params=list(MODEL_FACTORIES.values()), ids=list(MODEL_FACTORIES.keys()))
def retriever(request):
    model = request.param()
    index = SlideIndex.build(DECK, model)
    return QuestionSlideRetriever(index)


def test_ground_truth_top1_and_hard_negative(retriever):
    correct = 0
    top1_scores = []
    rows = []

    for case in GROUND_TRUTH:
        results = retriever.retrieve(case["question"], top_k=1)
        top = results[0]
        is_correct = top.slide_id == case["expected_slide_id"]
        correct += is_correct
        top1_scores.append(top.score)
        rows.append((case["question"], top.slide_id, top.score, case["expected_slide_id"], is_correct))
        print(
            f"Q: {case['question']!r} -> slide {top.slide_id} "
            f"(score={top.score:.4f}), expected {case['expected_slide_id']} "
            f"{'OK' if is_correct else 'MISS'}"
        )

    accuracy = correct / len(GROUND_TRUTH)
    mean_score = statistics.fmean(top1_scores)
    median_score = statistics.median(top1_scores)
    print(f"\nTop-1 accuracy: {accuracy:.2%} ({correct}/{len(GROUND_TRUTH)})")
    print(f"Mean top-1 score: {mean_score:.4f}, median: {median_score:.4f}")

    hard_neg_result = retriever.retrieve(HARD_NEGATIVE_QUESTION, top_k=1)[0]
    print(
        f"Hard negative Q: {HARD_NEGATIVE_QUESTION!r} -> slide "
        f"{hard_neg_result.slide_id} (score={hard_neg_result.score:.4f})"
    )

    # Regression baseline, not a tautology: this is the number to watch
    # if a future change to this package quietly makes retrieval worse.
    assert accuracy >= 0.75, (
        f"Top-1 accuracy dropped to {accuracy:.2%} — investigate before "
        f"loosening this assertion."
    )

    # A model that scores everything similarly high on short text would
    # pass every "is it in the top-1" check while being useless for gap
    # detection (it can't tell "covered" from "not covered"). The hard
    # negative must score clearly below the ground-truth matches.
    assert hard_neg_result.score < mean_score - 0.05, (
        f"Hard-negative score ({hard_neg_result.score:.4f}) isn't clearly "
        f"below the ground-truth mean ({mean_score:.4f}) — the model may "
        f"not be discriminating between relevant and irrelevant slides."
    )


def test_batch_matches_single_question_retrieval(retriever):
    # Batch retrieval must be equivalent to calling retrieve() per
    # question, not just faster — this is what callers (Step 2/3) rely on.
    questions = [c["question"] for c in GROUND_TRUTH]
    batch_results = retriever.retrieve_batch(questions, top_k=1)
    for question, batch_result in zip(questions, batch_results):
        single_result = retriever.retrieve(question, top_k=1)
        assert batch_result[0].slide_id == single_result[0].slide_id
        assert batch_result[0].score == pytest.approx(single_result[0].score)
