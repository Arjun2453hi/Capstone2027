"""common.embeddings — the shared EmbeddingModel interface + concrete implementations."""
from .base import EmbeddingModel
from .retrieval import RetrievalEmbeddingModel
from .sentence_transformer import SentenceTransformerEmbeddingModel
from .tfidf import TfidfEmbeddingModel

__all__ = [
    "EmbeddingModel",
    "TfidfEmbeddingModel",
    "SentenceTransformerEmbeddingModel",
    "RetrievalEmbeddingModel",
]
