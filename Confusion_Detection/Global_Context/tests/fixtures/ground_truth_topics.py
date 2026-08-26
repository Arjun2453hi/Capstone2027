"""Ground-truth fixture for numeric clustering evaluation.

14 questions: 4 real topics (3 phrasings each, drawn from recurring
concepts in the actual u2_questions.txt set so the fixture reflects
real phrasing variety) plus 2 deliberately unrelated singleton
outliers. TRUE_LABELS gives each outlier its own distinct label (not
shared with each other) so Adjusted Rand Index correctly penalizes a
run that merges them with anything, including each other.
"""
from __future__ import annotations

QUESTIONS = [
    # Topic 0 — RACI: Responsible vs Accountable
    "Why can only one person be Accountable in a RACI matrix?",
    "What is the difference between Responsible and Accountable in a RACI matrix?",
    "In a RACI matrix, why must exactly one person be marked Accountable for a task?",
    # Topic 1 — TDD Red-Green-Refactor
    "What are the three phases of the Red-Green-Refactor cycle in TDD?",
    "Explain the TDD mantra: Red, Green, Refactor.",
    "Why must a test fail before you write any implementation in TDD?",
    # Topic 2 — Project management triangle
    "What is the project management triangle and what are its three constraints?",
    "How does scope, time, and cost interact in a software project?",
    "Why can't you have unlimited scope, minimal time, and low cost all at once?",
    # Topic 3 — Critical path method
    "Why do activities on the critical path have zero slack time?",
    "How do you identify the critical path in a project network?",
    "What is the difference between critical path activities and non-critical activities?",
    # Outliers — unrelated to the topics above AND to each other
    "What is the best pizza topping combination?",
    "How do you train a dog to sit?",
]

TRUE_LABELS = [0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 5]

OUTLIER_INDICES = [12, 13]

assert len(QUESTIONS) == len(TRUE_LABELS)
