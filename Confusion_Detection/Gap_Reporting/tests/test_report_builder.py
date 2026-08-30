"""ReportBuilder orchestration tests: GapReport metadata, sort order,
DI (never a concrete scorer/aggregator hardcoded)."""
from __future__ import annotations

from ..aggregation import SimpleAggregator, SlideRangeAggregator
from ..report_builder import ReportBuilder
from ..schema import ReportEntry
from ..severity import DefaultSeverityScorer, SeverityScorer
from .fixtures.sample_verdicts import ALL_VERDICTS, COVERED_VERDICTS, REAL_CLUSTER_VERDICTS, SINGLETON_VERDICTS, make_deck


def test_total_topics_considered_counts_every_input_verdict_including_covered():
    builder = ReportBuilder(DefaultSeverityScorer(), SimpleAggregator(), make_deck())
    report = builder.build(ALL_VERDICTS)
    assert report.total_topics_considered == len(ALL_VERDICTS)


def test_total_gaps_reported_excludes_covered():
    builder = ReportBuilder(DefaultSeverityScorer(), SimpleAggregator(), make_deck())
    report = builder.build(ALL_VERDICTS)
    assert report.total_gaps_reported == len(REAL_CLUSTER_VERDICTS) + len(SINGLETON_VERDICTS)
    assert report.total_gaps_reported == len(report.entries)
    assert report.total_gaps_reported < len(ALL_VERDICTS)  # proves covered really got dropped
    assert len(COVERED_VERDICTS) > 0  # sanity: the fixture actually has covered verdicts to drop


def test_entries_sorted_by_severity_descending():
    builder = ReportBuilder(DefaultSeverityScorer(), SimpleAggregator(), make_deck())
    report = builder.build(ALL_VERDICTS)
    severities = [e.severity for e in report.entries]
    assert severities == sorted(severities, reverse=True)


def test_module_grouping_available_false_by_default():
    builder = ReportBuilder(DefaultSeverityScorer(), SimpleAggregator(), make_deck(with_modules=False))
    report = builder.build(REAL_CLUSTER_VERDICTS)
    assert report.module_grouping_available is False


def test_module_grouping_available_true_when_deck_has_modules():
    builder = ReportBuilder(DefaultSeverityScorer(), SimpleAggregator(), make_deck(with_modules=True))
    report = builder.build(REAL_CLUSTER_VERDICTS)
    assert report.module_grouping_available is True


def test_generated_at_is_a_real_iso_timestamp():
    from datetime import datetime

    builder = ReportBuilder(DefaultSeverityScorer(), SimpleAggregator(), make_deck())
    report = builder.build(REAL_CLUSTER_VERDICTS)
    datetime.fromisoformat(report.generated_at)  # raises if malformed


def test_builder_depends_on_interfaces_not_concrete_classes():
    # A custom scorer/aggregator must work with no changes to
    # ReportBuilder -- the actual proof that the DI works, not just a
    # claim in a docstring.
    class AlwaysOneScorer(SeverityScorer):
        def score(self, verdict) -> float:
            return 1.0

    class KeepAllAggregator(SlideRangeAggregator):
        # Deliberately does NOT exclude "covered" -- proves ReportBuilder
        # doesn't itself hardcode that rule; it's the aggregator's job.
        def aggregate(self, verdicts, deck, scorer):
            from ..aggregation import _slide_range_label

            return [
                ReportEntry(
                    slide_ids=list(v.slide_ids),
                    slide_range_label=_slide_range_label(v.slide_ids, deck),
                    module_id=None,
                    topic_id=v.topic_id,
                    gap_type=v.gap_type,
                    severity=scorer.score(v),
                    guidance=v.guidance,
                    suggested_addition=v.suggested_addition,
                    backed_by_questions=v.backed_by_questions,
                    confidence=v.confidence,
                    is_noise=v.is_noise,
                )
                for v in verdicts
            ]

    builder = ReportBuilder(AlwaysOneScorer(), KeepAllAggregator(), make_deck())
    report = builder.build(REAL_CLUSTER_VERDICTS + COVERED_VERDICTS)
    assert all(e.severity == 1.0 for e in report.entries)
    assert len(report.entries) == len(REAL_CLUSTER_VERDICTS) + len(COVERED_VERDICTS)
