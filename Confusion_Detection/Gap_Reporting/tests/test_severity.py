"""Severity ordering tests — pin down actual expected orderings from
the formula, not just "some order exists" (claude.md Section 4)."""
from __future__ import annotations

from math import log1p

import pytest

from ..severity import DefaultSeverityScorer
from .fixtures.sample_verdicts import REAL_CLUSTER_VERDICTS


@pytest.fixture
def scorer():
    return DefaultSeverityScorer()


def test_formula_matches_documented_definition(scorer):
    v = REAL_CLUSTER_VERDICTS[0]  # complete_omission, backed=8, confidence=0.85
    expected = 3.0 * log1p(8) * 0.85
    assert scorer.score(v) == pytest.approx(expected)


def test_modest_complete_omission_outranks_heavy_shallow_coverage(scorer):
    # topic 1: complete_omission, backed_by=8, confidence=0.85
    # topic 2: shallow_coverage, backed_by=51 (6x the backing), confidence=0.6
    # Despite far less backing, the higher gap-type tier should still win.
    omission = next(v for v in REAL_CLUSTER_VERDICTS if v.topic_id == 1)
    heavy_shallow = next(v for v in REAL_CLUSTER_VERDICTS if v.topic_id == 2)
    print(f"omission severity={scorer.score(omission):.4f}, heavy shallow severity={scorer.score(heavy_shallow):.4f}")
    assert scorer.score(omission) > scorer.score(heavy_shallow)


def test_heavy_shallow_coverage_outranks_modest_fragmented_context(scorer):
    # topic 2: shallow_coverage, backed_by=51, confidence=0.6
    # topic 3: fragmented_context (higher weight tier than shallow), backed_by=15, confidence=0.4
    # Here backing+confidence should compensate for the lower tier --
    # the "vice versa" direction claude.md Section 4 asks to pin down.
    heavy_shallow = next(v for v in REAL_CLUSTER_VERDICTS if v.topic_id == 2)
    modest_fragmented = next(v for v in REAL_CLUSTER_VERDICTS if v.topic_id == 3)
    print(f"heavy shallow severity={scorer.score(heavy_shallow):.4f}, modest fragmented severity={scorer.score(modest_fragmented):.4f}")
    assert scorer.score(heavy_shallow) > scorer.score(modest_fragmented)


def test_full_ordering_of_the_four_real_cluster_verdicts(scorer):
    ordered = sorted(REAL_CLUSTER_VERDICTS, key=lambda v: -scorer.score(v))
    ordered_topic_ids = [v.topic_id for v in ordered]
    print(f"severity order (topic_ids, highest first): {ordered_topic_ids}")
    print(f"scores: {[round(scorer.score(v), 4) for v in ordered]}")
    assert ordered_topic_ids == [1, 2, 3, 4]


def test_covered_always_scores_zero(scorer):
    from .fixtures.sample_verdicts import COVERED_VERDICTS

    for v in COVERED_VERDICTS:
        assert scorer.score(v) == 0.0


def test_singleton_backed_by_one_scores_above_zero(scorer):
    # log1p(1) = ln(2) ~ 0.693, not zero -- a singleton is still a real
    # signal, per Global_Context's "never drop noise" rule carried
    # through the whole pipeline.
    from .fixtures.sample_verdicts import SINGLETON_VERDICTS

    for v in SINGLETON_VERDICTS:
        assert scorer.score(v) > 0.0
