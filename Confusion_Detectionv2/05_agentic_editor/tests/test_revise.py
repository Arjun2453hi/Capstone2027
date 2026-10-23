"""revise.py tests: a flagged slide goes back to the model with its
findings and is checked again; what is still wrong stays rejected."""
from __future__ import annotations

import json

from ..src.planner import EditPlanner, with_max_tokens
from ..src.revise import suggest_for_topic
from ..src.verifier import EditVerifier, parse_problems
from .fixtures.sample_inputs import QUESTIONS, ScriptedGenerator, make_deck, make_dossier, make_report

DECK = make_deck(["Cgroups\nlimits and accounting", "Hierarchy\ncgroups are hierarchical", "Limits\nmemory, cpu"])
GOOD = ["The root cgroup holds every process until one is moved.", "A child group gets its own limit files.", "A parent limit caps its whole subtree."]
WRONG = GOOD[:2] + ["A child cgroup can exceed its parent's memory limit."]


def _plan(bullets):
    return json.dumps({"edits": [{"operation": "insert_slide_after", "after_slide_id": 1, "title": "A worked hierarchy", "bullets": bullets, "addresses_questions": [QUESTIONS[1]]}]})


class Sequence(ScriptedGenerator):
    """Answers each call with the next scripted response."""

    def __init__(self, responses):
        super().__init__(None)
        self.responses = list(responses)

    def generate(self, system, user):
        self.calls.append((system, user))
        return self.responses.pop(0)


FLAG = json.dumps({"problems": [{"where": 3, "issue": "A child cannot exceed its parent's limit."}]})
CLEAN = json.dumps({"problems": []})


def _run(planner_responses, verifier_responses, max_revisions=2):
    planner = EditPlanner(Sequence(planner_responses))
    verifier = EditVerifier(Sequence(verifier_responses))
    return suggest_for_topic(make_report(), make_dossier(), DECK, {0: make_dossier()}, planner, verifier, max_revisions=max_revisions), planner, verifier


def test_a_flagged_slide_is_revised_with_its_findings_and_accepted_once_fixed():
    results, planner, verifier = _run([_plan(WRONG), _plan(GOOD)], [FLAG, CLEAN])

    assert len(results) == 1 and results[0].accepted and results[0].revisions == 1
    assert results[0].edit.bullets == GOOD
    revision_prompt = planner.generator.calls[1][1]
    assert "A child cannot exceed its parent's limit." in revision_prompt  # the finding was sent back
    assert "[slide 1] Hierarchy cgroups are hierarchical" in revision_prompt  # with the deck around it


def test_a_slide_still_wrong_after_every_revision_stays_rejected():
    results, planner, _ = _run([_plan(WRONG), _plan(WRONG), _plan(WRONG)], [FLAG, FLAG, FLAG])

    assert not results[0].accepted and results[0].revisions == 2
    assert len(planner.generator.calls) == 3  # one plan + two revisions, then it stops


def test_a_clean_slide_costs_no_revision():
    results, planner, verifier = _run([_plan(GOOD)], [CLEAN])
    assert results[0].accepted and results[0].revisions == 0
    assert len(planner.generator.calls) == 1 and len(verifier.generator.calls) == 1


def test_unanswered_questions_are_reported_and_the_fact_check_can_use_a_smaller_budget():
    problems = parse_problems(json.dumps({"problems": [{"where": "questions", "issue": "Does not show a hierarchy example."}]}))
    assert problems == ["fact-check, questions: Does not show a hierarchy example."]

    class Budgeted(ScriptedGenerator):
        max_tokens = 3000

    original = Budgeted("x")
    smaller = with_max_tokens(original, 1500)
    assert (smaller.max_tokens, original.max_tokens) == (1500, 3000)
    plain = ScriptedGenerator("x")
    assert with_max_tokens(plain, 1500) is plain


def test_questions_the_slide_does_not_answer_are_dropped_not_the_slide():
    from ..src.schema import CheckedEdit
    from ..src.verifier import drop_unanswered_questions
    from .fixtures.sample_inputs import make_edit

    raw = json.dumps({"problems": [{"where": "questions", "question": QUESTIONS[0], "issue": "Never defines a cgroup."}]})
    over_claiming = CheckedEdit(make_edit(addresses_questions=list(QUESTIONS)), problems=parse_problems(raw))
    drop_unanswered_questions(over_claiming)
    assert over_claiming.accepted and over_claiming.edit.addresses_questions == [QUESTIONS[1]]

    answers_none = CheckedEdit(make_edit(addresses_questions=[QUESTIONS[0]]), problems=parse_problems(raw))
    drop_unanswered_questions(answers_none)
    assert not answers_none.accepted  # nothing it really answers would be left

    wrong_too = CheckedEdit(
        make_edit(addresses_questions=list(QUESTIONS)),
        problems=parse_problems(raw) + ["fact-check, bullet 2: wrong"],
    )
    drop_unanswered_questions(wrong_too)
    assert wrong_too.problems == ["fact-check, bullet 2: wrong"]  # still rejected for the real error
