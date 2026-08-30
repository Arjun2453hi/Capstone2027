"""SimpleAggregator tests: covered-exclusion, slide-range labeling,
module_id resolution."""
from __future__ import annotations

from ..aggregation import SimpleAggregator
from ..severity import DefaultSeverityScorer
from .fixtures.sample_verdicts import (
    ALL_VERDICTS,
    COVERED_VERDICTS,
    REAL_CLUSTER_VERDICTS,
    SINGLETON_VERDICTS,
    make_deck,
)


def test_covered_verdicts_excluded_entirely():
    aggregator = SimpleAggregator()
    entries = aggregator.aggregate(ALL_VERDICTS, make_deck(), DefaultSeverityScorer())
    entry_topic_ids = {e.topic_id for e in entries}
    covered_topic_ids = {v.topic_id for v in COVERED_VERDICTS}
    assert entry_topic_ids.isdisjoint(covered_topic_ids)


def test_one_entry_per_non_covered_verdict_no_merging():
    aggregator = SimpleAggregator()
    entries = aggregator.aggregate(ALL_VERDICTS, make_deck(), DefaultSeverityScorer())
    expected_count = len(REAL_CLUSTER_VERDICTS) + len(SINGLETON_VERDICTS)
    assert len(entries) == expected_count


def test_slide_range_label_uses_slide_number_not_slide_id():
    # slide_ids [269, 270, 271] -> slide_numbers [270, 271, 272] (slide_number = slide_id + 1)
    aggregator = SimpleAggregator()
    entries = aggregator.aggregate(REAL_CLUSTER_VERDICTS, make_deck(), DefaultSeverityScorer())
    entry = next(e for e in entries if e.topic_id == 2)
    print(f"slide_ids={entry.slide_ids} -> slide_range_label={entry.slide_range_label!r}")
    assert entry.slide_range_label == "Slides 270-272"


def test_single_slide_range_label_has_no_dash():
    # COVERED_VERDICTS are excluded from aggregate()'s output entirely,
    # so this exercises _slide_range_label directly for the
    # single-slide formatting path instead.
    from ..aggregation import _slide_range_label

    label = _slide_range_label([200], make_deck())
    assert label == "Slide 201"


def test_module_id_none_when_deck_has_no_module_grouping():
    aggregator = SimpleAggregator()
    deck = make_deck(with_modules=False)
    entries = aggregator.aggregate(REAL_CLUSTER_VERDICTS, deck, DefaultSeverityScorer())
    assert all(e.module_id is None for e in entries)


def test_module_id_resolved_when_deck_has_module_grouping():
    aggregator = SimpleAggregator()
    deck = make_deck(with_modules=True)
    entries = aggregator.aggregate(REAL_CLUSTER_VERDICTS, deck, DefaultSeverityScorer())
    topic1 = next(e for e in entries if e.topic_id == 1)  # slide_ids [40, 41] -> module 3
    topic2 = next(e for e in entries if e.topic_id == 2)  # slide_ids [269,270,271] -> module 7
    assert topic1.module_id == 3
    assert topic2.module_id == 7


def test_missing_slide_id_degrades_gracefully_instead_of_crashing():
    from dataclasses import replace

    aggregator = SimpleAggregator()
    deck = make_deck()
    bogus = replace(REAL_CLUSTER_VERDICTS[0], slide_ids=[99999])
    entries = aggregator.aggregate([bogus], deck, DefaultSeverityScorer())
    assert len(entries) == 1
    assert entries[0].slide_range_label  # produced something, didn't crash
