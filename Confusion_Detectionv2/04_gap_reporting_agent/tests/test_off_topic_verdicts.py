"""Per-question verdicts and the off_topic class for the
unmatched-questions investigation (topic_id -1)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ..src import agent as agent_module
from ..src.agent import run_topic_investigation
from ..src.schema import GapReport, QuestionVerdict, group_gap_type
from ..src.severity import severity
from .fixtures.mock_groq_responses import ScriptedChatModel, ai_tool_call, make_fake_context

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    monkeypatch.setattr(agent_module.time, "sleep", lambda seconds: None)


def _search():
    return ai_tool_call("search_similar_slides", {"query_text": "anything"}, "s1")


def test_group_gap_type_is_derived_from_verdicts():
    v = lambda verdict: QuestionVerdict(question="q", verdict=verdict)  # noqa: E731
    assert group_gap_type([v("off_topic"), v("off_topic")]) == "off_topic"
    assert group_gap_type([v("off_topic"), v("shallow_coverage")]) == "shallow_coverage"
    assert group_gap_type([v("covered"), v("complete_omission"), v("off_topic")]) == "complete_omission"


def test_off_topic_is_rejected_for_a_real_topic():
    with pytest.raises(ValidationError):
        GapReport(topic_id=3, gap_type="off_topic", confidence=0.9, report_text="x")


def test_off_topic_ranks_below_every_real_gap():
    assert severity("off_topic", 5, 0.95) == 0.0 < severity("shallow_coverage", 1, 0.1)


def test_committed_reports_written_before_verdicts_still_load():
    for path in sorted(OUTPUT_DIR.glob("topic_*_report.json")):
        GapReport(**json.loads(path.read_text(encoding="utf-8")))


def test_unmatched_report_takes_its_type_from_the_verdicts():
    ctx = make_fake_context()
    model = ScriptedChatModel([
        _search(),
        ai_tool_call("write_report", {
            "topic_id": -1, "gap_type": "complete_omission", "confidence": 0.8, "report_text": "searched each one",
            "question_verdicts": [
                {"question": "How do I center a div in CSS?", "verdict": "off_topic"},
                {"question": "What is a hypercall?", "verdict": "shallow_coverage"},
            ]}, "w1"),
    ])
    report = run_topic_investigation(model, ctx, topic_id=-1, kickoff_message="unmatched")["report"]
    # The model said complete_omission, but its own verdicts say thin + off-topic.
    assert report.gap_type == "shallow_coverage"
    assert [v.verdict for v in report.question_verdicts] == ["off_topic", "shallow_coverage"]


def test_all_off_topic_unmatched_group_is_not_a_gap():
    ctx = make_fake_context()
    model = ScriptedChatModel([
        _search(),
        ai_tool_call("write_report", {
            "topic_id": -1, "gap_type": "complete_omission", "confidence": 0.9, "report_text": "unrelated subjects",
            "question_verdicts": [{"question": "TCP vs UDP?", "verdict": "off_topic"}]}, "w1"),
    ])
    assert run_topic_investigation(model, ctx, topic_id=-1, kickoff_message="u")["report"].gap_type == "off_topic"


def test_unmatched_report_without_verdicts_is_sent_back():
    ctx = make_fake_context()
    model = ScriptedChatModel([
        _search(),
        ai_tool_call("write_report", {"topic_id": -1, "gap_type": "complete_omission", "confidence": 0.5,
                                      "report_text": "nothing found"}, "w1"),
        ai_tool_call("write_report", {"topic_id": -1, "gap_type": "complete_omission", "confidence": 0.5,
                                      "report_text": "nothing found",
                                      "question_verdicts": [{"question": "q", "verdict": "complete_omission"}]}, "w2"),
    ])
    result = run_topic_investigation(model, ctx, topic_id=-1, kickoff_message="u")
    assert result["outcome"] == "completed" and model.call_count == 3
    assert result["report"].question_verdicts[0].verdict == "complete_omission"


def test_malformed_verdict_is_sent_back_not_crashed_on():
    ctx = make_fake_context()
    model = ScriptedChatModel([
        _search(),
        ai_tool_call("write_report", {"topic_id": -1, "gap_type": "covered", "confidence": 0.5, "report_text": "x",
                                      "question_verdicts": [{"question": "q", "verdict": "maybe"}]}, "w1"),
        ai_tool_call("write_report", {"topic_id": -1, "gap_type": "covered", "confidence": 0.5, "report_text": "x",
                                      "question_verdicts": [{"question": "q", "verdict": "covered"}]}, "w2"),
    ])
    assert run_topic_investigation(model, ctx, topic_id=-1, kickoff_message="u")["report"].gap_type == "covered"


def test_report_is_filed_under_the_investigated_topic():
    ctx = make_fake_context()
    model = ScriptedChatModel([
        ai_tool_call("get_topic_slides", {"topic_id": 0}, "c1"),
        ai_tool_call("write_report", {"topic_id": 7, "gap_type": "covered", "confidence": 0.9, "report_text": "ok"}, "c2"),
    ])
    assert run_topic_investigation(model, ctx, topic_id=0, kickoff_message="t")["report"].topic_id == 0


def test_groq_output_parse_failure_is_retried_not_fatal():
    from ..src.agent import _is_tool_call_parse_error

    class Err(Exception):
        status_code = 400

    assert _is_tool_call_parse_error(Err("Error code: 400 - {'error': {'code': 'output_parse_failed'}}"))
    assert _is_tool_call_parse_error(Err("tool_use_failed"))
    assert not _is_tool_call_parse_error(Err("something else"))


def test_one_topic_crashing_does_not_end_the_run(tmp_path, monkeypatch):
    from ..src import orchestrator
    from .fixtures.mock_groq_responses import make_dossier

    calls = []

    def fake(chat_model, ctx, dossier):
        calls.append(dossier.topic_id)
        if dossier.topic_id == 0:
            raise RuntimeError("provider exploded")
        return {"report": GapReport(topic_id=dossier.topic_id, gap_type="covered", confidence=0.9, report_text="ok"),
                "outcome": "completed", "n_tool_calls": 1}

    monkeypatch.setattr(orchestrator, "_investigate_topic", fake)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda s: None)
    d0, d1 = (make_dossier(t, [t], "text", matched_questions=["a question"]) for t in (0, 1))
    results = orchestrator.run_all_topics(None, make_fake_context(), [d0, d1], [], tmp_path)
    assert calls == [0, 1]
    assert [r["outcome"] for r in results] == ["error", "completed"]
    assert not (tmp_path / "topic_00_report.json").exists() and (tmp_path / "topic_01_report.json").exists()


def test_reworded_searches_are_flagged_and_new_ones_are_not():
    from ..src.tools import _similar_earlier_query

    earlier = ["function point analysis", "Pareto principle software metrics"]
    assert _similar_earlier_query("function point", earlier) == "function point analysis"
    assert _similar_earlier_query("function point analysis example", earlier) == "function point analysis"
    assert _similar_earlier_query("Pareto", earlier) == "Pareto principle software metrics"
    assert _similar_earlier_query("cyclomatic complexity", earlier) is None
    assert _similar_earlier_query("the of and", earlier) is None


def test_last_step_forces_write_report_when_the_model_supports_it():
    from ..src.agent import _bind_forcing_report

    class Model:
        def bind_tools(self, tools, tool_choice=None):
            self.choice = tool_choice
            return self

    m = Model()
    assert _bind_forcing_report(m, []).choice == "write_report"

    class OldModel:
        def bind_tools(self, tools):
            return "plain"

    assert _bind_forcing_report(OldModel(), []) == "plain"


def test_every_topic_gets_per_question_verdicts_that_set_its_type():
    from ..src.agent import _build_report

    class Ctx:
        unmatched_questions = []
        dossiers_by_id = {3: type("D", (), {"matched_questions": ["What is a hypervisor?", "How does EPT work in detail?"]})()}

    args = {"gap_type": "complete_omission", "confidence": 0.8, "report_text": "x",
            "question_verdicts": [{"question": "what is a hypervisor", "verdict": "covered"},          # paraphrased
                                  {"question": "How does EPT work in detail?", "verdict": "shallow_coverage"}]}
    r = _build_report(3, args, [1], [], ctx=Ctx())
    assert r.gap_type == "shallow_coverage"  # derived from the questions, not the model's topic label
    assert [v.question for v in r.question_verdicts] == ["What is a hypervisor?", "How does EPT work in detail?"]
    only_off = dict(args, question_verdicts=[{"question": "What is a hypervisor?", "verdict": "off_topic"}])
    assert _build_report(3, only_off, [1], [], ctx=Ctx()).gap_type == "covered"  # a real topic is never off_topic


def test_split_topic_parts_keep_every_question_verdict():
    from ..src.orchestrator import merge_part_results
    from ..src.schema import QuestionVerdict
    from .fixtures.mock_groq_responses import make_dossier

    def part(verdict, q):
        rep = GapReport(topic_id=5, gap_type="covered", confidence=0.8, report_text="p",
                        question_verdicts=[QuestionVerdict(question=q, verdict=verdict)])
        return make_dossier(5, [0, 1], "t", matched_questions=[q]), {"report": rep, "outcome": "completed", "n_tool_calls": 3}

    merged = merge_part_results(make_dossier(5, [0, 1, 2, 3], "t"), [part("covered", "a?"), part("complete_omission", "b?")])
    assert merged["report"].gap_type == "complete_omission"
    assert {v.question for v in merged["report"].question_verdicts} == {"a?", "b?"}


def test_verdicts_can_cite_questions_by_number():
    from ..src.agent import _build_report

    class Ctx:
        unmatched_questions = []
        dossiers_by_id = {2: type("D", (), {"matched_questions": ["First question?", "Second question?"]})()}

    args = {"gap_type": "covered", "confidence": 0.8, "report_text": "x",
            "question_verdicts": [{"n": 2, "verdict": "complete_omission"}, {"n": 1, "verdict": "covered"}]}
    r = _build_report(2, args, [], [], ctx=Ctx())
    assert {(v.question, v.verdict) for v in r.question_verdicts} == {("Second question?", "complete_omission"),
                                                                      ("First question?", "covered")}
    assert r.gap_type == "complete_omission"
    with pytest.raises(ValueError):
        _build_report(2, dict(args, question_verdicts=[{"n": 7, "verdict": "covered"}]), [], [], ctx=Ctx())
