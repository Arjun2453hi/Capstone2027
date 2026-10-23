"""storage.py — save/load the edit plan and write the change log."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import List, Union

from .schema import APPEND_BULLETS, AppliedEdit, CheckedEdit, SlideEdit


def _write(payload, path: Union[str, Path]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def save_plan_json(checked: List[CheckedEdit], skipped: dict, path: Union[str, Path]) -> None:
    """`skipped`: topic_id -> why no edits were planned for it."""
    _write(
        {
            "edits": [{**asdict(c.edit), "accepted": c.accepted, "problems": c.problems, "revisions": c.revisions} for c in checked],
            "skipped_topics": {str(topic_id): reason for topic_id, reason in sorted(skipped.items())},
        },
        path,
    )


def load_plan_json(path: Union[str, Path]) -> List[CheckedEdit]:
    with Path(path).open("r", encoding="utf-8") as f:
        raw = json.load(f)
    checked = []
    for item in raw["edits"]:
        problems = item.pop("problems", [])
        revisions = item.pop("revisions", 0)
        item.pop("accepted", None)
        checked.append(CheckedEdit(edit=SlideEdit(**item), problems=problems, revisions=revisions))
    return checked


def save_change_log_json(applied: List[AppliedEdit], source_pptx, output_pptx, path: Union[str, Path]) -> None:
    _write(
        {
            "source_deck": str(source_pptx),
            "edited_deck": str(output_pptx),
            "slides_added": sum(len(a.new_slide_positions) for a in applied if a.edit.operation != APPEND_BULLETS or a.note),
            "edits": [
                {
                    **asdict(a.edit),
                    # 1-indexed, as a person paging through the edited deck sees them:
                    # the slides added, or (append_bullets) the slides changed.
                    "new_slide_numbers": [p + 1 for p in a.new_slide_positions],
                    "mirrored_after_slide_ids": a.mirrored_after_slide_ids,
                    "hidden_slide_numbers": [p + 1 for p in a.hidden_slide_positions],
                    "note": a.note,
                }
                for a in applied
            ],
        },
        path,
    )


def _checked_items(checked: List[CheckedEdit]) -> list:
    return [{**asdict(c.edit), "accepted": c.accepted, "problems": c.problems, "revisions": c.revisions} for c in checked]


def _checked_from_items(items: list) -> List[CheckedEdit]:
    result = []
    for item in items:
        item = dict(item)
        problems = item.pop("problems", [])
        revisions = item.pop("revisions", 0)
        item.pop("accepted", None)
        item.pop("review_note", None)
        result.append(CheckedEdit(edit=SlideEdit(**item), problems=problems, revisions=revisions))
    return result


def save_reviewed_json(topics: dict, repairs: dict, model: str, path: Union[str, Path]) -> None:
    """Suggestions generated ahead of time and read by a person, for the
    demo to load instantly. topics: topic_id -> [CheckedEdit];
    repairs: slide_id -> [CheckedEdit]."""
    from datetime import datetime, timezone

    _write(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": model,
            "topics": {str(t): _checked_items(c) for t, c in sorted(topics.items())},
            "repairs": {str(s): _checked_items(c) for s, c in sorted(repairs.items())},
        },
        path,
    )


def load_reviewed_json(path: Union[str, Path]):
    """(topics, repairs, metadata) as saved by save_reviewed_json, with
    int keys; ({}, {}, {}) if the file does not exist."""
    path = Path(path)
    if not path.exists():
        return {}, {}, {}
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    topics = {int(t): _checked_from_items(items) for t, items in raw.get("topics", {}).items()}
    repairs = {int(s): _checked_from_items(items) for s, items in raw.get("repairs", {}).items()}
    return topics, repairs, {"generated_at": raw.get("generated_at"), "model": raw.get("model")}
