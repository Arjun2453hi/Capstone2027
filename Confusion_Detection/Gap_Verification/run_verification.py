"""Generic CLI driver for Gap_Verification.

Same auto-detection posture as every prior stage's CLI: given a
directory, find context_windows_report.json by content, not filename,
and build the real pipeline: load ContextBundles -> GapVerifier -> a
GapVerdict per topic.

Usage:
    python -m Gap_Verification.run_verification
    python -m Gap_Verification.run_verification --singleton-mode full
    python -m Gap_Verification.run_verification --limit 20   # quick smoke run
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Optional

from . import _paths  # noqa: F401
from .classifier import ZeroShotNLIClassifier
from .generator import FlanT5Generator
from .verifier import DEFAULT_SINGLETON_MODE, SINGLETON_MODES, GapVerifier

from Context_Window.schema import ContextBundle

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "Context_Window"


def find_context_windows_report(directory: Path) -> Path:
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        if isinstance(data, dict) and "bundles" in data and isinstance(data["bundles"], list):
            return path
    raise FileNotFoundError(f"No context_windows_report.json-shaped file found in {directory}")


def load_bundles(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        ContextBundle(
            topic_id=b["topic_id"],
            representative_query=b["representative_query"],
            anchor_slide_id=b["anchor_slide_id"],
            window_slide_ids=b["window_slide_ids"],
            window_text=b["window_text"],
            source_questions=b["source_questions"],
            cluster_size=b["cluster_size"],
            is_noise=b["is_noise"],
        )
        for b in data["bundles"]
    ]


def run(
    directory: Path,
    report_path: Optional[Path],
    singleton_mode: str,
    limit: Optional[int],
    out_path: Optional[Path],
) -> dict:
    report_path = report_path or find_context_windows_report(directory)
    bundles = load_bundles(report_path)
    if limit:
        bundles = bundles[:limit]
    print(f"Bundles: {report_path} ({len(bundles)} topics, singleton_mode={singleton_mode!r})\n")

    classifier = ZeroShotNLIClassifier()
    generator = FlanT5Generator()
    verifier = GapVerifier(classifier, generator, singleton_mode=singleton_mode)

    verdicts = verifier.verify_all(bundles)

    gap_type_counts = {}
    for v in verdicts:
        gap_type_counts[v.gap_type] = gap_type_counts.get(v.gap_type, 0) + 1

    cluster_confidences = [v.confidence for v in verdicts if not v.is_noise]
    singleton_confidences = [v.confidence for v in verdicts if v.is_noise]
    n_suggested = sum(1 for v in verdicts if v.suggested_addition)
    n_eligible_for_suggestion = sum(1 for v in verdicts if v.gap_type != "covered" and not (v.is_noise and singleton_mode == "classify-only"))

    stats = {
        "n_topics_verified": len(verdicts),
        "gap_type_distribution": gap_type_counts,
        "mean_confidence_real_clusters": round(statistics.fmean(cluster_confidences), 4) if cluster_confidences else None,
        "mean_confidence_singletons": round(statistics.fmean(singleton_confidences), 4) if singleton_confidences else None,
        "n_suggested_addition_non_null": n_suggested,
        "n_eligible_for_suggestion": n_eligible_for_suggestion,
    }
    print(f"-- {len(verdicts)} verdicts --")
    print(f"  {stats}")

    technical_debt = next((v for v in verdicts if v.topic_id == 9), None)
    if technical_debt:
        print("\n  Technical debt topic (topic_id=9) verdict:")
        print(f"    gap_type={technical_debt.gap_type} confidence={technical_debt.confidence}")
        print(f"    guidance={technical_debt.guidance!r}")
        print(f"    suggested_addition={technical_debt.suggested_addition!r}")

    report = {
        "report_path": str(report_path),
        "singleton_mode": singleton_mode,
        "stats": stats,
        "verdicts": [
            {
                "topic_id": v.topic_id,
                "gap_type": v.gap_type,
                "slide_ids": v.slide_ids,
                "guidance": v.guidance,
                "suggested_addition": v.suggested_addition,
                "confidence": v.confidence,
                "backed_by_questions": v.backed_by_questions,
                "is_noise": v.is_noise,
            }
            for v in verdicts
        ],
    }

    out_path = out_path or (Path(__file__).resolve().parent / "verification_report.json")
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull report written to: {out_path}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--singleton-mode", choices=SINGLETON_MODES, default=DEFAULT_SINGLETON_MODE)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    run(
        directory=args.dir,
        report_path=args.report,
        singleton_mode=args.singleton_mode,
        limit=args.limit,
        out_path=args.out,
    )


if __name__ == "__main__":
    main()
