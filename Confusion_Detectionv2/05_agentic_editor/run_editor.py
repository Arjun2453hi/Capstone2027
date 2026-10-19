"""run_editor.py — CLI driver for 05_agentic_editor.

Run directly (NOT `python -m ...` -- the folder name starts with a
digit, same reason every other stage's runner does this):

    python "05_agentic_editor/run_editor.py"

Plans edits from the gap reports, checks them, and applies the accepted
ones to a COPY of the deck. Two-step use, for reviewing before applying:

    python 05_agentic_editor/run_editor.py --plan-only
    python 05_agentic_editor/run_editor.py --apply-plan 05_agentic_editor/output/edit_plan.json

Separately, list slides whose layout looks broken (for a person to check):

    python 05_agentic_editor/run_editor.py --find-broken
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_PROJECT_ROOT / ".env")

editor = importlib.import_module("05_agentic_editor.src")
editor_storage = importlib.import_module("05_agentic_editor.src.storage")
upstream = importlib.import_module("05_agentic_editor.src._upstream")

DEFAULT_DECK_PATH = _PROJECT_ROOT / "01_deck_parsing" / "parsed_deck.json"
DEFAULT_GAP_INPUT_PATH = _PROJECT_ROOT / "03_question_mapping" / "gap_verification_input.json"
DEFAULT_REPORTS_DIR = _PROJECT_ROOT / "04_gap_reporting_agent" / "output"
DEFAULT_SOURCE_PPTX = _PROJECT_ROOT / "data" / "cc-unit2-slides.pptx"
DEFAULT_SOURCE_PDF = _PROJECT_ROOT / "data" / "cc-unit2-slides.pdf"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def load_reports(reports_dir: Path) -> dict:
    reports = {}
    for path in sorted(reports_dir.glob("topic_*_report.json")):
        with path.open("r", encoding="utf-8") as f:
            report = upstream.GapReport(**json.load(f))
        reports[report.topic_id] = report
    return reports


def plan(deck, dossiers_by_id: dict, reports: dict, topic_ids, min_confidence: float, fact_check: bool = True):
    planner = editor.EditPlanner(editor.GroqEditGenerator(), min_confidence=min_confidence)
    print(f"Planner model: {planner.generator.model}")
    edits, skipped = [], {}
    for topic_id, report in sorted(reports.items()):
        if topic_ids is not None and topic_id not in topic_ids:
            continue
        dossier = dossiers_by_id.get(topic_id)
        reason = editor.skip_reason(report, dossier, min_confidence)
        if reason is not None:
            skipped[topic_id] = reason
            print(f"[skipped] topic {topic_id}: {reason}")
            continue
        topic_edits = planner.plan_topic(report, dossier, deck)
        print(f"[planned] topic {topic_id}: {len(topic_edits)} edit(s)")
        edits.extend(topic_edits)
    checked = editor.check_edits(edits, dossiers_by_id, deck)
    if fact_check:
        print(f"Fact-checking {sum(c.accepted for c in checked)} edit(s) that passed the checker")
        editor.verify_checked(checked, editor.EditVerifier(planner.generator), dossiers_by_id, deck)
    return checked, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deck", type=Path, default=DEFAULT_DECK_PATH)
    parser.add_argument("--gap-input", type=Path, default=DEFAULT_GAP_INPUT_PATH)
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    parser.add_argument("--source-pptx", type=Path, default=DEFAULT_SOURCE_PPTX)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--topic-ids", type=int, nargs="+", default=None, help="Only plan for these topic_id(s).")
    parser.add_argument("--min-confidence", type=float, default=None, help="Reports below this get no edits.")
    parser.add_argument("--no-fact-check", action="store_true", help="Skip the LLM fact-check of planned edits.")
    parser.add_argument("--plan-only", action="store_true", help="Write edit_plan.json and stop before touching any deck.")
    parser.add_argument("--find-broken", action="store_true", help="List slides whose layout looks broken, then stop.")
    parser.add_argument("--apply-plan", type=Path, default=None, help="Skip planning; apply the accepted edits of this plan file.")
    args = parser.parse_args()

    if args.find_broken:
        layout_check = importlib.import_module("05_agentic_editor.src.layout_check")
        pdf = DEFAULT_SOURCE_PDF if DEFAULT_SOURCE_PDF.exists() else None
        print("Candidates, most likely broken first (open each to confirm):")
        for issue in layout_check.find_layout_issues(args.source_pptx, pdf, max_results=15):
            print(f"  slide {issue.slide_id + 1:3d}  score {issue.score:.2f}  " + "; ".join(issue.reasons))
        return

    deck = upstream.load_deck_json(args.deck)
    gap_input = upstream.load_gap_verification_input_json(args.gap_input)
    dossiers_by_id = {d.topic_id: d for d in gap_input.topics}
    plan_path = args.output_dir / "edit_plan.json"

    if args.apply_plan is not None:
        # Re-checked rather than trusted: the plan file may have been
        # edited by hand, or by the review UI, since it was written. The
        # fact-check is not re-run (it costs LLM calls), so its findings
        # are kept from the file; delete them there to overrule them.
        stored = editor_storage.load_plan_json(args.apply_plan)
        checked = editor.check_edits([c.edit for c in stored], dossiers_by_id, deck)
        for fresh, old in zip(checked, stored):
            fresh.problems.extend(p for p in old.problems if p.startswith("fact-check"))
    else:
        planner_module = importlib.import_module("05_agentic_editor.src.planner")
        min_confidence = args.min_confidence if args.min_confidence is not None else planner_module.MIN_REPORT_CONFIDENCE
        checked, skipped = plan(deck, dossiers_by_id, load_reports(args.reports_dir), args.topic_ids, min_confidence, fact_check=not args.no_fact_check)
        editor_storage.save_plan_json(checked, skipped, plan_path)
        print(f"Wrote: {plan_path}")

    accepted = [c.edit for c in checked if c.accepted]
    for c in checked:
        status = "accepted" if c.accepted else "REJECTED: " + "; ".join(c.problems)
        print(f"  topic {c.edit.topic_id}, after slide {c.edit.after_slide_id}: {c.edit.title!r} -- {status}")
    print(f"{len(accepted)} of {len(checked)} edit(s) accepted")
    if args.plan_only or not accepted:
        return

    applier = importlib.import_module("05_agentic_editor.src.applier")
    output_pptx = args.output_dir / f"{args.source_pptx.stem}.edited.pptx"
    applied = applier.apply_edits(args.source_pptx, output_pptx, accepted, deck)
    change_log_path = args.output_dir / "change_log.json"
    editor_storage.save_change_log_json(applied, args.source_pptx, output_pptx, change_log_path)
    for a in applied:
        numbers = ", ".join(str(p + 1) for p in a.new_slide_positions)
        print(f"  added {a.edit.title!r} as slide(s) {numbers} of the edited deck")
    print(f"Wrote: {output_pptx}\nWrote: {change_log_path}")


if __name__ == "__main__":
    main()
