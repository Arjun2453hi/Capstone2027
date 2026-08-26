"""Distiller abstraction + CentroidClosestDistiller.

Why "pick the real question closest to the centroid" instead of
generating a fresh summary query: it's free (no LLM call), deterministic
(same cluster always distills the same way), and — most importantly —
guarantees the representative query is something a real student
actually asked, which matters when Step 5 shows a human "here's the
question we used to probe this gap." An LLM-paraphrase distiller could
produce a cleaner-sounding query but risks asking about something
subtly different from what was actually asked; that trade-off is why
claude.md says not to build one yet.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

import numpy as np


class Distiller(ABC):
    @abstractmethod
    def distill(self, questions: List[str], embeddings: np.ndarray) -> str:
        """`embeddings` are the rows for exactly these `questions`, in
        the same order — one representative string comes out."""
        raise NotImplementedError


class CentroidClosestDistiller(Distiller):
    def distill(self, questions: List[str], embeddings: np.ndarray) -> str:
        if len(questions) == 1:
            return questions[0]  # nothing to pick between

        centroid = embeddings.mean(axis=0)
        # Embeddings are assumed L2-normalized by the caller (see
        # clustering.py's module docstring for why); centroid distance
        # is computed via cosine similarity anyway (not just Euclidean
        # to the centroid) so this distiller gives a sane answer even if
        # it's ever called with un-normalized vectors.
        norms = np.linalg.norm(embeddings, axis=1)
        norms[norms == 0] = 1.0
        centroid_norm = np.linalg.norm(centroid) or 1.0
        similarities = (embeddings @ centroid) / (norms * centroid_norm)

        best_idx = int(np.argmax(similarities))
        return questions[best_idx]
