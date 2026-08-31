"""storage.py — save/load MappingResult as JSON."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Union

from .schema import MappingResult, QuestionMatch, TopicMapping, UnmatchedQuestion


def save_mapping_json(result: MappingResult, path: Union[str, Path]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(asdict(result), f, indent=2, ensure_ascii=False)


def load_mapping_json(path: Union[str, Path]) -> MappingResult:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    topics = [
        TopicMapping(
            topic_id=t["topic_id"],
            start_slide_id=t["start_slide_id"],
            end_slide_id=t["end_slide_id"],
            slide_ids=t.get("slide_ids", []),
            boundary_confidence=t.get("boundary_confidence", 0.0),
            matched_questions=[QuestionMatch(**q) for q in t["matched_questions"]],
        )
        for t in raw["topics"]
    ]
    unmatched = [UnmatchedQuestion(**u) for u in raw["unmatched_questions"]]
    return MappingResult(
        generated_at=raw["generated_at"],
        total_questions=raw["total_questions"],
        total_topics=raw["total_topics"],
        high_threshold=raw["high_threshold"],
        low_threshold=raw["low_threshold"],
        topics=topics,
        unmatched_questions=unmatched,
    )
