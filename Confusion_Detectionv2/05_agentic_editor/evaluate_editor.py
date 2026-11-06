"""Evaluate saved editor artifacts without API calls; optionally export a review form."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Keep this CLI usable with Python's standard library, independently of
# the editor package's optional model and presentation dependencies.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from evaluation import evaluate, render_markdown, review_template  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path(__file__).resolve().parent
    parser.add_argument("--input", type=Path, default=base / "output" / "edit_plan.json")
    parser.add_argument("--reviews", type=Path, help="Completed JSON human-review form")
    parser.add_argument("--questions", type=Path, help="UTF-8 text file, one question per line, for a new review form")
    parser.add_argument("--export-review", type=Path, help="Create a blank review form; never overwrites an existing form")
    parser.add_argument("--output-dir", type=Path, default=base / "output" / "evaluation")
    args = parser.parse_args()
    try:
        snapshot = json.loads(args.input.read_text(encoding="utf-8-sig"))
        reviews = json.loads(args.reviews.read_text(encoding="utf-8-sig")) if args.reviews else None
        report = evaluate(snapshot, reviews)
        if args.export_review:
            questions = args.questions.read_text(encoding="utf-8-sig").splitlines() if args.questions else []
            template = review_template(snapshot, questions)
            args.export_review.parent.mkdir(parents=True, exist_ok=True)
            with args.export_review.open("x", encoding="utf-8") as handle:
                json.dump(template, handle, indent=2, ensure_ascii=False)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "metrics.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (args.output_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Evaluation failed: {exc}\n")
    print(render_markdown(report))
    print(f"Saved metrics.json and report.md in {args.output_dir}")


if __name__ == "__main__":
    main()
