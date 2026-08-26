"""Clusterer abstraction + HDBSCANClusterer / KMeansClusterer.

Same DI posture as Similiarity_gen/embedding_models.py: every concrete
clustering library import lives in this one file, so swapping the
clustering strategy later means adding one class here, not touching
QuestionTopologyBuilder.

Both implementations expect L2-normalized embeddings (topology_builder
normalizes once, before handing embeddings to either the clusterer or
the distiller) — normalized-vector Euclidean distance is a monotonic
transform of cosine distance (||a-b||^2 = 2 - 2*cos_sim for unit
vectors), which is what actually matters for text embeddings. Neither
class re-normalizes internally: doing it in one place means both
clusterers and the distiller agree on the same notion of "close."
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class Clusterer(ABC):
    """`-1` in the returned labels means "no cluster assigned" (noise).
    Callers (topology_builder.py) must preserve every `-1` as its own
    singleton topic, never drop it — see claude.md Section 3.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def fit_predict(self, embeddings: np.ndarray) -> np.ndarray:
        """Return an (n,) int array of cluster labels, one per row of
        `embeddings`. Implementations that don't have a noise concept
        (e.g. KMeans) must still return an array with no -1s — the
        absence of noise is a property of the algorithm, not something
        topology_builder should have to special-case."""
        raise NotImplementedError


class HDBSCANClusterer(Clusterer):
    """Default clusterer. Doesn't require guessing the number of topics
    up front (unlike KMeans), and explicitly supports "this question
    doesn't fit any topic" via -1 instead of forcing every point into
    the nearest cluster regardless of how weak the fit is — exactly the
    behavior claude.md Section 3 requires.

    Uses sklearn's built-in HDBSCAN (available since scikit-learn 1.3)
    rather than the standalone `hdbscan` package, so this package pulls
    in zero extra dependencies beyond what Similiarity_gen already needs.
    """

    def __init__(self, min_cluster_size: int = 3, metric: str = "euclidean"):
        from sklearn.cluster import HDBSCAN

        self._min_cluster_size = min_cluster_size
        self._metric = metric
        self._model = HDBSCAN(min_cluster_size=min_cluster_size, metric=metric)

    @property
    def name(self) -> str:
        return f"hdbscan-mcs{self._min_cluster_size}"

    @property
    def min_cluster_size(self) -> int:
        return self._min_cluster_size

    def fit_predict(self, embeddings: np.ndarray) -> np.ndarray:
        return self._model.fit_predict(embeddings)


class KMeansClusterer(Clusterer):
    """Comparison baseline: forces every point into a cluster (no noise
    concept at all), and needs k chosen up front. k is picked
    automatically per call via silhouette score over `k_range` — a fixed
    k would make this class silently wrong the moment the input size or
    topic count changes.
    """

    def __init__(self, k_range=range(2, 11), random_state: int = 42):
        self._k_range = k_range
        self._random_state = random_state
        self.selected_k_ = None  # set by fit_predict, sklearn-style trailing underscore
        self.selected_silhouette_ = None

    @property
    def name(self) -> str:
        return f"kmeans-autok{list(self._k_range)[0]}-{list(self._k_range)[-1]}"

    def fit_predict(self, embeddings: np.ndarray) -> np.ndarray:
        from sklearn.cluster import KMeans
        from sklearn.metrics import silhouette_score

        n = len(embeddings)
        best_k, best_score, best_labels = None, -1.0, None

        for k in self._k_range:
            if k >= n:
                # Can't form k clusters from fewer than k+1 points.
                continue
            model = KMeans(n_clusters=k, random_state=self._random_state, n_init=10)
            labels = model.fit_predict(embeddings)
            if len(set(labels)) < 2:
                continue  # silhouette_score is undefined for a single cluster
            score = silhouette_score(embeddings, labels)
            if score > best_score:
                best_k, best_score, best_labels = k, score, labels

        if best_labels is None:
            # Degenerate case (e.g. n_samples <= min(k_range)): fall back
            # to k=2 rather than crash, so a tiny input still returns
            # something usable.
            from sklearn.cluster import KMeans

            k = min(2, max(1, n - 1))
            model = KMeans(n_clusters=k, random_state=self._random_state, n_init=10)
            best_labels = model.fit_predict(embeddings)
            best_k, best_score = k, float("nan")

        self.selected_k_ = best_k
        self.selected_silhouette_ = best_score
        return best_labels
