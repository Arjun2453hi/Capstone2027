"""grounding.py and verifier.py tests: what deck text a model is shown,
and that every fact-check outcome other than "nothing wrong" keeps the
edit from being accepted automatically."""
from __future__ import annotations

import json

from ..src.grounding import slide_context
from ..src.schema import CheckedEdit
from ..src.verifier import EditVerifier, parse_problems, verify_checked
from .fixtures.sample_inputs import ScriptedGenerator, make_deck, make_dossier, make_edit

LONG = "word " * 400  # 2000 chars


def test_context_shares_the_budget_across_a_whole_topic():
    deck = make_deck([f"Slide {i}\n{LONG}" for i in range(30)])
    text = slide_context(deck, range(30), max_chars=6000)
    lines = text.splitlines()

    assert len(lines) == 30  # every slide appears, in order
    assert lines[0].startswith("[slide 0] Slide 0") and lines[-1].startswith("[slide 29]")
    assert all(line.endswith("...") for line in lines)  # each one cut to its share
    assert len(text) < 30 * 260


def test_context_around_a_focus_gives_the_nearest_slides_in_full():
    deck = make_deck([f"Slide {i}\n{'x ' * 150}" for i in range(10)])  # ~310 chars each
    lines = slide_context(deck, range(10), focus_slide_id=5, max_chars=1000).splitlines()

    assert "(text not shown)" not in lines[5] and "(text not shown)" not in lines[4]
    assert lines[0] == "[slide 0] Slide 0 (text not shown)"  # the budget never reached it


def test_image_only_slides_are_named_not_dropped():
    deck = make_deck(["Title\nbody", ""])
    assert slide_context(deck, [0, 1]).splitlines()[1] == "[slide 1] (image only)"


def test_fact_check_findings_become_problems_that_block_acceptance():
    deck = make_deck(["Cgroups\nlimits", "Hierarchy\ntrees of groups", "Limits\ncpu"])
    response = json.dumps({"problems": [{"where": 2, "issue": "cpu.max takes a quota and a period, not one number."}]})
    generator = ScriptedGenerator(response)
    checked = verify_checked([CheckedEdit(make_edit())], EditVerifier(generator), {0: make_dossier()}, deck)

    assert not checked[0].accepted
    assert checked[0].problems == ["fact-check, bullet 2: cpu.max takes a quota and a period, not one number."]
    _, user = generator.calls[0]
    assert "[slide 1] Hierarchy trees of groups" in user and "2. Creating /myapp" in user


def test_clean_fact_check_accepts_and_rejected_edits_cost_no_call():
    deck = make_deck(["a", "b", "c"])
    generator = ScriptedGenerator('{"problems": []}')
    good, bad = CheckedEdit(make_edit()), CheckedEdit(make_edit(), problems=["title is empty"])
    verify_checked([good, bad], EditVerifier(generator), {0: make_dossier()}, deck)

    assert good.accepted and bad.problems == ["title is empty"]
    assert len(generator.calls) == 1


def test_unreadable_or_failed_fact_check_blocks_the_edit():
    for raw in ["", None, "not json", '{"problems": "none"}']:
        assert parse_problems(raw) is None
        problems = EditVerifier(ScriptedGenerator(raw)).verify(make_edit(), make_dossier(), make_deck(["a", "b", "c"]))
        assert problems == ["fact-check could not be completed; review this slide by hand"]
