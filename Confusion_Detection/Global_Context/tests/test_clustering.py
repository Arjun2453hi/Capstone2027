"""Unit tests for the Clusterer implementations in isolation (synthetic
blobs, not text) — separate from test_topology_builder.py's end-to-end
ARI evaluation on real text embeddings."""
from __future__ import annotations

import numpy as np
import pytest
from sklearn.datasets import make_blobs

from ..clustering import HDBSCANClusterer, KMeansClusterer


def _normalized_blobs():
    X, true_labels = make_blobs(
        n_samples=30, centers=3, cluster_std=0.5, random_state=42
    )
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    return X / norms, true_labels


def test_kmeans_selects_a_reasonable_k():
    X, true_labels = _normalized_blobs()
    clusterer = KMeansClusterer(k_range=range(2, 8))
    labels = clusterer.fit_predict(X)

    print(f"KMeans selected k={clusterer.selected_k_}, silhouette={clusterer.selected_silhouette_:.4f}")
    assert clusterer.selected_k_ == 3  # the blobs really do have 3 centers
    assert len(labels) == len(X)
    assert -1 not in labels  # KMeans has no noise concept


def test_kmeans_name_reflects_k_range():
    a = KMeansClusterer(k_range=range(2, 8))
    b = KMeansClusterer(k_range=range(2, 20))
    assert a.name != b.name


def test_hdbscan_finds_clusters_and_labels_outlier_as_noise():
    X, _ = _normalized_blobs()
    # Add one point far away from every blob — a clear outlier.
    outlier = np.array([[10.0] * X.shape[1]])
    X_with_outlier = np.vstack([X, outlier])

    clusterer = HDBSCANClusterer(min_cluster_size=3)
    labels = clusterer.fit_predict(X_with_outlier)

    print(f"HDBSCAN labels: {labels}")
    assert len(labels) == len(X_with_outlier)
    assert labels[-1] == -1, "the far-away point should be flagged as noise"
    assert len(set(labels) - {-1}) >= 2  # found at least 2 real clusters among the blobs


def test_hdbscan_name_reflects_min_cluster_size():
    a = HDBSCANClusterer(min_cluster_size=3)
    b = HDBSCANClusterer(min_cluster_size=5)
    assert a.name != b.name
