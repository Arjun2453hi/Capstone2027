"""ReportBuilder — the orchestrator for this folder.

Depends on SeverityScorer + SlideRangeAggregator + a DeckDocument,
never a concrete scorer/aggregator — same DI posture as every prior
module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from .aggregation import SlideRangeAggregator
from .schema import GapReport
from .severity import SeverityScorer


class ReportBuilder:
    def __init__(self, scorer: SeverityScorer, aggregator: SlideRangeAggregator, deck):
        self.scorer = scorer
        self.aggregator = aggregator
        self.deck = deck

    def build(self, verdicts: List) -> GapReport:
        """`verdicts` are Gap_Verification GapVerdicts, duck-typed —
        this module never imports Gap_Verification directly."""
        entries = self.aggregator.aggregate(verdicts, self.deck, self.scorer)
        entries.sort(key=lambda e: -e.severity)

        return GapReport(
            generated_at=datetime.now(timezone.utc).isoformat(),
            total_topics_considered=len(verdicts),
            total_gaps_reported=len(entries),
            module_grouping_available=any(e.module_id is not None for e in entries),
            entries=entries,
        )
