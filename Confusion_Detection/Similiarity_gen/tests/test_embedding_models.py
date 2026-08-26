"""Unit tests for the embedding models themselves (not retrieval)."""
from __future__ import annotations

import numpy as np
import pytest

from ..embedding_models import SentenceTransformerEmbeddingModel, TfidfEmbeddingModel

CORPUS = [
    "Mocking replaces real dependencies with test doubles.",
    "A stub returns canned answers to calls.",
    "Red, Green, Refactor is the TDD cycle.",
]


def test_tfidf_requires_fit_before_embed():
    model = TfidfEmbeddingModel()
    with pytest.raises(RuntimeError):
        model.embed(["What is mocking?"])


def test_tfidf_fit_then_embed_shape():
    model = TfidfEmbeddingModel()
    model.fit(CORPUS)
    vectors = model.embed(CORPUS)
    assert vectors.shape[0] == len(CORPUS)
    assert vectors.shape[1] > 0
    # A single query embeds fine post-fit too.
    query_vec = model.embed(["What is a stub?"])
    assert query_vec.shape == (1, vectors.shape[1])


def test_tfidf_name_is_stable_and_config_sensitive():
    a = TfidfEmbeddingModel(max_features=100)
    b = TfidfEmbeddingModel(max_features=100)
    c = TfidfEmbeddingModel(max_features=200)
    assert a.name == b.name
    assert a.name != c.name  # cache key must change when config changes


@pytest.mark.slow
def test_sentence_transformer_embed_shape_and_normalization():
    model = SentenceTransformerEmbeddingModel()
    vectors = model.embed(CORPUS)
    assert vectors.shape[0] == len(CORPUS)
    norms = np.linalg.norm(vectors, axis=1)
    print(f"sentence-transformer norms: {norms}")
    # normalize_embeddings=True should make every vector ~unit length.
    assert np.allclose(norms, 1.0, atol=1e-3)
