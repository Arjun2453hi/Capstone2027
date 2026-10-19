"""checker.py tests: one deliberately broken edit per rule, and a
clean edit that passes all of them."""
from __future__ import annotations

import pytest

from ..src.checker import MAX_BODY_CHARS, check_edit, check_edits
from .fixtures.sample_inputs import make_deck, make_dossier, make_edit

DECK = make_deck(["Cgroups\nlimits and accounting", "Hierarchy\ncgroups are hierarchically structured", "Limits\nmemory, cpu"])
DOSSIER = make_dossier()


def test_a_well_formed_edit_is_accepted():
    checked = check_edit(make_edit(), DOSSIER, DECK)
    assert checked.accepted, checked.problems


@pytest.mark.parametrize(
    "overrides, expected_problem",
    [
        ({"after_slide_id": 9}, "not a slide of topic"),
        ({"title": ""}, "title is empty"),
        ({"title": "x" * 120}, "title is 120 chars"),
        ({"bullets": ["only one bullet here"]}, "1 bullets"),
        ({"bullets": ["fine first bullet here", "ok"]}, "bullet 2 is too short"),
        ({"bullets": ["word " * 45, "a normal second bullet"]}, "bullet 1 is 45 words"),
        ({"bullets": ["y" * 200 + " a b"] * 5}, "would overflow the slide"),
        ({"bullets": ["Add a new slide that walks through the hierarchy.", "a normal second bullet"]}, "advice about the deck"),
        ({"bullets": ["The instructor should explain parent limits.", "a normal second bullet"]}, "advice about the deck"),
        ({"addresses_questions": []}, "does not name a student question"),
        ({"addresses_questions": ["What is quicksort?"]}, "cites a question not matched"),
        ({"title": "Hierarchy", "bullets": ["cgroups are hierarchically structured", "hierarchy cgroups structured are"]}, "restates it"),
    ],
)
def test_each_rule_rejects_the_edit_that_breaks_it(overrides, expected_problem):
    checked = check_edit(make_edit(**overrides), DOSSIER, DECK)
    assert not checked.accepted
    assert any(expected_problem in p for p in checked.problems), checked.problems


def test_body_limit_is_on_total_text_not_bullet_count():
    bullets = ["z" * (MAX_BODY_CHARS // 4 - 10) + " a b"] * 4
    assert check_edit(make_edit(bullets=bullets), DOSSIER, DECK).accepted


def test_a_repeat_of_an_earlier_edit_at_the_same_place_is_rejected_but_a_different_one_is_not():
    # Slides 1-2 are repeated verbatim as 4-5: anchors 2 and 5 are the same place.
    deck = make_deck(["Intro", "Namespaces\nisolation", "PID\nprocess ids", "Intro", "Namespaces\nisolation", "PID\nprocess ids"])
    dossiers = {0: make_dossier(0, slide_ids=(0, 1, 2)), 1: make_dossier(1, slide_ids=(3, 4, 5))}
    first = make_edit(topic_id=0, after_slide_id=2)
    repeat = make_edit(topic_id=1, after_slide_id=5, title="Cgroup hierarchy example")  # same bullets, other copy
    different = make_edit(
        topic_id=1,
        after_slide_id=5,
        title="Network namespaces in practice",
        bullets=[
            "Each container receives its own virtual ethernet pair and routing table.",
            "Port mappings forward traffic from the host interface into that namespace.",
        ],
    )
    checked = check_edits([first, repeat, different], dossiers, deck)

    assert checked[0].accepted and checked[2].accepted
    assert len(checked[1].problems) == 1 and checked[1].problems[0].startswith("repeats 'A cgroup hierarchy, step by step' (topic 0)")


def test_meta_patterns_see_through_typographic_spaces_and_hyphens():
    bullet = "For deeper OS fundamentals see slide 130 on user‑space vs kernel‑space."
    edit = make_edit(bullets=make_edit().bullets[:2] + [bullet])
    assert "bullet 3 is advice about the deck, not slide content" in check_edit(edit, DOSSIER, DECK).problems
