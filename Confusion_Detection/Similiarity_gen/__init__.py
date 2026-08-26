"""Similarity_gen — the semantic-similarity engine for Gap Detection.

Given a question and a parsed slide deck, says which slide(s) are most
relevant, with a numeric score. Scoped narrowly to embedding +
similarity only (see claude.md Section 1) — clustering, BM25 fusion,
and LLM judgment are later stages that consume this module's output.
"""
from .embedding_models import (
    EmbeddingModel,
    SentenceTransformerEmbeddingModel,
    TfidfEmbeddingModel,
)
from .retriever import QuestionSlideRetriever, RetrievalResult
from .slide_index import SlideIndex

__all__ = [
    "EmbeddingModel",
    "TfidfEmbeddingModel",
    "SentenceTransformerEmbeddingModel",
    "SlideIndex",
    "QuestionSlideRetriever",
    "RetrievalResult",
]
