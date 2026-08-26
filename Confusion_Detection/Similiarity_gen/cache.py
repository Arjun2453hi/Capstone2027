"""Disk cache for slide embeddings, keyed by (deck source, model name,
content hash).

Why content-hash and not just "deck path": editing the deck (re-parsing
after a slide fix) or swapping models must auto-invalidate the cache —
serving stale vectors silently is worse than recomputing, since a stale
cache hit looks identical to a correct one until scores quietly stop
making sense. Keying on a hash of the actual slide texts (not just the
deck's file mtime) also means copying/renaming the deck file doesn't
force a needless recompute.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / ".cache" / "embeddings"


class EmbeddingCache:
    def __init__(self, cache_dir: Path | str = DEFAULT_CACHE_DIR):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def make_key(deck_source: str, model_name: str, texts: Sequence[str]) -> str:
        """Hash the actual slide contents, not just a count/length, so
        any edit to any slide's text changes the key."""
        hasher = hashlib.sha256()
        hasher.update(deck_source.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(model_name.encode("utf-8"))
        hasher.update(b"\0")
        for t in texts:
            hasher.update(t.encode("utf-8"))
            hasher.update(b"\x1f")  # unit separator, cheap collision guard
        return hasher.hexdigest()

    def _paths(self, key: str) -> tuple[Path, Path]:
        return (
            self.cache_dir / f"{key}.npy",
            self.cache_dir / f"{key}.meta.json",
        )

    def get(self, key: str) -> Optional[np.ndarray]:
        vec_path, _ = self._paths(key)
        if not vec_path.exists():
            return None
        try:
            return np.load(vec_path)
        except Exception:
            # A corrupted cache entry (partial write, format change)
            # should degrade to "cache miss", never crash the pipeline.
            return None

    def set(self, key: str, embeddings: np.ndarray, meta: dict | None = None) -> None:
        vec_path, meta_path = self._paths(key)
        np.save(vec_path, embeddings)
        meta_path.write_text(
            json.dumps(meta or {}, indent=2), encoding="utf-8"
        )
