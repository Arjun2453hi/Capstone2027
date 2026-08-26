"""EmbeddingModel abstraction + concrete implementations.

Why an ABC at all: the whole point of this folder (per claude.md
Section 3) is that swapping the similarity strategy later — a different
embedding model, or eventually BM25/hybrid — should mean writing one new
subclass here, not touching SlideIndex or QuestionSlideRetriever. To
keep that promise real (not just a docstring claim), no other file in
this package may import a concrete embedding library directly; every
concrete import lives in this one file.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Sequence

import numpy as np


class EmbeddingModel(ABC):
    """Common interface every similarity backend implements.

    `fit` is separate from `embed` (rather than one `fit_transform`)
    because SlideIndex needs to fit once on the deck's slide corpus and
    then later embed one-off queries with the *same* fitted state
    (e.g. a TF-IDF vocabulary) — collapsing the two would make that
    impossible to express.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier used in cache keys. Must change whenever a
        change to this model would change its output vectors (e.g. bump
        it if you change TF-IDF's ngram_range), or the disk cache will
        silently serve stale embeddings for the old config."""
        raise NotImplementedError

    def fit(self, corpus: Sequence[str]) -> None:
        """Optional hook for models with corpus-dependent state (e.g. a
        TF-IDF vocabulary). No-op by default because most embedding
        models (sentence-transformers, any pretrained encoder) don't
        need one — overriding is the exception, not the rule."""
        return None

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Return an (len(texts), dim) float array. Implementations must
        be safe to call with a single-item list (query embedding)."""
        raise NotImplementedError


class TfidfEmbeddingModel(EmbeddingModel):
    """sklearn TF-IDF. No download, fully deterministic, always
    available — the baseline every other model is compared against, and
    the fallback when there's no internet/no GPU/no patience to wait on
    a model download.
    """

    def __init__(self, max_features: int | None = 20000, ngram_range=(1, 2)):
        # Imported here (not at module top) purely so the *symbol*
        # sklearn.feature_extraction lives only in this class's
        # __init__/embed — mirrors how the sentence-transformers class
        # below isolates its import, even though sklearn is a much
        # lighter dependency.
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
            # A TF-IDF vectorizer has no meaning before fit() — embedding
            # against an empty vocabulary would silently return all-zero
            # vectors, which looks like "everything is equally
            # dissimilar" instead of the real bug (used out of order).
            raise RuntimeError(
                "TfidfEmbeddingModel.embed() called before fit(); "
                "SlideIndex must fit the model on the slide corpus first."
            )
        matrix = self._vectorizer.transform(list(texts))
        return matrix.toarray().astype(np.float32)


class SentenceTransformerEmbeddingModel(EmbeddingModel):
    """Real semantic embeddings via all-MiniLM-L6-v2 — small (~80MB),
    free, runs on CPU, cached locally by the library after first
    download. This is what actually understands that "mocking" and
    "test doubles" are related concepts, which TF-IDF's bag-of-words
    can't.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        # The only file in this package allowed to import
        # sentence_transformers — see the module docstring.
        from sentence_transformers import SentenceTransformer

        self._model_name = model_name
        self._model = SentenceTransformer(model_name)

    @property
    def name(self) -> str:
        return f"sentence-transformers-{self._model_name}"

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self._model.encode(
            list(texts),
            normalize_embeddings=True,  # so cosine similarity == dot product
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)
