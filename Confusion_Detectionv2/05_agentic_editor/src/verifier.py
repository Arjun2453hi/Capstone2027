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

UNANSWERED = "fact-check, unanswered question: "

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
Also list every student question the slide claims to answer that it does
not actually answer: use "where": "questions" and copy that question
exactly into "question", one entry per question.
Do NOT flag a correct statement only because the deck does not mention
it -- adding what the deck is missing is the point of the slide.

Check each bullet on its own, as worded, against what you know of the
technology -- not only against the deck. Errors that are easy to miss:
  - a mapping or relationship stated backwards (who maps to whom, what is
    privileged where, inside vs outside, parent vs child);
  - a count or list presented as complete ("uses six X") when the real
    set differs, or an optional feature presented as the default;
  - a "because", "so" or "showing" whose conclusion does not follow;
  - what an instruction or command actually does (reads vs writes,
    limits vs labels, creates vs joins);
  - the wrong actor (an application vs the OS kernel, guest vs host);
  - wording stronger than the fact ("any", "always", "never",
    "instantly", "security breach") where the truth is conditional;
  - a sentence about the slide or the deck itself ("this slide does not
    show ...") instead of about the subject;
  - numbering or order taken from the deck ("Principle #1", "step 3")
    that does not match the deck slide exactly -- check each number.
Flag such a bullet even if the rest of it is right.

Respond with strict JSON only:
{"problems": [{"where": "title" or <bullet number> or "questions", "question": "<only for questions>", "issue": "<one sentence>"}]}
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
    lines += ["", "Student questions this slide claims to answer:"] + [f"- {q}" for q in edit.addresses_questions]
    return "\n".join(lines)


def parse_problems(raw: Optional[str]) -> Optional[List[str]]:
    """The fact-check's problems as readable strings; None if the output
    could not be read at all (which the caller treats as a failure)."""
    if not raw:
        return None
    try:
        data = json.loads(raw.strip())
        items = data.get("problems", [] if "bullets" in data else None)
        bullets = data.get("bullets", [])
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None
    if not isinstance(items, list) or not isinstance(bullets, list):
        return None
    problems = []
    # Per-bullet verdicts: every bullet judged "true": false is a problem.
    for b in bullets:
        if not isinstance(b, dict):
            return None
        if b.get("true") is False or str(b.get("true")).lower() == "false":
            issue = str(b.get("issue") or b.get("claim") or "judged false").strip()
            problems.append(f"fact-check, bullet {b.get('n')}: {issue}")
    for item in items:
        if isinstance(item, dict) and str(item.get("issue") or "").strip():
            where = item.get("where")
            issue = str(item["issue"]).strip()
            if where in ("questions", "question"):
                question = str(item.get("question") or "").strip()
                problems.append(f"{UNANSWERED}{question} -- {issue}" if question else f"fact-check, questions: {issue}")
                continue
            label = "title" if where in (None, "title", 0, "0") else f"bullet {where}"
            problems.append(f"fact-check, {label}: {issue}")
        elif isinstance(item, str) and item.strip():
            problems.append(f"fact-check: {item.strip()}")
        else:
            return None  # malformed findings must not become a clean verification
    return problems


class EditVerifier:
    def __init__(self, generator: EditGenerator):
        self.generator = generator

    def verify(self, edit: SlideEdit, dossier, deck) -> List[str]:
        problems = parse_problems(self.generator.generate(SYSTEM_PROMPT, build_user_prompt(edit, dossier, deck)))
        if problems is None:
            return ["fact-check could not be completed; review this slide by hand"]
        return problems


def drop_unanswered_questions(checked: CheckedEdit) -> None:
    """A slide that answers some of the questions it cites is a good
    slide that over-claims. The questions the fact-check named as not
    answered are taken off its list -- not the slide thrown away -- as
    long as at least one question it really answers remains."""
    import difflib

    unanswered = [p for p in checked.problems if p.startswith(UNANSWERED)]
    if not unanswered:
        return
    named = [p[len(UNANSWERED):].split(" -- ")[0].lower() for p in unanswered]

    def is_named(question: str) -> bool:
        return any(difflib.SequenceMatcher(None, question.lower(), n).ratio() >= 0.9 for n in named)

    cited = checked.edit.addresses_questions
    keep = [q for q in cited if not is_named(q)]
    if keep and len(cited) - len(keep) == len(unanswered):  # every named question was found and removed
        checked.edit.addresses_questions = keep
        checked.problems = [p for p in checked.problems if not p.startswith(UNANSWERED)]


def verify_checked(checked: List[CheckedEdit], verifier: EditVerifier, dossiers_by_id: dict, deck) -> List[CheckedEdit]:
    """Fact-checks the edits that passed the checker (a rejected edit is
    not worth an LLM call) and adds whatever it finds to their problems.
    Repairs are skipped: they may only restate their own slide, which
    the checker already enforces word by word."""
    for c in checked:
        if c.accepted and c.edit.operation != REPAIR_SLIDE:
            c.problems.extend(verifier.verify(c.edit, dossiers_by_id[c.edit.topic_id], deck))
            drop_unanswered_questions(c)
    return checked
