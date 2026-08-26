"""QuestionSlideRetriever — ranks slides against a question.

Takes a SlideIndex, not a separate model reference, and always reads the
embedding model off that index. This is the one design choice in this
package that most needs a "why": if the retriever accepted its own
model argument, nothing would stop someone from building a SlideIndex
with model A and then calling QuestionSlideRetriever(index, model=B) —
the query would land in a different vector space than the slides, and
every score would be silently meaningless (usually still a valid float,
just not comparable to anything). Reading the model off the index makes
that mistake structurally impossible.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np

from .slide_index import SlideIndex


@dataclass
class RetrievalResult:
    slide_id: int
    slide_number: int
    score: float


def _cosine_similarity(query_vecs: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """(n_queries, dim) x (n_slides, dim) -> (n_queries, n_slides).

    Normalizes both sides explicitly rather than assuming the embedding
    model already did so — SentenceTransformerEmbeddingModel does
    (`normalize_embeddings=True`), but TfidfEmbeddingModel's sklearn
    vectorizer also happens to L2-normalize by default; either way this
    makes the retriever correct regardless of what a *future*
    EmbeddingModel implementation does or forgets to do.
    """
    def _l2_normalize(a: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(a, axis=1, keepdims=True)
        norms[norms == 0] = 1.0  # avoid div-by-zero for an all-zero vector
        return a / norms

    q = _l2_normalize(np.atleast_2d(query_vecs))
    m = _l2_normalize(matrix)
    return q @ m.T


class QuestionSlideRetriever:
    def __init__(self, index: SlideIndex):
        self.index = index

    def retrieve(self, question: str, top_k: int = 5) -> List[RetrievalResult]:
        return self.retrieve_batch([question], top_k=top_k)[0]

    def retrieve_batch(
        self, questions: List[str], top_k: int = 5
    ) -> List[List[RetrievalResult]]:
        if len(self.index) == 0:
            # An empty index (e.g. every slide was blank) is a valid
            # state, not an error — return "no matches" per question
            # rather than crashing on the matmul below.
            return [[] for _ in questions]

        query_vectors = self.index.model.embed(list(questions))
        sims = _cosine_similarity(query_vectors, self.index.embeddings)

        k = min(top_k, len(self.index))
        results: List[List[RetrievalResult]] = []
        for row in sims:
            # argpartition for the top-k candidates, then sort just
            # those k by score — avoids a full O(n log n) sort per
            # question when n_slides is large and top_k is small.
            top_idx = np.argpartition(-row, k - 1)[:k]
            top_idx = top_idx[np.argsort(-row[top_idx])]
            results.append(
                [
                    RetrievalResult(
                        slide_id=self.index.slide_ids[i],
                        slide_number=self.index.slide_numbers[i],
                        score=float(row[i]),
                    )
                    for i in top_idx
                ]
            )
        return results
