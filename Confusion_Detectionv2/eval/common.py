"""Shared pieces of the evaluation harness: the gap taxonomy, question
ids, config loading and slide equivalence.

Nothing in eval/ is imported by the pipeline, and nothing here feeds
labels back into it. Labels are only ever read by the scorer.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The paper's five classes.
GAP_TYPES = ("missing", "thin", "scattered", "covered", "off_topic")
GAP_CLASSES = {"missing", "thin", "scattered"}  # "is a gap" for binary detection

# Pipeline (stage 4) names -> paper names. The pipeline has no off_topic
# class: off-topic questions can only show up as "unmatched" in stage 3.
PIPELINE_TO_PAPER = {
    "complete_omission": "missing",
    "shallow_coverage": "thin",
    "fragmented_context": "scattered",
    "covered": "covered",
    "off_topic": "off_topic",  # unmatched-group verdicts only
}


def normalize_question(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


def question_id(text: str) -> str:
    """Stable id from the question text, independent of file order."""
    return "q_" + hashlib.sha1(normalize_question(text).encode("utf-8")).hexdigest()[:10]


def load_questions(path: Path) -> List[str]:
    seen, out = set(), []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        q = line.strip()
        if q and normalize_question(q) not in seen:
            seen.add(normalize_question(q))
            out.append(q)
    return out


def load_config(path: Path) -> dict:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    config["_path"] = str(path)
    return config


def resolve(config: dict, key: str) -> Path:
    return PROJECT_ROOT / config[key]


class SlideEquivalence:
    """Maps duplicate slides onto one canonical slide id, so a prediction
    of either copy of a duplicated slide counts as the same answer."""

    def __init__(self, ranges: Optional[Iterable[dict]] = None):
        self._ranges = list(ranges or [])

    def canonical(self, slide_id: int) -> int:
        for r in self._ranges:
            copy_first, copy_last = r["first"] + r["offset"], r["last"] + r["offset"]
            if copy_first <= slide_id <= copy_last:
                return slide_id - r["offset"]
        return slide_id

    def canonical_set(self, slide_ids: Iterable[int]) -> Set[int]:
        return {self.canonical(int(s)) for s in slide_ids}


def parse_int_list(value) -> Optional[List[int]]:
    """'3; 5-7' -> [3, 5, 6, 7]. Empty -> None (unlabelled)."""
    if value is None:
        return None
    if isinstance(value, list):
        return [int(v) for v in value]
    text = str(value).strip()
    if not text:
        return None
    out: List[int] = []
    for part in re.split(r"[;,\s]+", text):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def load_labels(path: Path) -> Dict[str, dict]:
    """Reads a labels CSV (or JSON list) into {question_id: label}.

    Columns: question_id, question, gap_type, topic_ids, slide_ids,
    annotator, notes. ``slide_ids`` are 1-indexed slide NUMBERS as shown
    in PowerPoint (easier for a person); they are converted to 0-indexed
    slide_id here. An empty gap_type means the row is not labelled yet.
    """
    path = Path(path)
    if path.suffix == ".json":
        rows = json.loads(path.read_text(encoding="utf-8"))
    else:
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    labels = {}
    for row in rows:
        gap_type = (row.get("gap_type") or "").strip().lower() or None
        if gap_type is not None and gap_type not in GAP_TYPES:
            raise ValueError(f"{row.get('question_id')}: gap_type {gap_type!r} is not one of {GAP_TYPES}")
        slide_numbers = parse_int_list(row.get("slide_numbers", row.get("slide_ids")))
        labels[row["question_id"]] = {
            "question_id": row["question_id"],
            "question": row.get("question"),
            "gap_type": gap_type,
            "topic_ids": parse_int_list(row.get("topic_ids")),
            "slide_ids": [n - 1 for n in slide_numbers] if slide_numbers else None,
            "annotator": (row.get("annotator") or "").strip() or None,
        }
    return labels


# Synthetic generator intent -> gap type. "deeper" questions ask for more
# than the slides give, so the intended verdict is "thin". These are the
# generator's INTENT, not verified labels: report results on them as
# synthetic, separately from scores on human labels.
SYNTHETIC_KIND_TO_GAP = {"answered": "covered", "deeper": "thin", "missing": "missing", "off_topic": "off_topic"}


def config_for_deck(deck_id: Optional[str] = None) -> dict:
    """Evaluation config for a deck workspace: every path comes from the
    workspace; deck-specific facts (e.g. duplicated slide ranges) come
    from an optional eval/configs/<deck_id>.json."""
    from common import deck_workspace

    ws = deck_workspace.resolve(deck_id)
    config = {
        "deck_id": ws.deck_id,
        "questions": _rel(ws.questions),
        "parsed_deck": _rel(ws.parsed_deck),
        "question_mapping": _rel(ws.question_mapping),
        "reports_dir": _rel(ws.reports_dir),
        "split": f"eval/datasets/splits/{ws.deck_id}.json",
        "labels": f"eval/datasets/gold/{ws.deck_id}.dev.labels.csv",
        "question_source": ws.question_source,
        "equivalent_slide_ranges": [],
        "seed": 2027,
        "held_out_fraction": 0.3,
    }
    extra = PROJECT_ROOT / "eval" / "configs" / f"{ws.deck_id}.json"
    if extra.exists():
        for key in ("equivalent_slide_ranges", "seed", "held_out_fraction"):
            config[key] = json.loads(extra.read_text(encoding="utf-8")).get(key, config[key])
    config["_path"] = f"deck:{ws.deck_id}"
    return config


def _rel(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def synthetic_labels(deck_id: Optional[str] = None) -> Path:
    """Write eval/datasets/gold/<deck>.synthetic.labels.csv from the
    synthetic generator's intent (all questions, any split)."""
    from common import deck_workspace

    ws = deck_workspace.resolve(deck_id)
    if not ws.synthetic_meta.exists():
        raise FileNotFoundError(f"Deck '{ws.deck_id}' has no synthetic questions")
    meta = json.loads(ws.synthetic_meta.read_text(encoding="utf-8"))
    out = PROJECT_ROOT / "eval" / "datasets" / "gold" / f"{ws.deck_id}.synthetic.labels.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["question_id", "question", "gap_type", "slide_numbers", "topic_ids", "annotator", "notes"])
        w.writeheader()
        for r in meta["questions"]:
            gap = SYNTHETIC_KIND_TO_GAP[r["kind"]]
            w.writerow({"question_id": question_id(r["question"]), "question": r["question"], "gap_type": gap,
                        # "missing" questions have no answer slide; their topic's slides are where it belongs.
                        "slide_numbers": "" if gap == "off_topic" else "; ".join(map(str, r.get("slide_numbers", []))),
                        "topic_ids": "" if r.get("topic_id") is None else str(r["topic_id"]),
                        "annotator": f"synthetic:{meta.get('model')}", "notes": f"intent={r['kind']}"})
    return out
