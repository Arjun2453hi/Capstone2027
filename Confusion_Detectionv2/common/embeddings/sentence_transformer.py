"""sentence_transformer.py — sentence-transformers EmbeddingModel implementation.

The real default for this project (see 02_topic_segmentation/CLAUDE.md
Section 2): understands that two slides can continue the same topic
while sharing almost no exact vocabulary, which TF-IDF's bag-of-words
view can't.

Default model: all-mpnet-base-v2 (~420MB, 768-dim), upgraded from
all-MiniLM-L6-v2 (~80MB, 384-dim). mpnet scores meaningfully better on
semantic-textual-similarity benchmarks, and at deck sizes in the
hundreds of slides the extra compute cost is trivial (single-digit
seconds either way) -- so the quality gain is close to free here. Still
small enough to run on CPU and cache locally after first download.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from .base import EmbeddingModel


class SentenceTransformerEmbeddingModel(EmbeddingModel):
    def __init__(self, model_name: str = "all-mpnet-base-v2"):
        from sentence_transformers import SentenceTransformer

        self._model_name = model_name
        self._model = SentenceTransformer(model_name)

    @property
    def name(self) -> str:
        return f"sentence-transformers-{self._model_name}"

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)
