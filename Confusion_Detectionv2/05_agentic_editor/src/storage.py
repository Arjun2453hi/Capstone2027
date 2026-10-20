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
            "edits": [{**asdict(c.edit), "accepted": c.accepted, "problems": c.problems} for c in checked],
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
        item.pop("accepted", None)
        checked.append(CheckedEdit(edit=SlideEdit(**item), problems=problems))
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
