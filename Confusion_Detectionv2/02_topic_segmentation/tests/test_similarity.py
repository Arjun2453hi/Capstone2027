"""Unit tests for the core depth-score computation, against synthetic
embedding sequences with known, constructed dips (claude.md Section 7)
-- deterministic, no real model needed."""
from __future__ import annotations

import numpy as np
import pytest

from ..src.similarity import block_similarity, combined_depth, cosine_similarity, depth_score
from .fixtures.synthetic_embeddings import (
    make_flat_embeddings,
    make_three_topic_embeddings,
    make_two_topic_embeddings,
)


def test_cosine_similarity_orthogonal_and_identical():
    assert cosine_similarity(np.array([1.0, 0.0]), np.array([0.0, 1.0])) == pytest.approx(0.0)
    assert cosine_similarity(np.array([1.0, 0.0]), np.array([1.0, 0.0])) == pytest.approx(1.0)


def test_block_similarity_detects_clean_boundary():
    embeddings, true_boundary = make_two_topic_embeddings(6, 6)
    sim = block_similarity(embeddings, k=2)
    print(f"sim at true boundary ({true_boundary}): {sim[true_boundary]}, mid-topic: {sim[1]}")
    assert sim[true_boundary] == pytest.approx(0.0, abs=1e-6)
    assert sim[1] == pytest.approx(1.0, abs=1e-6)  # well within topic A, blocks identical


def test_block_similarity_edge_handling_returns_nan_not_a_shrunk_window():
    embeddings, _ = make_two_topic_embeddings(6, 6)  # n=12
    sim = block_similarity(embeddings, k=5)
    assert np.isnan(sim[0])  # needs 5 slides before position 0 -- doesn't exist
    assert np.isnan(sim[3])  # needs 5 slides before position 3 -- doesn't exist (only 4)
    assert not np.isnan(sim[5])  # position 5: 5 before (0-4), 5 after (6-10) both exist (n=12)


def test_depth_score_peaks_exactly_at_the_true_boundary():
    embeddings, true_boundary = make_two_topic_embeddings(8, 8)
    sim = block_similarity(embeddings, k=2)
    depth = depth_score(sim, k=2)
    valid_positions = [i for i in range(len(depth)) if not np.isnan(depth[i])]
    best = max(valid_positions, key=lambda i: depth[i])
    print(f"depth curve: {depth}")
    print(f"predicted boundary={best}, true boundary={true_boundary}")
    assert best == true_boundary
    assert depth[true_boundary] > 0


def test_combined_depth_across_scales_still_finds_the_boundary():
    embeddings, true_boundary = make_two_topic_embeddings(10, 10)
    depth_by_scale = combined_depth(embeddings, scales=[2, 3, 4])
    stacked = np.vstack(list(depth_by_scale.values()))
    with np.errstate(all="ignore"):
        combined = np.nanmax(stacked, axis=0)
    valid_positions = [i for i in range(len(combined)) if not np.isnan(combined[i])]
    best = max(valid_positions, key=lambda i: combined[i])
    assert best == true_boundary


def test_three_topics_every_true_boundary_has_positive_depth():
    embeddings, boundaries = make_three_topic_embeddings((6, 6, 6))
    sim = block_similarity(embeddings, k=2)
    depth = depth_score(sim, k=2)
    for b in boundaries:
        print(f"boundary {b}: depth={depth[b]}")
        assert depth[b] > 0


def test_flat_signal_has_zero_depth_everywhere_valid():
    embeddings = make_flat_embeddings(10)
    sim = block_similarity(embeddings, k=2)
    depth = depth_score(sim, k=2)
    valid = depth[~np.isnan(depth)]
    print(f"flat-signal depth values: {valid}")
    assert np.allclose(valid, 0.0, atol=1e-6)
