"""verifier.py — an LLM fact-check of each proposed slide against the deck.

The checker catches what plain code can see (length, advice-not-content,
restating the anchor). It cannot see that a bullet is wrong. This pass
asks a second LLM call, with the slides around the insertion point in
full, to name any statement that contradicts the deck, is wrong, or
presents an invented number as fact; each one it names becomes a
problem on the edit, so the edit is not accepted automatically.

A failed or unreadable fact-check also blocks the edit: an unchecked
slide should be a person's decision, not a silent pass.
"""
from __future__ import annotations

import json
from typing import List, Optional

from .grounding import slide_context
from .planner import EditGenerator
from .schema import APPEND_BULLETS, REPAIR_SLIDE, CheckedEdit, SlideEdit

# Slides on either side of the insertion point given to the fact-check,
# beyond the topic's own (a gap is often explained just past a boundary).
NEIGHBOUR_SLIDES = 3

SYSTEM_PROMPT = """You fact-check a proposed new lecture slide before it is added to a deck.

You are given slides from the deck around where the new slide will go,
then the proposed slide's title and numbered bullets. List every
statement on the proposed slide that:
  (a) contradicts what the deck's slides say,
  (b) is factually wrong about the technology, or
  (c) states a specific number, threshold, percentage or limit as fact
      when the deck does not give it and it is not a well-known,
      documented value. A value clearly marked as an example is fine.
Do NOT flag a correct statement only because the deck does not mention
it -- adding what the deck is missing is the point of the slide.

Respond with strict JSON only:
{"problems": [{"where": "title" or <bullet number>, "issue": "<one sentence>"}]}
Use an empty list when nothing is wrong."""


def build_user_prompt(edit: SlideEdit, dossier, deck) -> str:
    first, last = min(dossier.slide_ids), max(dossier.slide_ids)
    ids = sorted(set(dossier.slide_ids) | set(range(max(first - NEIGHBOUR_SLIDES, 0), last + NEIGHBOUR_SLIDES + 1)))
    lines = [
        "Deck slides:",
        slide_context(deck, ids, focus_slide_id=edit.after_slide_id),
        "",
    ]
    if edit.operation == APPEND_BULLETS:
        lines += [f"Proposed bullets, to be added to the end of slide {edit.after_slide_id}:"]
    else:
        lines += [f"Proposed slide, to go after slide {edit.after_slide_id}:", f"Title: {edit.title}"]
    lines += [f"{i}. {b}" for i, b in enumerate(edit.bullets, start=1)]
    return "\n".join(lines)


def parse_problems(raw: Optional[str]) -> Optional[List[str]]:
    """The fact-check's problems as readable strings; None if the output
    could not be read at all (which the caller treats as a failure)."""
    if not raw:
        return None
    try:
        items = json.loads(raw.strip()).get("problems")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None
    if not isinstance(items, list):
        return None
    problems = []
    for item in items:
        if isinstance(item, dict) and str(item.get("issue") or "").strip():
            where = item.get("where")
            label = "title" if where in (None, "title", 0, "0") else f"bullet {where}"
            problems.append(f"fact-check, {label}: {str(item['issue']).strip()}")
        elif isinstance(item, str) and item.strip():
            problems.append(f"fact-check: {item.strip()}")
    return problems


class EditVerifier:
    def __init__(self, generator: EditGenerator):
        self.generator = generator

    def verify(self, edit: SlideEdit, dossier, deck) -> List[str]:
        problems = parse_problems(self.generator.generate(SYSTEM_PROMPT, build_user_prompt(edit, dossier, deck)))
        if problems is None:
            return ["fact-check could not be completed; review this slide by hand"]
        return problems


def verify_checked(checked: List[CheckedEdit], verifier: EditVerifier, dossiers_by_id: dict, deck) -> List[CheckedEdit]:
    """Fact-checks the edits that passed the checker (a rejected edit is
    not worth an LLM call) and adds whatever it finds to their problems.
    Repairs are skipped: they may only restate their own slide, which
    the checker already enforces word by word."""
    for c in checked:
        if c.accepted and c.edit.operation != REPAIR_SLIDE:
            c.problems.extend(verifier.verify(c.edit, dossiers_by_id[c.edit.topic_id], deck))
    return checked
