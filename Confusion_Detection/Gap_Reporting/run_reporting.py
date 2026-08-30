"""Generic CLI driver for Gap_Reporting.

Same auto-detection posture as every prior stage's CLI: given a
directory, find the verdict input (a JSON with a "verdicts" list, i.e.
Gap_Verification's verification_report.json) by content, not filename,
and the deck JSON the same way Similiarity_gen's own CLI does. Builds
the real pipeline: GapVerdicts -> ReportBuilder -> gap_report.json +
gap_report.md.

Usage:
    python -m Gap_Reporting.run_reporting
    python -m Gap_Reporting.run_reporting --verdicts path/to/verification_report.json
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import _paths  # noqa: F401
from .aggregation import SimpleAggregator
from .renderers import JSONRenderer, MarkdownRenderer
from .report_builder import ReportBuilder
from .severity import DefaultSeverityScorer

from gap_detection.parsing.storage import load_deck_json

DEFAULT_VERDICTS_DIR = Path(__file__).resolve().parent.parent / "Gap_Verification"
DEFAULT_DECK_DIR = Path(__file__).resolve().parent.parent / "Similiarity_gen"


@dataclass
class _Verdict:
    """GapVerdict-shaped stand-in built from the JSON report -- this
    module doesn't import Gap_Verification.schema, keeping the
    dependency direction one-way like everywhere else in this folder."""

    topic_id: int
    gap_type: str
    slide_ids: List[int]
    guidance: str
    suggested_addition: Optional[str]
    confidence: float
    backed_by_questions: int
    is_noise: bool


def find_verdicts_file(directory: Path) -> Path:
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        if isinstance(data, dict) and "verdicts" in data and isinstance(data["verdicts"], list):
            return path
    raise FileNotFoundError(f"No verdicts JSON (a {{'verdicts': [...]}} file) found in {directory}")


def find_deck_json(directory: Path) -> Path:
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        if isinstance(data, dict) and "slides" in data and isinstance(data["slides"], list):
            return path
    raise FileNotFoundError(f"No deck JSON found in {directory}")


def load_verdicts(path: Path) -> List[_Verdict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        _Verdict(
            topic_id=v["topic_id"],
            gap_type=v["gap_type"],
            slide_ids=v["slide_ids"],
            guidance=v["guidance"],
            suggested_addition=v["suggested_addition"],
            confidence=v["confidence"],
            backed_by_questions=v["backed_by_questions"],
            is_noise=v["is_noise"],
        )
        for v in data["verdicts"]
    ]


def run(
    verdicts_dir: Path,
    verdicts_path: Optional[Path],
    deck_dir: Path,
    deck_path: Optional[Path],
    out_dir: Optional[Path],
) -> None:
    verdicts_path = verdicts_path or find_verdicts_file(verdicts_dir)
    deck_path = deck_path or find_deck_json(deck_dir)

    verdicts = load_verdicts(verdicts_path)
    deck = load_deck_json(deck_path)
    print(f"Verdicts: {verdicts_path} ({len(verdicts)} topics)")
    print(f"Deck:     {deck_path} ({len(deck.slides)} slides)\n")

    builder = ReportBuilder(DefaultSeverityScorer(), SimpleAggregator(), deck)
    report = builder.build(verdicts)

    print(f"total_topics_considered={report.total_topics_considered}")
    print(f"total_gaps_reported={report.total_gaps_reported}")
    print(f"module_grouping_available={report.module_grouping_available}")

    gap_type_counts = {}
    for e in report.entries:
        gap_type_counts[e.gap_type] = gap_type_counts.get(e.gap_type, 0) + 1
    print(f"gap_type distribution among reported gaps: {gap_type_counts}")

    real_count = sum(1 for e in report.entries if not e.is_noise)
    singleton_count = sum(1 for e in report.entries if e.is_noise)
    print(f"real-cluster entries: {real_count}, singleton entries: {singleton_count}")

    print("\ntop 5 entries by severity:")
    for e in report.entries[:5]:
        print(
            f"  {e.slide_range_label} (topic {e.topic_id}, {e.gap_type}, "
            f"severity={e.severity:.2f}, backed_by={e.backed_by_questions}, is_noise={e.is_noise})"
        )

    out_dir = out_dir or Path(__file__).resolve().parent
    json_path = out_dir / "gap_report.json"
    md_path = out_dir / "gap_report.md"
    json_path.write_text(JSONRenderer().render(report), encoding="utf-8")
    md_path.write_text(MarkdownRenderer().render(report), encoding="utf-8")

    print(f"\nWrote: {json_path}")
    print(f"Wrote: {md_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verdicts-dir", type=Path, default=DEFAULT_VERDICTS_DIR)
    parser.add_argument("--verdicts", type=Path, default=None)
    parser.add_argument("--deck-dir", type=Path, default=DEFAULT_DECK_DIR)
    parser.add_argument("--deck", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()

    run(
        verdicts_dir=args.verdicts_dir,
        verdicts_path=args.verdicts,
        deck_dir=args.deck_dir,
        deck_path=args.deck,
        out_dir=args.out_dir,
    )


if __name__ == "__main__":
    main()
