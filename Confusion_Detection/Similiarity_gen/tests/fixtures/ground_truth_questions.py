"""Synthetic ground-truth fixture for numeric similarity testing.

No real PDF exists yet for this package's own tests (Step 1's real
fixture, tests/fixtures/sample_deck.pdf, belongs to gap_detection's own
test suite once that's written), so this builds a small DeckDocument
in memory instead — deliberately covering distinct, non-overlapping
topics so a *correct* embedding model has a real signal to find, and
including the same edge cases claude.md calls out (a title-only slide,
a fully blank slide).
"""
from __future__ import annotations

from ... import _paths  # noqa: F401  (side effect: puts gap_detection on sys.path)

from gap_detection.parsing.schema import DeckDocument, Slide

SLIDES = [
    Slide(
        slide_id=0,
        slide_number=1,
        title="Unit 2: Mocking Dependencies",
        bullets=[
            "Mocking replaces real dependencies with test doubles",
            "Used to isolate the unit under test from external systems",
        ],
    ),
    Slide(
        slide_id=1,
        slide_number=2,
        title="Stubs vs Spies",
        bullets=[
            "A stub returns canned answers to calls made during the test",
            "A spy records how it was called, for verification afterward",
        ],
    ),
    Slide(
        slide_id=2,
        slide_number=3,
        title="Test-Driven Development",
        bullets=[
            "Red: write a failing test first",
            "Green: write the minimal code needed to make it pass",
            "Refactor: clean up the code without changing behavior",
        ],
    ),
    Slide(
        slide_id=3,
        slide_number=4,
        title="RACI Matrix",
        bullets=[
            "Responsible: the person who does the work",
            "Accountable: owns the outcome; exactly one person per task",
            "Consulted: two-way input sought before a decision",
            "Informed: one-way notification after a decision is made",
        ],
    ),
    Slide(
        slide_id=4,
        slide_number=5,
        title="COCOMO Estimation",
        bullets=[
            "Effort = a * (KLOC)^b person-months",
            "Development Time = c * (Effort)^d months",
            "Three project types: organic, semi-detached, embedded",
        ],
    ),
    Slide(
        slide_id=5,
        slide_number=6,
        title="Critical Path Method",
        bullets=[
            "The critical path is the longest sequence of dependent activities",
            "Activities on the critical path have zero slack time",
        ],
    ),
    Slide(
        slide_id=6,
        slide_number=7,
        title="Microservices Architecture",
        bullets=[
            "Independently deployable services, each owning its own data",
            "Communicate over lightweight protocols such as REST or gRPC",
        ],
    ),
    Slide(
        slide_id=7,
        slide_number=8,
        title="Questions?",
        bullets=[],  # edge case: title-only slide
    ),
    Slide(
        slide_id=8,
        slide_number=9,
        title=None,
        bullets=[],  # edge case: fully blank slide, must be skipped by SlideIndex
    ),
    Slide(
        slide_id=9,
        slide_number=10,
        title="Technical Debt",
        bullets=[
            "Shortcuts taken now create interest that must be paid later",
            "Classified as reckless vs prudent, and deliberate vs inadvertent",
        ],
    ),
]

DECK = DeckDocument(source_pdf="ground_truth_fixture.pdf", num_pages=len(SLIDES), slides=SLIDES)

GROUND_TRUTH = [
    {"question": "What is mocking used for in testing?", "expected_slide_id": 0},
    {"question": "What's the difference between a stub and a spy?", "expected_slide_id": 1},
    {"question": "What are the three phases of the TDD Red-Green-Refactor cycle?", "expected_slide_id": 2},
    {"question": "Why can only one person be Accountable in a RACI matrix?", "expected_slide_id": 3},
    {"question": "What is the COCOMO formula for estimating effort?", "expected_slide_id": 4},
    {"question": "Why do activities on the critical path have zero slack time?", "expected_slide_id": 5},
    {"question": "What are the core characteristics of microservices architecture?", "expected_slide_id": 6},
    {"question": "How is technical debt like financial debt?", "expected_slide_id": 9},
]

# Should not score well against any slide — used to check the model
# doesn't just score everything similarly high on short texts.
HARD_NEGATIVE_QUESTION = "What's the best pizza topping combination?"
