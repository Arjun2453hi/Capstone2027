"""retrieval.py — asymmetric query/passage EmbeddingModel implementation.

Some sentence-transformers models are trained specifically for
retrieval (query and passage come from different distributions) and
require a model-specific instruction prefix on the query side for
their vector space to behave as intended -- prepending it to both
sides, or neither, measurably degrades retrieval quality. Generic
SentenceTransformerEmbeddingModel doesn't know about this convention
(and shouldn't -- the model it's used for elsewhere in this project,
all-mpnet-base-v2 in 02_topic_segmentation, is symmetric and has no
such distinction). This class is for the specific models that do,
primarily 03_question_mapping's question-against-topic scoring, which
is genuinely asymmetric (a short interrogative vs. a long descriptive
passage).
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from .base import EmbeddingModel

# Each entry: model_name -> that model's own documented query-side
# instruction string. Passage-side text is embedded as-is unless the
# model also specifies a passage prefix (e5 wants both sides prefixed,
# differently; BGE only prefixes the query).
_QUERY_PREFIXES = {
    "BAAI/bge-small-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "intfloat/e5-small-v2": "query: ",
}
_PASSAGE_PREFIXES = {
    "intfloat/e5-small-v2": "passage: ",
}


class RetrievalEmbeddingModel(EmbeddingModel):
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        from sentence_transformers import SentenceTransformer

        self._model_name = model_name
        self._model = SentenceTransformer(model_name)
        self._query_prefix = _QUERY_PREFIXES.get(model_name, "")
        self._passage_prefix = _PASSAGE_PREFIXES.get(model_name, "")

    @property
    def name(self) -> str:
        return f"retrieval-{self._model_name}"

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        return self._encode([f"{self._passage_prefix}{t}" for t in texts])

    def embed_query(self, texts: Sequence[str]) -> np.ndarray:
        return self._encode([f"{self._query_prefix}{t}" for t in texts])

    def _encode(self, texts) -> np.ndarray:
        vectors = self._model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vectors, dtype=np.float32)
