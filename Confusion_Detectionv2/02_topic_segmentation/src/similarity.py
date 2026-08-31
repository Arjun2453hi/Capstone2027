"""similarity.py — multi-scale block cosine similarity + depth score.

Pure geometric computation: knows nothing about slide content, empty
slides, or structural cues -- just embeddings in, per-position depth
scores out. Keeping this module free of any business rule (like
excluding a position for being adjacent to an empty slide, which is
segmenter.py's job) is what makes it independently unit-testable
against synthetic embedding sequences with known, constructed dips
(claude.md Section 7).

This is the primary logic driving segmentation (claude.md Section 2) --
everything else in this stage only adjusts its output slightly.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    norm_a, norm_b = np.linalg.norm(a), np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def block_similarity(embeddings: np.ndarray, k: int) -> np.ndarray:
    """sim_k(i) for every candidate boundary i (between slide i and
    slide i+1), i in [0, n-2]: cosine similarity between the mean
    embedding of the k slides ending at i and the mean embedding of the
    k slides starting at i+1.

    NaN wherever fewer than k slides exist on either side (claude.md
    Section 2's edge-handling rule) -- computed with a shrunk or padded
    window instead would trust a comparison that doesn't have enough
    context to mean the same thing as everywhere else on the curve.
    """
    n = len(embeddings)
    sim = np.full(n - 1, np.nan)
    for i in range(n - 1):
        left_start = i - k + 1
        right_end = i + 1 + k
        if left_start < 0 or right_end > n:
            continue
        left_mean = embeddings[left_start : i + 1].mean(axis=0)
        right_mean = embeddings[i + 1 : right_end].mean(axis=0)
        sim[i] = cosine_similarity(left_mean, right_mean)
    return sim


def depth_score(sim_k: np.ndarray, k: int) -> np.ndarray:
    """depth_k(i), peak search pinned to the same scale k being
    evaluated (claude.md Section 2.4):

        left_peak(i)  = max(sim_k(j) for j in [i-k, i))
        right_peak(i) = max(sim_k(j) for j in [i, i+k))
        depth_k(i) = (left_peak - sim_k(i)) + (right_peak - sim_k(i))

    NaN wherever the peak search needs a position outside sim_k's own
    valid domain, or a full k-sized window isn't available on either
    side -- this is a narrower valid range than sim_k's own (a position
    can have a defined sim_k value but still lack enough *neighboring*
    sim_k values to trust the peak search around it), and that's a
    correct consequence of the formula, not a separate rule invented
    here.
    """
    n = len(sim_k)
    depth = np.full(n, np.nan)
    for i in range(n):
        left_window = sim_k[max(0, i - k) : i]
        right_window = sim_k[i : min(n, i + k)]
        if len(left_window) < k or len(right_window) < k:
            continue
        if np.isnan(left_window).any() or np.isnan(right_window).any():
            continue
        left_peak = left_window.max()
        right_peak = right_window.max()
        depth[i] = (left_peak - sim_k[i]) + (right_peak - sim_k[i])
    return depth


def combined_depth(embeddings: np.ndarray, scales: List[int]) -> Dict[int, np.ndarray]:
    """Per-scale depth arrays -- {k: depth_k(i) array}. Callers combine
    via nanmax across scales (claude.md Section 2.5); kept separate here
    (rather than pre-combined) so tests and the visualization can
    inspect any one scale's contribution independently."""
    return {k: depth_score(block_similarity(embeddings, k), k) for k in scales}
