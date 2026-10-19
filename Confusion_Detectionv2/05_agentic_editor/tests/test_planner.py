"""planner.py tests: which reports get planned at all, what the model
is shown, and that malformed output degrades to "no edits"."""
from __future__ import annotations

import json

from ..src.planner import EditPlanner, parse_edits, skip_reason
from .fixtures.sample_inputs import QUESTIONS, ScriptedGenerator, make_deck, make_dossier, make_report

DECK = make_deck(["Cgroups\nlimits and accounting", "Hierarchy\ncgroups are hierarchical", "Limits\nmemory, cpu"])
GOOD_RESPONSE = json.dumps(
    {"edits": [{"after_slide_id": 1, "title": "A worked hierarchy", "bullets": ["one two three", "four five six"], "addresses_questions": [QUESTIONS[1]]}]}
)


def test_covered_low_confidence_and_rangeless_reports_are_not_planned():
    generator = ScriptedGenerator(GOOD_RESPONSE)
    planner = EditPlanner(generator)

    assert planner.plan_topic(make_report(gap_type="covered", confidence=0.95), make_dossier(), DECK) == []
    assert planner.plan_topic(make_report(confidence=0.35), make_dossier(), DECK) == []
    assert planner.plan_topic(make_report(topic_id=-1, gap_type="complete_omission"), None, DECK) == []
    assert generator.calls == []  # none of them cost an LLM call
    assert "covered" in skip_reason(make_report(gap_type="covered"), make_dossier())
    assert "0.35" in skip_reason(make_report(confidence=0.35), make_dossier())


def test_plannable_report_yields_edits_carrying_the_reports_provenance():
    generator = ScriptedGenerator(GOOD_RESPONSE)
    edits = EditPlanner(generator).plan_topic(make_report(topic_id=7, confidence=0.85), make_dossier(topic_id=7), DECK)

    assert len(edits) == 1
    assert (edits[0].topic_id, edits[0].after_slide_id, edits[0].title) == (7, 1, "A worked hierarchy")
    assert (edits[0].gap_type, edits[0].report_confidence) == ("shallow_coverage", 0.85)
    _, user = generator.calls[0]
    assert "No hierarchy example is given." in user  # the report
    assert "[slide 1] Hierarchy cgroups are hierarchical" in user  # full slide text, by id
    assert all(q in user for q in QUESTIONS)


def test_edits_beyond_the_per_topic_limit_are_dropped():
    item = json.loads(GOOD_RESPONSE)["edits"][0]
    generator = ScriptedGenerator(json.dumps({"edits": [item, item, item]}))
    assert len(EditPlanner(generator, max_edits_per_topic=2).plan_topic(make_report(), make_dossier(), DECK)) == 2


def test_malformed_model_output_yields_no_edits_instead_of_raising():
    report = make_report()
    for raw in [None, "", "not json", "[1, 2]", '{"edits": "none"}', '{"edits": [{"title": "no anchor"}, 7]}']:
        assert parse_edits(raw, report) == []


def test_a_quoted_slide_id_is_read_as_a_number_and_junk_ids_are_dropped():
    report = make_report()
    item = json.loads(GOOD_RESPONSE)["edits"][0]
    quoted = dict(item, after_slide_id="1")
    junk = [dict(item, after_slide_id=v) for v in ("one", True, 1.5, None)]
    edits = parse_edits(json.dumps({"edits": [quoted] + junk}), report)
    assert [e.after_slide_id for e in edits] == [1]


class _FlakyCompletions:
    """Fails like Groq does on truncated JSON, then answers."""

    def __init__(self, failures, error="Error code: 400 - json_validate_failed"):
        self.failures, self.error, self.calls = failures, error, 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(self.error)
        message = type("M", (), {"content": GOOD_RESPONSE})
        return type("R", (), {"choices": [type("C", (), {"message": message})]})


def _generator(completions):
    from ..src.planner import GroqEditGenerator

    generator = GroqEditGenerator.__new__(GroqEditGenerator)
    generator._client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})})
    generator.model, generator.last_error = "test-model", None
    return generator


def test_truncated_json_is_retried_once_and_other_failures_are_not():
    flaky = _FlakyCompletions(failures=1)
    assert _generator(flaky).generate("s", "u") == GOOD_RESPONSE and flaky.calls == 2

    always = _FlakyCompletions(failures=5)
    generator = _generator(always)
    assert generator.generate("s", "u") == "" and always.calls == 2
    assert "json_validate_failed" in generator.last_error

    auth = _FlakyCompletions(failures=5, error="Error code: 401 - invalid api key")
    assert _generator(auth).generate("s", "u") == "" and auth.calls == 1


def test_a_near_exact_question_citation_is_snapped_to_the_real_question_and_an_invented_one_is_kept_for_the_checker():
    from ..src.planner import snap_to_known_questions

    known = ["What's the exact procedure for moving one end of a veth pair into a namespace?", QUESTIONS[0]]
    cited = [
        "What is the exact procedure for moving one end of a veth pair into a namespace?",
        "How does Kubernetes schedule pods?",
    ]
    assert snap_to_known_questions(cited, known) == [known[0], "How does Kubernetes schedule pods?"]
