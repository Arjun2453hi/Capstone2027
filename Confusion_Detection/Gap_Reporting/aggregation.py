"""SlideRangeAggregator abstraction + SimpleAggregator.

Same DI posture as every prior module: report_builder.py depends on
this interface, never SimpleAggregator directly.

Design note on the interface signature: claude.md Section 3 states
`aggregate(verdicts: List[GapVerdict]) -> List[ReportEntry]`, but
producing a real ReportEntry requires resolving slide_ids into a
human-readable slide_range_label (needs the DeckDocument) and computing
severity (needs a SeverityScorer) — genuinely part of what "aggregate"
must do to satisfy its own declared return type. Both are passed in
explicitly here rather than smuggled in through a constructor, because
a future MergingAggregator needs the scorer at aggregation time too: if
it merges two verdicts into one entry, it — not report_builder.py — is
the thing that knows how to combine their two severities (max? sum?
recompute from merged backing?), and that decision belongs where the
merge happens.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from .schema import ReportEntry
from .severity import SeverityScorer


def _slide_number(slide_id: int, deck) -> int:
    slide = deck.get(slide_id)
    # Fall back to slide_id+1 (the usual slide_number convention) if a
    # verdict somehow references a slide_id the deck doesn't have —
    # degrade gracefully rather than crash the whole report.
    return slide.slide_number if slide is not None else slide_id + 1


def _slide_range_label(slide_ids: List[int], deck) -> str:
    if not slide_ids:
        return "(no slides)"
    numbers = sorted(_slide_number(sid, deck) for sid in slide_ids)
    if numbers[0] == numbers[-1]:
        return f"Slide {numbers[0]}"
    return f"Slides {numbers[0]}-{numbers[-1]}"


def _resolve_module_id(slide_ids: List[int], deck) -> Optional[int]:
    """None until Step 1's module grouper exists (module_id is null on
    every slide today) — this just reads whatever's there, so it starts
    working automatically the day that grouper ships, no change needed
    here."""
    module_ids = set()
    for sid in slide_ids:
        slide = deck.get(sid)
        if slide is not None and slide.module_id is not None:
            module_ids.add(slide.module_id)
    if not module_ids:
        return None
    return min(module_ids)  # lowest module_id if a window spans more than one (rare, near a boundary)


class SlideRangeAggregator(ABC):
    @abstractmethod
    def aggregate(self, verdicts, deck, scorer: SeverityScorer) -> List[ReportEntry]:
        """`verdicts` are Gap_Verification GapVerdicts, duck-typed —
        this module never imports Gap_Verification directly. Must
        exclude `covered` verdicts entirely (not a gap)."""
        raise NotImplementedError


class SimpleAggregator(SlideRangeAggregator):
    """v1: one ReportEntry per non-covered verdict. No merging of
    overlapping/adjacent slide ranges across different topics.

    Known limitation, intentional for v1 (claude.md Section 3 and
    Section 5.3): if two different topics both implicate slide 12,
    they appear as two separate entries rather than one merged "slide
    12" entry. A correct merge has to decide how to combine two
    different gap_types/severities/guidance texts into one coherent
    entry — a real design question, deferred rather than guessed at
    preemptively. A future MergingAggregator can implement it behind
    this same interface without touching report_builder.py.
    """

    def aggregate(self, verdicts, deck, scorer: SeverityScorer) -> List[ReportEntry]:
        entries = []
        for verdict in verdicts:
            if verdict.gap_type == "covered":
                continue
            entries.append(
                ReportEntry(
                    slide_ids=list(verdict.slide_ids),
                    slide_range_label=_slide_range_label(verdict.slide_ids, deck),
                    module_id=_resolve_module_id(verdict.slide_ids, deck),
                    topic_id=verdict.topic_id,
                    gap_type=verdict.gap_type,
                    severity=scorer.score(verdict),
                    guidance=verdict.guidance,
                    suggested_addition=verdict.suggested_addition,
                    backed_by_questions=verdict.backed_by_questions,
                    confidence=verdict.confidence,
                    is_noise=verdict.is_noise,
                )
            )
        return entries
