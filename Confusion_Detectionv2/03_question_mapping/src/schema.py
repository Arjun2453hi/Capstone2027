"""schema.py — this stage's output contract.

Topic-centric on purpose (topic -> question direction, per the project
root CLAUDE.md's rebuild rationale) -- but a question can legitimately
appear under more than one topic's matched_questions (this isn't a
clustering assignment), and a question matching nothing above threshold
is never silently dropped: it goes into unmatched_questions instead
(the same "never drop noise" rule Global_Context established in the old
build, carried forward here).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class QuestionMatch:
    question: str
    score: float
    method: str  # "semantic" | "llm_fallback"


@dataclass
class TopicMapping:
    topic_id: int
    start_slide_id: int
    end_slide_id: int
    slide_ids: List[int] = field(default_factory=list)
    boundary_confidence: float = 0.0
    matched_questions: List[QuestionMatch] = field(default_factory=list)


@dataclass
class UnmatchedQuestion:
    question: str
    best_score: float
    best_topic_id: Optional[int]


@dataclass
class MappingResult:
    generated_at: str
    total_questions: int
    total_topics: int
    high_threshold: float
    low_threshold: float
    topics: List[TopicMapping]
    unmatched_questions: List[UnmatchedQuestion]
