"""SlideIndex — embeds and holds all non-empty slides for one deck.

Deliberately knows nothing about *how* similarity is computed later
(that's QuestionSlideRetriever's job) or which embedding library is in
use (that's EmbeddingModel's job) — its only responsibilities are: pick
which slides are worth embedding, fit/embed them through whatever model
it's given, and cache the result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np

from . import _paths  # noqa: F401  (side effect: puts gap_detection on sys.path)
from .cache import EmbeddingCache
from .embedding_models import EmbeddingModel

from gap_detection.parsing.schema import DeckDocument, Slide


@dataclass
class SlideIndex:
    deck_source: str
    model: EmbeddingModel
    slide_ids: List[int]
    slide_numbers: List[int]
    texts: List[str]
    embeddings: np.ndarray  # shape (n_slides, dim)

    @classmethod
    def build(
        cls,
        deck: DeckDocument,
        model: EmbeddingModel,
        cache: EmbeddingCache | None = None,
    ) -> "SlideIndex":
        """Embed every non-empty slide in `deck` with `model`.

        Slides with no title and no bullets are skipped entirely (not
        embedded as an empty string) — an empty embedding is noise that
        can spuriously rank high against short questions, per
        claude.md Section 3.
        """
        non_empty: List[Slide] = [s for s in deck.slides if not s.is_empty]
        texts = [s.raw_text for s in non_empty]
        slide_ids = [s.slide_id for s in non_empty]
        slide_numbers = [s.slide_number for s in non_empty]

        cache = cache if cache is not None else EmbeddingCache()
        cache_key = cache.make_key(deck.source_pdf, model.name, texts)

        # fit() always runs, cache hit or not: a cached SlideIndex still
        # hands its `model` to QuestionSlideRetriever for *query-time*
        # embedding later, and a TfidfEmbeddingModel with an unfitted
        # vectorizer would raise on the first query. fit() is cheap and
        # deterministic given the same corpus, so redoing it on a cache
        # hit costs far less than the alternative (a retriever that
        # silently only works after a cold run).
        model.fit(texts)

        embeddings = cache.get(cache_key)
        if embeddings is None or len(embeddings) != len(texts):
            embeddings = model.embed(texts)
            cache.set(
                cache_key,
                embeddings,
                meta={
                    "deck_source": deck.source_pdf,
                    "model_name": model.name,
                    "n_slides": len(texts),
                },
            )

        return cls(
            deck_source=deck.source_pdf,
            model=model,
            slide_ids=slide_ids,
            slide_numbers=slide_numbers,
            texts=texts,
            embeddings=np.asarray(embeddings),
        )

    def __len__(self) -> int:
        return len(self.slide_ids)
