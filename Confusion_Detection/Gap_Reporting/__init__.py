"""Gap_Reporting — Step 5: Hierarchical Gap Reporting, the final stage
of the 5-step Gap Detection pipeline.

Turns GapVerdicts (Gap_Verification's output) into the actual
deliverable: a severity-scored report grouped Module -> Slide Range,
with actionable revision guidance. Whether a Step 6 (auto-revised deck)
ever gets built doesn't change this folder's job — it produces the
report, nothing more (see claude.md Section 1).
"""
from .aggregation import SimpleAggregator, SlideRangeAggregator
from .renderers import JSONRenderer, MarkdownRenderer, ReportRenderer
from .report_builder import ReportBuilder
from .schema import GapReport, ReportEntry
from .severity import DefaultSeverityScorer, SeverityScorer

__all__ = [
    "SlideRangeAggregator",
    "SimpleAggregator",
    "ReportRenderer",
    "JSONRenderer",
    "MarkdownRenderer",
    "ReportBuilder",
    "ReportEntry",
    "GapReport",
    "SeverityScorer",
    "DefaultSeverityScorer",
]
