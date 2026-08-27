"""ClassificationResult, GeneratedContent, GapVerdict — this folder's
internal and output contracts.

GapVerdict is Step 5's actual input — cluster_size and is_noise are
carried straight through from Context_Window's ContextBundle unchanged,
never recomputed, so this folder is not where traceability finally
breaks (see claude.md Section 4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

GAP_TYPES = ("complete_omission", "shallow_coverage", "fragmented_context", "covered")


@dataclass
class ClassificationResult:
    gap_type: str
    confidence: float  # the model's own label probability -- never an invented heuristic
    label_scores: Dict[str, float] = field(default_factory=dict)  # full distribution, for confusion-matrix debugging

    def __post_init__(self):
        if self.gap_type not in GAP_TYPES:
            raise ValueError(f"gap_type must be one of {GAP_TYPES}, got {self.gap_type!r}")


@dataclass
class GeneratedContent:
    guidance: str
    suggested_addition: Optional[str]  # null when not confident enough, or degenerate raw output


@dataclass
class GapVerdict:
    topic_id: int
    gap_type: str
    slide_ids: List[int]  # = bundle.window_slide_ids, carried through
    guidance: str
    suggested_addition: Optional[str]
    confidence: float  # classifier confidence, adjusted for known data-quality caveats -- see verifier.py
    backed_by_questions: int  # = bundle.cluster_size
    is_noise: bool  # carried through -- Step 5 needs this for severity weighting

    def __post_init__(self):
        if self.gap_type not in GAP_TYPES:
            raise ValueError(f"gap_type must be one of {GAP_TYPES}, got {self.gap_type!r}")
