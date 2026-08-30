"""SeverityScorer abstraction + DefaultSeverityScorer.

Same DI posture as every prior module: swapping the severity formula
later means writing one new class here, not touching report_builder.py
or aggregation.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from math import log1p

GAP_TYPE_WEIGHT = {
    "complete_omission": 3.0,
    "fragmented_context": 2.0,
    "shallow_coverage": 1.0,
    "covered": 0.0,
}


class SeverityScorer(ABC):
    @abstractmethod
    def score(self, verdict) -> float:
        """`verdict` is a Gap_Verification GapVerdict, duck-typed
        (gap_type, backed_by_questions, confidence) -- this module
        never imports Gap_Verification directly."""
        raise NotImplementedError


class DefaultSeverityScorer(SeverityScorer):
    """First-version formula — explicit and documented, not an implicit
    ordering buried in sort logic. Treat this as tunable, not final
    (claude.md Section 5.1 flags it for review once real GapVerdict
    data exists — it now does, see run_reporting.py's real-data run).

        severity = GAP_TYPE_WEIGHT[gap_type] * log1p(backed_by_questions) * confidence

    Why each factor:
    - GAP_TYPE_WEIGHT sets the base severity tier (an omission is
      worse than shallow coverage, categorically).
    - log1p(backed_by_questions) rewards a gap backed by more student
      questions, but sub-linearly — a 51-question cluster should
      outrank a 3-question one, but shouldn't make a smaller, more
      severe gap (e.g. a genuine complete_omission with only 8
      questions) invisible by comparison. log1p also keeps a
      backed_by_questions=1 singleton's factor comfortably above zero
      (log1p(1)=0.69), rather than a plain log() sending it toward
      -inf.
    - Multiplying by confidence means the ambiguous windowing cases
      Gap_Verification's own confidence-dampening flags (e.g. the
      "technical debt" case — thin window, high backing) naturally
      rank lower here too, rather than a shaky classification
      overstating its own certainty by outranking a gap the model was
      actually sure about.
    """

    def score(self, verdict) -> float:
        weight = GAP_TYPE_WEIGHT.get(verdict.gap_type, 0.0)
        return weight * log1p(verdict.backed_by_questions) * verdict.confidence
