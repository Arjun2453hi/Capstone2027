"""tfidf.py — TF-IDF EmbeddingModel implementation.

No download, fully deterministic, always available -- the explicit
no-network/offline fallback (see 02_topic_segmentation/CLAUDE.md
Section 2: not a co-equal default with sentence-transformers, since
TF-IDF's literal-token-overlap view of similarity can misread a real
topic-continuing slide that just uses different vocabulary as a false
boundary).
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

import numpy as np

from .base import EmbeddingModel


class TfidfEmbeddingModel(EmbeddingModel):
    def __init__(self, max_features: Optional[int] = 20000, ngram_range: Tuple[int, int] = (1, 2)):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._max_features = max_features
        self._ngram_range = ngram_range
        self._vectorizer = TfidfVectorizer(
            max_features=max_features,
            ngram_range=ngram_range,
            stop_words="english",
        )
        self._fitted = False

    @property
    def name(self) -> str:
        return f"tfidf-v1-mf{self._max_features}-ng{self._ngram_range}"

    def fit(self, corpus: Sequence[str]) -> None:
        self._vectorizer.fit(list(corpus))
        self._fitted = True

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError(
                "TfidfEmbeddingModel.embed() called before fit() -- fit "
                "on the deck's slide corpus first."
            )
        return self._vectorizer.transform(list(texts)).toarray().astype(np.float32)
