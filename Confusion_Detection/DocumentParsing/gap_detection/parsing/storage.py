"""Load a DeckDocument from the JSON produced by Step 1 (DocParsing_1.py).

Kept separate from schema.py so the schema stays importable with zero
I/O dependencies (useful for tests that build a DeckDocument in memory).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from .schema import DeckDocument, Slide


def load_deck_json(path: Union[str, Path]) -> DeckDocument:
    """Read a deck JSON file (see Similiarity_gen/claude.md Section 2 for
    the exact shape) and return it as a DeckDocument.

    Slides are loaded in file order but keyed by their own `slide_id` —
    callers must never assume slide_id == index into `.slides`.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    slides = [
        Slide(
            slide_id=s["slide_id"],
            slide_number=s["slide_number"],
            title=s.get("title"),
            bullets=s.get("bullets", []),
            notes=s.get("notes"),
            raw_text=s.get("raw_text", ""),
            char_count=s.get("char_count", 0),
            module_id=s.get("module_id"),
        )
        for s in raw["slides"]
    ]

    return DeckDocument(
        source_pdf=raw["source_pdf"],
        num_pages=raw["num_pages"],
        slides=slides,
        extracted_at=raw.get("extracted_at", ""),
    )


def save_deck_json(deck: DeckDocument, path: Union[str, Path]) -> None:
    """Inverse of load_deck_json — used by test fixtures, not by the
    main pipeline (Step 1 owns writing real deck JSON)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(deck.to_dict(), f, indent=2, ensure_ascii=False)
