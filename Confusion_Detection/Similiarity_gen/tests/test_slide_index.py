"""Tests for SlideIndex: empty-slide skipping and disk caching."""
from __future__ import annotations

import numpy as np
import pytest

from ..cache import EmbeddingCache
from ..embedding_models import TfidfEmbeddingModel
from ..slide_index import SlideIndex
from .fixtures.ground_truth_questions import DECK


def test_skips_empty_slides():
    index = SlideIndex.build(DECK, TfidfEmbeddingModel())
    # DECK has 10 slides; slide_id 8 is fully blank and must be excluded.
    assert len(DECK.slides) == 10
    assert 8 not in index.slide_ids
    assert len(index) == 9
    assert index.embeddings.shape[0] == 9


def test_title_only_slide_is_kept():
    index = SlideIndex.build(DECK, TfidfEmbeddingModel())
    # slide_id 7 has a title but no bullets — still worth indexing.
    assert 7 in index.slide_ids


def test_cache_hit_avoids_recompute(tmp_path):
    cache = EmbeddingCache(cache_dir=tmp_path)
    model = TfidfEmbeddingModel()

    index1 = SlideIndex.build(DECK, model, cache=cache)
    key = cache.make_key(DECK.source_pdf, model.name, index1.texts)
    assert cache.get(key) is not None  # written after first build

    # A fresh model instance + fresh cache lookup should return the
    # exact cached vectors, not merely same-shaped ones.
    model2 = TfidfEmbeddingModel()
    index2 = SlideIndex.build(DECK, model2, cache=cache)
    np.testing.assert_array_equal(index1.embeddings, index2.embeddings)


def test_editing_deck_invalidates_cache(tmp_path):
    from dataclasses import replace

    cache = EmbeddingCache(cache_dir=tmp_path)
    model = TfidfEmbeddingModel()
    index1 = SlideIndex.build(DECK, model, cache=cache)

    edited_slides = list(DECK.slides)
    # raw_text/char_count must be reset too (not just bullets) — Slide's
    # __post_init__ only derives them when empty, so leaving the old
    # raw_text here would silently embed the *unedited* text.
    edited_slides[0] = replace(
        edited_slides[0],
        bullets=["A completely different bullet."],
        raw_text="",
        char_count=0,
    )
    edited_deck = replace(DECK, slides=edited_slides)

    model2 = TfidfEmbeddingModel()
    index2 = SlideIndex.build(edited_deck, model2, cache=cache)

    # Different content hash -> different cache entry -> not byte-identical.
    assert not np.array_equal(index1.embeddings, index2.embeddings)
