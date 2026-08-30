"""Hand-authored GapVerdict-shaped fixtures mimicking the real
distribution (Gap_Verification didn't exist yet when this folder's
claude.md was written — it does now, but this fixture stays as the
fast, deterministic unit-test baseline; the real 445-verdict output is
exercised separately via run_reporting.py).

Verdicts are plain dataclass stand-ins (not Gap_Verification.schema.GapVerdict)
so this folder's tests don't need to import Gap_Verification — same
one-way-dependency posture as every other test suite in this project.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ... import _paths  # noqa: F401  (side effect: puts DocumentParsing on sys.path)

from gap_detection.parsing.schema import DeckDocument, Slide


@dataclass
class FakeVerdict:
    topic_id: int
    gap_type: str
    slide_ids: List[int]
    guidance: str
    suggested_addition: Optional[str]
    confidence: float
    backed_by_questions: int
    is_noise: bool


# Four "real cluster" verdicts, sizes/confidences chosen so severity
# ordering isn't a straight function of gap_type alone -- see
# test_severity.py for the exact expected order and why.
REAL_CLUSTER_VERDICTS = [
    FakeVerdict(
        topic_id=1,
        gap_type="complete_omission",
        slide_ids=[40, 41],
        guidance="The deck never addresses this topic.",
        suggested_addition="Add a slide covering X.",
        confidence=0.85,
        backed_by_questions=8,
        is_noise=False,
    ),
    FakeVerdict(
        topic_id=2,
        gap_type="shallow_coverage",
        slide_ids=[269, 270, 271],
        guidance="Only the title is present; no real explanation.",
        suggested_addition=None,
        confidence=0.6,
        backed_by_questions=51,  # mirrors the real "technical debt" cluster size
        is_noise=False,
    ),
    FakeVerdict(
        topic_id=3,
        gap_type="fragmented_context",
        slide_ids=[100, 101, 102],
        guidance="Explanation may continue outside this window.",
        suggested_addition="Add a summary bullet tying the pieces together.",
        confidence=0.4,
        backed_by_questions=15,
        is_noise=False,
    ),
    FakeVerdict(
        topic_id=4,
        gap_type="shallow_coverage",
        slide_ids=[10, 11, 12],
        guidance="Mentions the topic but doesn't explain it.",
        suggested_addition="Add a worked example.",
        confidence=0.9,
        backed_by_questions=5,
        is_noise=False,
    ),
]

# Must never appear in a GapReport's entries.
COVERED_VERDICTS = [
    FakeVerdict(
        topic_id=5,
        gap_type="covered",
        slide_ids=[30, 31],
        guidance="This topic appears adequately covered.",
        suggested_addition=None,
        confidence=0.75,
        backed_by_questions=30,
        is_noise=False,
    ),
    FakeVerdict(
        topic_id=6,
        gap_type="covered",
        slide_ids=[200],
        guidance="This topic appears adequately covered.",
        suggested_addition=None,
        confidence=0.5,
        backed_by_questions=10,
        is_noise=False,
    ),
]

# 20 singletons -- deliberately more than MAX_SINGLETON_ENTRIES_SHOWN
# (15) so the display-cap test has something to cap. Slide ids spread
# out and mostly non-overlapping with the real-cluster verdicts above.
SINGLETON_VERDICTS = [
    FakeVerdict(
        topic_id=100 + i,
        gap_type="shallow_coverage" if i % 2 == 0 else "complete_omission",
        slide_ids=[150 + i],
        guidance=f"Singleton finding #{i}.",
        suggested_addition=None,
        confidence=0.3 + (i % 5) * 0.05,  # spread of low-ish confidences
        backed_by_questions=1,
        is_noise=True,
    )
    for i in range(20)
]

ALL_VERDICTS = REAL_CLUSTER_VERDICTS + COVERED_VERDICTS + SINGLETON_VERDICTS


def make_deck(with_modules: bool = False) -> DeckDocument:
    """A synthetic deck covering every slide_id referenced above, so
    slide_range_label resolution has something real to look up.
    `with_modules=True` sets module_id on a couple of slide ranges, to
    exercise the "module grouping available" path (real data never
    does, since Step 1's grouper isn't built -- see the module-fallback
    test for the default, always-null case).
    """
    max_slide_id = max(sid for v in ALL_VERDICTS for sid in v.slide_ids)
    slides = []
    for slide_id in range(max_slide_id + 1):
        module_id = None
        if with_modules:
            if slide_id in (40, 41):
                module_id = 3
            elif slide_id in (269, 270, 271):
                module_id = 7
        slides.append(
            Slide(
                slide_id=slide_id,
                slide_number=slide_id + 1,
                title=f"Slide {slide_id} title",
                bullets=["some content"],
                module_id=module_id,
            )
        )
    return DeckDocument(source_pdf="fixture_deck.pdf", num_pages=len(slides), slides=slides)
