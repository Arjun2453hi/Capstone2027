"""Load editor context from existing outputs, without student questions."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_topic_context(topic_id: int, project_root: Path = PROJECT_ROOT) -> dict:
    """Return topic metadata, report and slide content in memory.

    Topic slides are editable; discovered slides outside the topic are
    read-only references. All slide IDs retain their original zero-based IDs.
    """
    root = Path(project_root)
    topics = _read_json(root / "02_topic_segmentation/topics.json")
    topic = next((item for item in topics if item["topic_id"] == topic_id), None)
    if topic is None:
        raise ValueError(f"Unknown topic_id: {topic_id}")
    report = _read_json(root / f"04_gap_reporting_agent/output/topic_{topic_id:02d}_report.json")
    if report["topic_id"] != topic_id:
        raise ValueError("Report topic_id does not match requested topic")
    editable_ids = topic["slide_ids"]
    if set(report["assigned_slide_ids"]) != set(editable_ids):
        raise ValueError("Report assigned slides do not match topic slide IDs")
    deck = _read_json(root / "01_deck_parsing/parsed_deck.json")
    slides = {slide["slide_id"]: slide for slide in deck["slides"]}
    reference_ids = sorted(set(report.get("discovered_slide_ids", [])) - set(editable_ids))
    missing = (set(editable_ids) | set(reference_ids)) - slides.keys()
    if missing:
        raise ValueError(f"Slide IDs missing from parsed deck: {sorted(missing)}")
    return {
        "topic_id": topic_id,
        "source_deck": deck["source_pdf"],
        "topic": topic,
        "report_text": report["report_text"],
        "gap_type": report["gap_type"],
        "confidence": report["confidence"],
        "slide_content": [slides[slide_id] for slide_id in editable_ids],
        "reference_slide_content": [slides[slide_id] for slide_id in reference_ids],
    }


def save_topic_context(context: dict, output_path: Path) -> None:
    """Optionally persist a context snapshot for inspection or later reuse."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
