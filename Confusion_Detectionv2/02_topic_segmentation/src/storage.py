"""storage.py — save/load the Topic list as JSON."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import List, Union

from .schema import Topic


def save_topics_json(topics: List[Topic], path: Union[str, Path]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump([asdict(t) for t in topics], f, indent=2)


def load_topics_json(path: Union[str, Path]) -> List[Topic]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return [Topic(**r) for r in raw]
