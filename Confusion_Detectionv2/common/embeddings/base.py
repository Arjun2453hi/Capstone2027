"""base.py — the shared EmbeddingModel interface.

Lives in `common/` (not in any one stage) because more than one stage
needs embeddings under this project's design -- 02_topic_segmentation
(detecting where content shifts) and, later, 03_question_mapping
(scoring questions against topics) -- and both must be able to swap the
concrete model without touching each other or duplicating the
interface (root CLAUDE.md's dependency-injection principle).

No stage may import a concrete embedding library directly -- only files
under `common/embeddings/` do that.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

import numpy as np


class EmbeddingModel(ABC):
    """`fit` is a separate, optional hook (not folded into `embed`)
    because a model with corpus-dependent state (e.g. a TF-IDF
    vocabulary) needs to fit once on a corpus and then embed later,
    one-off text with that *same* fitted state -- collapsing the two
    into a single fit_transform-style call would make that impossible
    to express cleanly."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier, useful for logging which model actually
        produced a given run's embeddings (see common/embeddings'
        module-level requirement: never silently substitute a fallback
        model without saying so)."""
        raise NotImplementedError

    def fit(self, corpus: Sequence[str]) -> None:
        """No-op by default -- most embedding models (sentence-
        transformers, any pretrained encoder) don't need one;
        overriding is the exception (TF-IDF), not the rule."""
        return None

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Return an (len(texts), dim) float array. Must be safe to
        call with a single-item list."""
        raise NotImplementedError

    def embed_query(self, texts: Sequence[str]) -> np.ndarray:
        """Embed short queries for asymmetric retrieval against longer
        passages (embedded via plain `embed()`). Defaults to `embed()`
        -- most models used in this project (e.g. the symmetric
        sentence-transformer 02_topic_segmentation uses) have no
        query/passage distinction at all. A model trained specifically
        for retrieval (e.g. BGE, e5) requires a model-specific
        instruction prefix on the query side only for its vector space
        to behave as intended -- see RetrievalEmbeddingModel, which
        overrides this."""
        return self.embed(texts)
