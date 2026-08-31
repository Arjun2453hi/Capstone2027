"""storage.py — save/load a Deck as JSON."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Union

from .schema import Deck, Slide


def save_deck_json(deck: Deck, path: Union[str, Path]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_pdf": deck.source_pdf,
        "num_pages": deck.num_pages,
        "slides": [asdict(s) for s in deck.slides],
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def load_deck_json(path: Union[str, Path]) -> Deck:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    slides = [
        Slide(
            slide_id=s["slide_id"],
            slide_number=s["slide_number"],
            title=s.get("title"),
            title_font_size=s.get("title_font_size"),
            bullets=s.get("bullets", []),
            raw_text=s.get("raw_text", ""),
            char_count=s.get("char_count", 0),
        )
        for s in raw["slides"]
    ]
    return Deck(source_pdf=raw["source_pdf"], num_pages=raw["num_pages"], slides=slides)
