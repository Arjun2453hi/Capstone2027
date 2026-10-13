"""orchestrator.py tests: the adaptive found threshold and the
per-topic dispatch loop -- deterministic, via a fake embedding model
and a scripted fake chat model."""
from __future__ import annotations

import numpy as np
import pytest

from ..src import agent as agent_module
from ..src import orchestrator as orchestrator_module
from ..src._upstream import QuestionMatch
from ..src.orchestrator import compute_found_threshold, merge_part_results, run_all_topics, split_into_parts
from ..src.schema import GapReport
from .fixtures.mock_groq_responses import (
    ScriptedChatModel,
    _FakeFixedEmbeddingModel,
    ai_tool_call,
    make_dossier,
    make_fake_context,
    make_fake_deck,
)


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    monkeypatch.setattr(agent_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(orchestrator_module.time, "sleep", lambda seconds: None)


def test_found_threshold_is_calibrated_on_windows_around_real_matches():
    """The threshold must sit on the scale search_expanding_context
    actually measures (a question against the radius-1 window around a
    slide), not on Stage 3's own single-slide match score -- here the
    recorded match score is 0.99 but the window it sits in only reaches
    ~0.58, and the threshold has to follow the window."""
    deck = make_fake_deck(n_slides=3)
    question = "what is X?"
    vectors = {
        deck.get(0).raw_text: [0.0, 1.0, 0.0],
        deck.get(1).raw_text: [1.0, 0.0, 0.0],
        deck.get(2).raw_text: [0.0, 0.0, 1.0],
        question: [1.0, 0.0, 0.0],
    }
    model = _FakeFixedEmbeddingModel(vectors, dim=3)
    match = QuestionMatch(question=question, score=0.99, method="semantic", best_slide_id=1)
    dossiers = [make_dossier(topic_id=0, slide_ids=[0, 1, 2], window_text="content", matched_questions=[match])]

    threshold = compute_found_threshold(dossiers, deck, model)

    expected = 1 / np.sqrt(3)  # the window is the mean of three orthogonal slides
    print(f"found threshold: {threshold:.3f} (window relevance {expected:.3f}, stage-3 score 0.99)")
    assert threshold == pytest.approx(expected)


def test_topics_with_no_matched_questions_are_not_investigated(tmp_path):
    match = QuestionMatch(question="what is X?", score=0.8, method="semantic", best_slide_id=1)
    dossiers = [
        make_dossier(topic_id=0, slide_ids=[0], window_text="content 0"),
        make_dossier(topic_id=1, slide_ids=[1], window_text="content 1", matched_questions=[match]),
    ]
    ctx = make_fake_context(dossiers=dossiers)
    model = ScriptedChatModel(
        [ai_tool_call("write_report", {"topic_id": 1, "gap_type": "covered", "confidence": 0.9, "report_text": "ok"}, "c1")]
    )

    results = run_all_topics(model, ctx, dossiers, [], tmp_path)

    assert [r["topic_id"] for r in results] == [1]
    assert model.call_count == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["topic_01_report.json"]


def _match(question: str, best_slide_id: int) -> QuestionMatch:
    return QuestionMatch(question=question, score=0.8, method="semantic", best_slide_id=best_slide_id)


def test_small_topic_is_not_split():
    deck = make_fake_deck(n_slides=10)
    dossier = make_dossier(0, list(range(10)), "all", [_match(f"q{i}", i) for i in range(8)])
    assert split_into_parts(dossier, deck) == [dossier]


def test_large_topic_is_split_by_where_its_questions_land():
    """12 questions: 5 around slides 2-4, 7 around slides 20-24. Each
    part gets its own questions and only the slides around them, and
    between them the parts keep every question exactly once."""
    deck = make_fake_deck(n_slides=30)
    early = [_match(f"early {i}", 2 + i % 3) for i in range(5)]
    late = [_match(f"late {i}", 20 + i % 5) for i in range(7)]
    dossier = make_dossier(0, list(range(30)), "all", early + late)

    parts = split_into_parts(dossier, deck)

    print([(p.slide_ids[0], p.slide_ids[-1], len(p.matched_questions)) for p in parts])
    assert [(p.slide_ids[0], p.slide_ids[-1]) for p in parts] == [(1, 5), (19, 25)]
    assert [len(p.matched_questions) for p in parts] == [5, 7]
    assert sorted(m.question for p in parts for m in p.matched_questions) == sorted(m.question for m in early + late)
    assert parts[0].window_text == "\n\n".join(deck.get(i).raw_text for i in range(1, 6))
    assert all(p.topic_id == 0 for p in parts)


def _part_result(gap_type: str, confidence: float, outcome: str = "completed", discovered=()):
    report = GapReport(topic_id=0, gap_type=gap_type, confidence=confidence, report_text=f"{gap_type} text",
                       discovered_slide_ids=list(discovered))
    return {"report": report, "outcome": outcome, "n_tool_calls": 3}


def test_merged_report_takes_the_most_severe_concluded_gap():
    dossier = make_dossier(0, list(range(30)), "all")
    parts = [make_dossier(0, [1, 2, 3], "a", [_match("a", 2)]), make_dossier(0, [20, 21], "b", [_match("b", 20)]),
             make_dossier(0, [27, 28], "c", [_match("c", 27)])]
    results = [_part_result("covered", 0.9, discovered=[7]), _part_result("shallow_coverage", 0.8, discovered=[7, 9]),
               _part_result("complete_omission", 0.0, outcome="cap_hit")]  # failed outright: no say in the label

    merged = merge_part_results(dossier, list(zip(parts, results)))

    assert merged["report"].gap_type == "shallow_coverage"
    assert merged["report"].confidence == pytest.approx(0.8)
    assert merged["report"].assigned_slide_ids == list(range(30))
    assert merged["report"].discovered_slide_ids == [7, 9]
    assert merged["outcome"] == "cap_hit"
    assert merged["n_tool_calls"] == 9
    assert "Part 2 of 3 -- slides 20-21, 1 question(s): shallow_coverage" in merged["report"].report_text


def test_large_topic_runs_one_investigation_per_part_and_writes_one_report(tmp_path):
    deck = make_fake_deck(n_slides=30)
    matches = [_match(f"early {i}", 2) for i in range(5)] + [_match(f"late {i}", 20) for i in range(5)]
    dossier = make_dossier(topic_id=0, slide_ids=list(range(30)), window_text="all", matched_questions=matches)
    ctx = make_fake_context(deck=deck, dossiers=[dossier])
    seen_slide_ranges = []

    class _Recording(ScriptedChatModel):
        def invoke(self, messages):
            seen_slide_ranges.append(messages[1].content.splitlines()[0])
            return super().invoke(messages)

    model = _Recording(
        [
            ai_tool_call("write_report", {"topic_id": 0, "gap_type": "covered", "confidence": 0.9, "report_text": "fine"}, "c1"),
            ai_tool_call("write_report", {"topic_id": 0, "gap_type": "covered", "confidence": 0.7, "report_text": "fine too"}, "c2"),
        ]
    )

    results = run_all_topics(model, ctx, [dossier], [], tmp_path)

    assert seen_slide_ranges == ["Topic 0, slides 1-3.", "Topic 0, slides 19-21."]
    assert len(results) == 1 and results[0]["outcome"] == "completed"
    assert results[0]["report"].confidence == pytest.approx(0.8)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["topic_00_report.json"]
    assert ctx.dossiers_by_id[0] is dossier  # the shared context is not left narrowed to a part
