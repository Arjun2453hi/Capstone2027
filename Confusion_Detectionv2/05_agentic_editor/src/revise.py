"""revise.py — the full suggestion pipeline for one gap, with self-correction.

    plan -> check -> fact-check -> (revise -> check -> fact-check) x 2

A flagged slide is not thrown away on the first finding: it goes back to
the model with the exact findings, the deck slides around it and the
questions it should answer, and the model fixes it -- or removes the
statement it cannot make correct. The revision is checked and
fact-checked again like a new slide. Whatever still fails after
MAX_REVISIONS rounds stays rejected, with its findings, for a person to
see.

Shared by run_editor.py and the demo, so both produce the same results.
"""
from __future__ import annotations

import json
from typing import Callable, List, Optional

from .checker import check_edits
from .grounding import slide_context
from .planner import EditGenerator, parse_edits, snap_to_known_questions
from .schema import APPEND_BULLETS, CheckedEdit, SlideEdit
from .verifier import NEIGHBOUR_SLIDES, EditVerifier, drop_unanswered_questions

MAX_REVISIONS = 2

SYSTEM_PROMPT = """You revise a proposed lecture slide that failed review.

You are given the deck slides around where it goes, the proposed slide,
the student questions it must answer, and the reviewer's findings.
Fix every finding:
- A wrong or contradicting statement: correct it so it agrees with the
  deck slides and is technically right, or remove it.
- An invented number or threshold: remove it, or mark it clearly as an
  example.
- A question the slide does not answer: answer it, or remove it from
  addresses_questions.
- Anything about length or format: follow the limit given.
- A slide that "has no room" for added bullets: make it a new slide
  instead ("operation": "insert_slide_after", same after_slide_id) with
  its own descriptive title (never the title of the slide it follows)
  and 3 to 6 bullets that each add something different, drawn from the
  deck slides and the student questions.
Keep everything that was not flagged. Bullets stay plain sentences of
at most 30 words; 3 to 6 bullets for a new slide, 1 to 3 when adding to
an existing slide. addresses_questions must be copied exactly from the
list.

Respond with strict JSON only, the revised slide as the single edit:
{"edits": [{"operation": "<same as before, unless told to change it>", "after_slide_id": <same>, "title": "<str>", "bullets": ["<str>", ...], "addresses_questions": ["<str>", ...]}]}"""


def build_revision_prompt(edit: SlideEdit, problems: List[str], dossier, deck) -> str:
    first, last = min(dossier.slide_ids), max(dossier.slide_ids)
    ids = sorted(set(dossier.slide_ids) | set(range(max(first - NEIGHBOUR_SLIDES, 0), last + NEIGHBOUR_SLIDES + 1)))
    proposed = {
        "operation": edit.operation,
        "after_slide_id": edit.after_slide_id,
        "title": edit.title,
        "bullets": edit.bullets,
        "addresses_questions": edit.addresses_questions,
    }
    lines = [
        "Deck slides:",
        slide_context(deck, ids, focus_slide_id=edit.after_slide_id),
        "",
        "Student questions matched to this topic:",
        *[f"- {m.question}" for m in dossier.matched_questions],
        "",
        "Proposed slide:",
        json.dumps(proposed, ensure_ascii=False, indent=1),
        "",
        "Reviewer's findings:",
        *[f"- {p}" for p in problems],
    ]
    return "\n".join(lines)


def revise_edit(edit: SlideEdit, problems: List[str], dossier, deck, report, generator: EditGenerator) -> Optional[SlideEdit]:
    """The model's fix for one flagged edit, or None if it gave none."""
    raw = generator.generate(SYSTEM_PROMPT, build_revision_prompt(edit, problems, dossier, deck))
    revised = parse_edits(raw, report)
    if not revised:
        return None
    new = revised[0]
    # A revision fixes the slide in place. The one change of kind allowed
    # is bullets that do not fit becoming a new slide after the same one.
    no_room = any("has no room" in p for p in problems)
    if not (no_room and edit.operation == APPEND_BULLETS and new.operation == "insert_slide_after"):
        new.operation = edit.operation
    new.after_slide_id = edit.after_slide_id
    new.addresses_questions = snap_to_known_questions(new.addresses_questions, [m.question for m in dossier.matched_questions])
    if new.operation == APPEND_BULLETS:
        new.title = edit.title
    return new


def suggest_for_topic(
    report,
    dossier,
    deck,
    dossiers_by_id: dict,
    planner,
    verifier: Optional[EditVerifier],
    reviser: Optional[EditGenerator] = None,
    earlier: Optional[List[SlideEdit]] = None,
    max_revisions: int = MAX_REVISIONS,
    progress: Callable[[str], None] = lambda message: None,
    room=None,
) -> List[CheckedEdit]:
    """Every suggestion for one gap, each accepted or rejected with its
    findings. `earlier`: accepted edits for other gaps, so a repeat of one
    of them is caught. `verifier=None` skips the fact-check (and with it
    the revisions it would trigger)."""
    earlier = earlier or []
    reviser = reviser or planner.generator

    def review(edits: List[SlideEdit]) -> List[CheckedEdit]:
        checked = check_edits(earlier + edits, dossiers_by_id, deck, room)[len(earlier):]
        if verifier is not None:
            for c in checked:
                if c.accepted:
                    c.problems.extend(verifier.verify(c.edit, dossier, deck))
                    drop_unanswered_questions(c)
        return checked

    progress("Writing new slides from the report and the topic's slides")
    edits = planner.plan_topic(report, dossier, deck)
    if not edits:
        return []
    progress(f"{len(edits)} slide(s) proposed; running the quality checks and the fact-check")
    results = review(edits)

    for round_number in range(1, max_revisions + 1):
        flagged = [i for i, c in enumerate(results) if not c.accepted]
        if not flagged:
            break
        progress(f"Revision round {round_number}: fixing {len(flagged)} flagged slide(s)")
        for i in flagged:
            revised = revise_edit(results[i].edit, results[i].problems, dossier, deck, report, reviser)
            if revised is None:
                continue
            others = [c.edit for j, c in enumerate(results) if j != i and c.accepted]
            again = check_edits(earlier + others + [revised], dossiers_by_id, deck, room)[-1]
            if verifier is not None and again.accepted:
                again.problems.extend(verifier.verify(again.edit, dossier, deck))
                drop_unanswered_questions(again)
            again.revisions = round_number
            results[i] = again
    return results
