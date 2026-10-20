"""planner.py — turns one topic's gap report into proposed SlideEdits.

One LLM call per topic, behind the EditGenerator interface so tests and
the applier never need a real model. The planner decides nothing about
whether an edit is acceptable -- that is checker.py's job -- but it does
decide which reports are worth planning for at all.
"""
from __future__ import annotations

import difflib
import json
import os
from abc import ABC, abstractmethod
from typing import List, Optional

from .grounding import slide_context
from .schema import APPEND_BULLETS, INSERT_SLIDE_AFTER, SlideEdit

# A report below this is not acted on. Reports cut short at the step
# cap are written at <= 0.40, and a part-merged topic with one capped
# part lands around 0.55-0.68, so this bar lets through topics whose
# gap finding the agent actually concluded.
MIN_REPORT_CONFIDENCE = 0.6
MAX_EDITS_PER_TOPIC = 2
# gpt-oss spends part of its output budget on hidden reasoning before the
# JSON. At 1500 the two largest topics (6 and 16) ran out mid-JSON and
# Groq rejected the response (json_validate_failed). 3000 fits them while
# the largest prompt (~4.2k tokens) + this stays under 8k tokens/minute.
MAX_OUTPUT_TOKENS = 3000
# The model is told to copy cited questions exactly but sometimes slips
# ("What is" for "What's", a dropped word). A citation this close to one
# real question is that question; anything looser is left as written for
# the checker to reject as not one of the topic's questions.
MIN_QUESTION_MATCH = 0.9
# The editor writes content students will read, so it defaults to the
# larger model rather than the pipeline-wide GROQ_MODEL_NAME (the 20b
# model the mapping fallback uses). Every editor result so far was
# measured with this one. Validated at startup like any other model.
EDITOR_MODEL_ENV = "GROQ_EDITOR_MODEL_NAME"
DEFAULT_EDITOR_MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = """You write new lecture slides that close gaps found in an existing deck.

You are given one topic's gap report, the slides in that topic, and the
student questions that exposed the gap. Write the slide content itself
-- the actual example, steps, comparison or explanation a student is
missing -- not advice to the instructor about what to add.

Rules:
- Only cover what the report says is missing or thin. Do not restate
  what the slides already explain.
- At most {max_edits} edits. Fewer is better; return none if the report
  identifies nothing that can be added.
- Each edit is one of two operations:
  * "insert_slide_after": a new slide with a short title and 3 to 6
    bullets, placed directly after slide after_slide_id. Use this for
    anything substantial.
  * "append_bullets": 1 to 3 bullets added to the end of slide
    after_slide_id itself, when the missing piece is a small detail
    that belongs on that exact slide and the slide is not already full
    of text. Its title is ignored (give "").
- Every bullet is one plain sentence of at most 30 words.
- after_slide_id must be one of the listed slide ids.
- addresses_questions must be copied exactly from the question list.
- Stay consistent with the slides. Where they describe how something
  works, follow their account; never substitute a different one.
- Do not state a specific number, threshold, percentage or limit as a
  rule unless the slides give it. A value used purely as an
  illustration must say so ("for example, a 512 MB limit").

Respond with strict JSON only:
{{"edits": [{{"operation": "insert_slide_after" | "append_bullets", "after_slide_id": <int>, "title": "<str>", "bullets": ["<str>", ...], "addresses_questions": ["<str>", ...]}}]}}"""


class EditGenerator(ABC):
    @abstractmethod
    def generate(self, system: str, user: str) -> str:
        """Returns the model's raw text for one planning call."""
        raise NotImplementedError


class GroqEditGenerator(EditGenerator):
    """Client construction and startup model validation come from
    common/llm_client.py, like every other Groq-calling stage."""

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None, client=None):
        from common.llm_client import get_validated_groq_client

        model_name = model_name or os.getenv(EDITOR_MODEL_ENV) or DEFAULT_EDITOR_MODEL
        self._client, self.model = get_validated_groq_client(api_key=api_key, model_name=model_name, client=client)
        self.last_error: Optional[str] = None  # why the most recent call returned "", for callers to show
        # A topic's request is ~4-6k tokens against an 8k tokens/minute
        # limit, so back-to-back calls routinely wait out a 429; the SDK
        # honours the provider's retry-after on each retry.
        if hasattr(self._client, "with_options"):
            self._client = self._client.with_options(max_retries=5)

    def generate(self, system: str, user: str) -> str:
        self.last_error = None
        for attempt in (1, 2):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    temperature=0.0,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                )
                return response.choices[0].message.content
            except Exception as e:
                # A failed call (rate limit, the model not producing valid
                # JSON) costs this one topic or edit, not the whole run.
                # Invalid JSON is worth exactly one more try.
                self.last_error = f"{type(e).__name__}: {str(e)[:200]}"
                print(f"[warn] LLM call failed, attempt {attempt}/2 ({self.last_error})")
                if "json_validate_failed" not in str(e):
                    break
        return ""


def skip_reason(report, dossier, min_confidence: float = MIN_REPORT_CONFIDENCE) -> Optional[str]:
    """Why this report gets no edits, or None if it should be planned."""
    if dossier is None or not dossier.slide_ids:
        return "no slide range to insert into (the unmatched-questions report)"
    if report.gap_type == "covered":
        return "covered: nothing to add"
    if report.confidence < min_confidence:
        return f"confidence {report.confidence:.2f} is below {min_confidence:.2f}"
    return None


def build_user_prompt(report, dossier, deck) -> str:
    lines = [
        f"Topic {report.topic_id} -- gap type: {report.gap_type} (confidence {report.confidence:.2f})",
        "",
        "Gap report:",
        report.report_text.strip(),
        "",
        "Slides in this topic:",
        slide_context(deck, dossier.slide_ids),
        "",
        "Student questions matched to this topic:",
    ]
    lines += [f"- {m.question}" for m in dossier.matched_questions]
    return "\n".join(lines)


def snap_to_known_questions(cited: List[str], known: List[str]) -> List[str]:
    snapped = []
    for question in cited:
        ratios = [(difflib.SequenceMatcher(None, question.lower(), k.lower()).ratio(), k) for k in known]
        ratio, closest = max(ratios, default=(0.0, question))
        snapped.append(closest if ratio >= MIN_QUESTION_MATCH else question)
    return snapped


def parse_edits(raw: Optional[str], report) -> List[SlideEdit]:
    """Malformed output yields no edits rather than an exception: one bad
    planning call must not take down a run over every topic. Anything
    structurally an edit is passed on as-is -- judging it is the
    checker's job, and it must see bad edits to report them."""
    if not raw:
        return []
    try:
        items = json.loads(raw.strip()).get("edits", [])
    except (json.JSONDecodeError, AttributeError, TypeError):
        return []
    edits = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        anchor = item.get("after_slide_id")
        if isinstance(anchor, str) and anchor.strip().isdigit():
            anchor = int(anchor)  # models sometimes quote numbers in JSON
        if not isinstance(anchor, int) or isinstance(anchor, bool):
            continue
        bullets = item.get("bullets")
        questions = item.get("addresses_questions")
        operation = item.get("operation") or INSERT_SLIDE_AFTER  # older plans had no operation field
        edits.append(
            SlideEdit(
                topic_id=report.topic_id,
                after_slide_id=anchor,
                title=str(item.get("title") or "").strip(),
                bullets=[str(b).strip() for b in bullets] if isinstance(bullets, list) else [],
                addresses_questions=[str(q).strip() for q in questions] if isinstance(questions, list) else [],
                operation=str(operation),
                gap_type=report.gap_type,
                report_confidence=report.confidence,
            )
        )
    return edits


class EditPlanner:
    def __init__(self, generator: EditGenerator, min_confidence: float = MIN_REPORT_CONFIDENCE, max_edits_per_topic: int = MAX_EDITS_PER_TOPIC):
        self.generator = generator
        self.min_confidence = min_confidence
        self.max_edits_per_topic = max_edits_per_topic

    def plan_topic(self, report, dossier, deck) -> List[SlideEdit]:
        if skip_reason(report, dossier, self.min_confidence) is not None:
            return []
        system = SYSTEM_PROMPT.format(max_edits=self.max_edits_per_topic)
        raw = self.generator.generate(system, build_user_prompt(report, dossier, deck))
        edits = parse_edits(raw, report)[: self.max_edits_per_topic]
        known = [m.question for m in dossier.matched_questions]
        for edit in edits:
            edit.addresses_questions = snap_to_known_questions(edit.addresses_questions, known)
            anchor = deck.get(edit.after_slide_id)
            if edit.operation == APPEND_BULLETS and anchor is not None:
                edit.title = anchor.title or ""  # the slide keeps its own title; recorded for the change log
        return edits
