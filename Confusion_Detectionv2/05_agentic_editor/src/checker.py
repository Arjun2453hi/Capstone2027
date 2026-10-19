"""checker.py — the quality gate between the planner and the deck.

Plain code, no LLM: every proposed edit is either accepted or rejected
with the reasons listed. The old build's core failure was ungated
generator output reaching the final report (root CLAUDE.md); nothing the
planner writes is applied without passing this.
"""
from __future__ import annotations

import re
from typing import List

from .schema import INSERT_SLIDE_AFTER, CheckedEdit, SlideEdit

MAX_TITLE_CHARS = 90
MIN_BULLETS, MAX_BULLETS = 2, 6
MAX_BULLET_WORDS = 40
# A slide body in this deck's template holds roughly this much 22pt text
# before it runs off the bottom.
MAX_BODY_CHARS = 900
# Share of a new slide's words already on its anchor slide above which
# it is a restatement, not an addition.
MAX_OVERLAP_WITH_ANCHOR = 0.8
# Two edits for the same place in the deck (the same anchor, or anchors
# that are verbatim copies of each other) sharing this much of their
# content words say the same thing: the later one is a repeat. Measured:
# topics 14 and 18 cover the two copies of the duplicated lecture and
# both proposed a "Docker namespaces" slide.
MAX_OVERLAP_WITH_SAME_PLACE_EDIT = 0.5
_MIN_CONTENT_WORD = 4  # shorter words ("the", "and", "pid") don't tell two slides apart

# Text addressed to the instructor instead of the student: a suggestion
# about what to add is not the addition.
_META_PATTERNS = [
    r"\badd (a|an|another|one|two|more) (new |short |brief |dedicated )?(slide|section|diagram|subsection|example|table)\b",
    r"\b(the )?(instructor|lecturer|presenter|deck|slides?) (should|could|needs? to|must)\b",
    r"\b(should|could) be added\b",
    r"\b(include|insert|provide) (a|an) (diagram|figure|image|picture|table)\b",
    r"\bsee slides? \d+",
    r"\b(todo|tbd|placeholder|lorem ipsum)\b",
]


# LLM output often uses typographic look-alikes (U+202F narrow no-break
# space, U+2011 non-breaking hyphen) that a pattern's plain " " or "-"
# does not match -- "see slide\u202f130" got past the rule below.
_HYPHENS = str.maketrans({c: "-" for c in "\u2010\u2011\u2012\u2013\u2212"})


def _plain(text: str) -> str:
    return " ".join(text.translate(_HYPHENS).split())


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def _normalize(text: str) -> str:
    return " ".join(_words(text))


def duplicate_anchor_ids(deck, slide_id: int) -> List[int]:
    """Other slides that are verbatim repeats of this one IN CONTEXT:
    same text, and the slide before it has the same text too. A deck
    that repeats a whole lecture gets the new slide in every copy; a
    boilerplate slide that merely recurs (e.g. a closing slide) does
    not match, because what precedes it differs."""
    slides = deck.slides
    row = next((i for i, s in enumerate(slides) if s.slide_id == slide_id), None)
    if row is None or row == 0 or not slides[row].raw_text.strip():
        return []
    text, previous = slides[row].raw_text, slides[row - 1].raw_text
    return [
        slides[i].slide_id
        for i in range(1, len(slides))
        if i != row and slides[i].raw_text == text and slides[i - 1].raw_text == previous
    ]


def check_edit(edit: SlideEdit, dossier, deck) -> CheckedEdit:
    problems: List[str] = []

    if edit.operation != INSERT_SLIDE_AFTER:
        problems.append(f"unknown operation {edit.operation!r}")
    if edit.after_slide_id not in dossier.slide_ids:
        problems.append(f"after_slide_id {edit.after_slide_id} is not a slide of topic {edit.topic_id}")

    if not edit.title:
        problems.append("title is empty")
    elif len(edit.title) > MAX_TITLE_CHARS:
        problems.append(f"title is {len(edit.title)} chars (max {MAX_TITLE_CHARS})")

    if not MIN_BULLETS <= len(edit.bullets) <= MAX_BULLETS:
        problems.append(f"{len(edit.bullets)} bullets (need {MIN_BULLETS}-{MAX_BULLETS})")
    for i, bullet in enumerate(edit.bullets, start=1):
        n_words = len(bullet.split())
        if n_words < 3:
            problems.append(f"bullet {i} is too short to say anything")
        elif n_words > MAX_BULLET_WORDS:
            problems.append(f"bullet {i} is {n_words} words (max {MAX_BULLET_WORDS})")
    body_chars = sum(len(b) for b in edit.bullets)
    if body_chars > MAX_BODY_CHARS:
        problems.append(f"body is {body_chars} chars and would overflow the slide (max {MAX_BODY_CHARS})")

    for i, text in enumerate([edit.title] + edit.bullets):
        label = "title" if i == 0 else f"bullet {i}"
        for pattern in _META_PATTERNS:
            if re.search(pattern, _plain(text), flags=re.IGNORECASE):
                problems.append(f"{label} is advice about the deck, not slide content")
                break

    known_questions = {_normalize(m.question) for m in dossier.matched_questions}
    if not edit.addresses_questions:
        problems.append("does not name a student question it answers")
    for question in edit.addresses_questions:
        if _normalize(question) not in known_questions:
            problems.append(f"cites a question not matched to this topic: {question[:60]!r}")

    anchor = deck.get(edit.after_slide_id)
    new_words = set(_words(" ".join([edit.title] + edit.bullets)))
    if anchor is not None and new_words:
        overlap = len(new_words & set(_words(anchor.raw_text))) / len(new_words)
        if overlap > MAX_OVERLAP_WITH_ANCHOR:
            problems.append(f"{overlap:.0%} of its words are already on slide {edit.after_slide_id}: restates it")

    return CheckedEdit(edit=edit, problems=problems)


def _content_words(edit: SlideEdit) -> set:
    return {w for w in _words(" ".join([edit.title] + edit.bullets)) if len(w) >= _MIN_CONTENT_WORD}


def check_edits(edits: List[SlideEdit], dossiers_by_id: dict, deck) -> List[CheckedEdit]:
    """Checks each edit, then rejects an edit that repeats an earlier
    accepted one at the same place. Plan order decides which is kept."""
    checked = [check_edit(e, dossiers_by_id[e.topic_id], deck) for e in edits]
    kept = []  # (places, content words, edit) of edits accepted so far
    for c in checked:
        if not c.accepted:
            continue
        places = {c.edit.after_slide_id, *duplicate_anchor_ids(deck, c.edit.after_slide_id)}
        words = _content_words(c.edit)
        for other_places, other_words, other in kept:
            shared = len(words & other_words) / (min(len(words), len(other_words)) or 1)
            if places & other_places and shared > MAX_OVERLAP_WITH_SAME_PLACE_EDIT:
                c.problems.append(
                    f"repeats {other.title!r} (topic {other.topic_id}), planned for the same place in the deck ({shared:.0%} shared content words)"
                )
                break
        else:
            kept.append((places, words, c.edit))
    return checked
