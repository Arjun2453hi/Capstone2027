"""Deterministic embedding sequences with known, constructed dips
(claude.md Section 7) -- lets similarity.py be unit-tested without a
real embedding model."""
from __future__ import annotations

from typing import List, Tuple

import numpy as np


def make_two_topic_embeddings(topic_a_len: int = 6, topic_b_len: int = 6, dim: int = 4) -> Tuple[np.ndarray, int]:
    """Two orthogonal unit vectors, one per topic block -- a clean,
    exactly-predictable dip at the true boundary (cosine=0 across
    topics, cosine=1 within a topic). Returns (embeddings, true_boundary_index)."""
    a = np.zeros(dim)
    a[0] = 1.0
    b = np.zeros(dim)
    b[1] = 1.0
    embeddings = np.vstack([a] * topic_a_len + [b] * topic_b_len)
    true_boundary = topic_a_len - 1  # boundary between last slide of A and first of B
    return embeddings, true_boundary


def make_three_topic_embeddings(lens: Tuple[int, int, int] = (6, 6, 6), dim: int = 4) -> Tuple[np.ndarray, List[int]]:
    vectors = []
    for i, length in enumerate(lens):
        v = np.zeros(dim)
        v[i] = 1.0
        vectors.extend([v] * length)
    embeddings = np.vstack(vectors)
    boundaries, pos = [], 0
    for length in lens[:-1]:
        pos += length
        boundaries.append(pos - 1)
    return embeddings, boundaries


def make_flat_embeddings(n: int = 10, dim: int = 4) -> np.ndarray:
    """All identical -- zero content signal anywhere."""
    v = np.zeros(dim)
    v[0] = 1.0
    return np.vstack([v] * n)
