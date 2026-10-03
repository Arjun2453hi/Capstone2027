"""export.py — assembles the one final, self-contained JSON handed to
Stage 4 (04_gap_verification).

question_mapping.json is this stage's own faithful output (scores,
thresholds, method per match) -- useful for debugging this stage. This
file is a different, purpose-built artifact: one record per topic with
everything Stage 4 needs to reason about it -- slide range (both the
raw slide_ids and a human-readable label), the topic's own
representative content, and its matched questions -- so Stage 4 can
consume it directly without re-loading and cross-referencing Stage 1's
deck JSON or Stage 2's topics JSON itself. unmatched_questions is
carried through unchanged for traceability (same "never drop noise"
rule this stage's own schema.py already follows), even though Stage 4
has no direct use for it.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Union

from .mapper import topic_text
from .schema import MappingResult, QuestionMatch, UnmatchedQuestion

# Deliberately UNBOUNDED, unlike mapper.py's MAX_TOPIC_CHARS (1500) --
# that budget was tuned for Stage 3's own different use (embedding many
# topics at once + fitting several candidates into one LLM-fallback
# prompt, where compactness across candidates genuinely matters).
# window_text here feeds 04_gap_reporting_agent's get_topic_slides tool
# -- a single topic read once by one agent, not a multi-candidate
# prompt -- so completeness matters more than compactness, and a fixed
# budget can't actually guarantee completeness anyway: measured
# directly against the real deck, a 6000-char budget (tried first)
# silently dropped 10 of a real 22-slide topic's slides -- not just
# trailing boilerplate, but the topic's substantive middle content --
# because that topic's true total (12,494 chars) simply exceeded the
# budget. The largest real topic on this deck is 16,128 chars, well
# within any modern LLM's context for a single tool call -- there is no
# real constraint here that justifies truncating and risking another
# silent content gap.
GAP_INPUT_MAX_TOPIC_CHARS = None


@dataclass
class TopicDossier:
    topic_id: int
    slide_ids: List[int]
    slide_range: str
    boundary_confidence: float
    window_text: str
    cluster_size: int  # == len(matched_questions); named for continuity with the old build's "backed_by_questions"
    matched_questions: List[QuestionMatch] = field(default_factory=list)


@dataclass
class GapVerificationInput:
    generated_at: str
    source_deck: str
    total_topics: int
    total_questions: int
    topics: List[TopicDossier]
    unmatched_questions: List[UnmatchedQuestion]


def _slide_range_label(slide_ids: List[int], deck) -> str:
    """Human-readable range using real (1-indexed) slide numbers, e.g.
    "Slides 51-80" -- slide_ids are the internal 0-indexed identity,
    not what anyone reading a report should see."""
    if not slide_ids:
        return "(no slides)"
    numbers = []
    for sid in slide_ids:
        slide = deck.get(sid)
        numbers.append(slide.slide_number if slide is not None else sid + 1)
    numbers.sort()
    if numbers[0] == numbers[-1]:
        return f"Slide {numbers[0]}"
    return f"Slides {numbers[0]}-{numbers[-1]}"


def build_gap_verification_input(
    result: MappingResult, topics: List, deck, max_topic_chars: Optional[int] = GAP_INPUT_MAX_TOPIC_CHARS
) -> GapVerificationInput:
    topics_by_id = {t.topic_id: t for t in topics}
    dossiers = []
    for tm in result.topics:
        topic = topics_by_id[tm.topic_id]
        dossiers.append(
            TopicDossier(
                topic_id=tm.topic_id,
                slide_ids=list(topic.slide_ids),
                slide_range=_slide_range_label(topic.slide_ids, deck),
                boundary_confidence=topic.boundary_confidence,
                window_text=topic_text(topic, deck, max_topic_chars),
                cluster_size=len(tm.matched_questions),
                matched_questions=list(tm.matched_questions),
            )
        )
    return GapVerificationInput(
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_deck=deck.source_pdf,
        total_topics=result.total_topics,
        total_questions=result.total_questions,
        topics=dossiers,
        unmatched_questions=list(result.unmatched_questions),
    )


def save_gap_verification_input_json(data: GapVerificationInput, path: Union[str, Path]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(asdict(data), f, indent=2, ensure_ascii=False)


def load_gap_verification_input_json(path: Union[str, Path]) -> GapVerificationInput:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    dossiers = [
        TopicDossier(
            topic_id=t["topic_id"],
            slide_ids=t["slide_ids"],
            slide_range=t["slide_range"],
            boundary_confidence=t["boundary_confidence"],
            window_text=t["window_text"],
            cluster_size=t["cluster_size"],
            matched_questions=[QuestionMatch(**q) for q in t["matched_questions"]],
        )
        for t in raw["topics"]
    ]
    unmatched = [UnmatchedQuestion(**u) for u in raw["unmatched_questions"]]
    return GapVerificationInput(
        generated_at=raw["generated_at"],
        source_deck=raw["source_deck"],
        total_topics=raw["total_topics"],
        total_questions=raw["total_questions"],
        topics=dossiers,
        unmatched_questions=unmatched,
    )
