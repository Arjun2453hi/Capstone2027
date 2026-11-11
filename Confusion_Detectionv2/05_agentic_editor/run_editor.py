"""run_editor.py — CLI driver for 05_agentic_editor.

Run directly (NOT `python -m ...` -- the folder name starts with a
digit, same reason every other stage's runner does this):

    python "05_agentic_editor/run_editor.py"

Plans edits from the gap reports, checks them, and applies the accepted
ones to a COPY of the deck. Two-step use, for reviewing before applying:

    python 05_agentic_editor/run_editor.py --deck-id <deck_id> --plan-only
    python 05_agentic_editor/run_editor.py --deck-id <deck_id> --apply-plan decks/<deck_id>/editor/edit_plan.json

Broken layouts: list candidates, look at them, then repair the ones you
confirm (slide numbers as shown in PowerPoint; add --topic-ids to plan
gap edits in the same run):

    python 05_agentic_editor/run_editor.py --deck-id <deck_id> --find-broken
    python 05_agentic_editor/run_editor.py --deck-id <deck_id> --repair-slides 5 98 282
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

deck_workspace = importlib.import_module("common.deck_workspace")
pdf_applier = importlib.import_module("05_agentic_editor.src.pdf_applier")
# The fact-check's answer is a short list, so it asks for a smaller reply
# budget than the planner and waits less on the tokens/minute limit.
FACT_CHECK_MAX_TOKENS = 1500


def load_reports(reports_dir: Path) -> dict:
    reports = {}
    for path in sorted(reports_dir.glob("topic_*_report.json")):
        with path.open("r", encoding="utf-8") as f:
            report = upstream.GapReport(**json.load(f))
        reports[report.topic_id] = report
    return reports


def _room(source: Path):
    """A .pptx deck's real slide space is measured; a PDF can only gain pages."""
    if source.suffix.lower() == ".pdf":
        return pdf_applier.pdf_room_check
    return importlib.import_module("05_agentic_editor.src.applier").RoomChecker(source)


def plan(deck, dossiers_by_id: dict, reports: dict, topic_ids, min_confidence: float, fact_check: bool = True, source_pptx: Path = None):
    planner = editor.EditPlanner(editor.GroqEditGenerator(), min_confidence=min_confidence,
                                 insert_only=source_pptx.suffix.lower() == ".pdf")
    print(f"Planner model: {planner.generator.model}")
    planner_module = importlib.import_module("05_agentic_editor.src.planner")
    revise = importlib.import_module("05_agentic_editor.src.revise")
    verifier = editor.EditVerifier(planner_module.with_max_tokens(planner.generator, FACT_CHECK_MAX_TOKENS)) if fact_check else None
    room = _room(source_pptx)
    checked = []
    skipped = {}
    for topic_id, report in sorted(reports.items()):
        if topic_ids is not None and topic_id not in topic_ids:
            continue
        dossier = dossiers_by_id.get(topic_id)
        reason = editor.skip_reason(report, dossier, min_confidence)
        if reason is not None:
            skipped[topic_id] = reason
            print(f"[skipped] topic {topic_id}: {reason}")
            continue
        topic_checked = revise.suggest_for_topic(
            report, dossier, deck, dossiers_by_id, planner, verifier,
            earlier=[c.edit for c in checked if c.accepted],
            progress=lambda message, t=topic_id: print(f"  topic {t}: {message}"),
            room=room,
        )
        print(f"[planned] topic {topic_id}: {sum(c.accepted for c in topic_checked)} of {len(topic_checked)} accepted")
        checked.extend(topic_checked)
    return checked, skipped


def describe(edit) -> str:
    """One line naming what an edit does, with 1-indexed slide numbers."""
    n = edit.after_slide_id + 1
    if edit.operation == "repair_slide":
        return f"repair of slide {n}"
    if edit.operation == "append_bullets":
        return f"topic {edit.topic_id}, add to slide {n}"
    return f"topic {edit.topic_id}, new slide after {n}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deck-id", default=None,
                        help="deck workspace to edit (see run_pipeline.py list); every path below defaults to it")
    parser.add_argument("--deck", type=Path, default=None, help="parsed deck JSON (default: the workspace's)")
    parser.add_argument("--gap-input", type=Path, default=None)
    parser.add_argument("--reports-dir", type=Path, default=None)
    parser.add_argument("--source-pptx", "--source", dest="source_pptx", type=Path, default=None,
                        help="the deck file edits are applied to: .pptx, or .pdf for a PDF-only deck")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--topic-ids", type=int, nargs="+", default=None, help="Only plan for these topic_id(s).")
    parser.add_argument("--min-confidence", type=float, default=None, help="Reports below this get no edits.")
    parser.add_argument("--no-fact-check", action="store_true", help="Skip the LLM fact-check of planned edits.")
    parser.add_argument("--plan-only", action="store_true", help="Write edit_plan.json and stop before touching any deck.")
    parser.add_argument("--find-broken", action="store_true", help="List slides whose layout looks broken, then stop.")
    parser.add_argument(
        "--repair-slides", type=int, nargs="+", default=None,
        help="Rebuild these slides (1-indexed, as PowerPoint numbers them) after confirming their layout is broken.",
    )
    parser.add_argument("--apply-plan", type=Path, default=None, help="Skip planning; apply the accepted edits of this plan file.")
    args = parser.parse_args()
    ws = deck_workspace.resolve(args.deck_id)
    args.deck = args.deck or ws.parsed_deck
    args.gap_input = args.gap_input or ws.gap_input
    args.reports_dir = args.reports_dir or ws.reports_dir
    args.source_pptx = args.source_pptx or ws.edit_source
    args.output_dir = args.output_dir or ws.editor_dir
    is_pdf = args.source_pptx.suffix.lower() == ".pdf"
    print(f"Deck '{ws.deck_id}' ({ws.title}); editing {args.source_pptx.name}")
    if is_pdf and (args.find_broken or args.repair_slides):
        sys.exit("Layout checks and repairs need the deck as .pptx; this deck is a PDF.")

    if args.find_broken:
        layout_check = importlib.import_module("05_agentic_editor.src.layout_check")
        pdf = ws.pdf if ws.pdf and ws.pdf.exists() else None
        print("Candidates, most likely broken first (open each to confirm):")
        for issue in layout_check.find_layout_issues(args.source_pptx, pdf, max_results=15):
            print(f"  slide {issue.slide_id + 1:3d}  score {issue.score:.2f}  " + "; ".join(issue.reasons))
        return

    deck = ws.editor_deck() if args.deck == ws.parsed_deck else upstream.load_deck_json(args.deck)
    gap_input = upstream.load_gap_verification_input_json(args.gap_input)
    dossiers_by_id = {d.topic_id: d for d in gap_input.topics}
    plan_path = args.output_dir / "edit_plan.json"

    if args.apply_plan is not None:
        # Re-checked rather than trusted: the plan file may have been
        # edited by hand, or by the review UI, since it was written. The
        # fact-check is not re-run (it costs LLM calls), so its findings
        # are kept from the file; delete them there to overrule them.
        stored = editor_storage.load_plan_json(args.apply_plan)
        room = _room(args.source_pptx)
        checked = editor.check_edits([c.edit for c in stored], dossiers_by_id, deck, room)
        for fresh, old in zip(checked, stored):
            fresh.problems.extend(p for p in old.problems if p.startswith("fact-check"))
    else:
        planner_module = importlib.import_module("05_agentic_editor.src.planner")
        min_confidence = args.min_confidence if args.min_confidence is not None else planner_module.MIN_REPORT_CONFIDENCE
        checked, skipped = [], {}
        if args.repair_slides is None or args.topic_ids is not None:
            checked, skipped = plan(deck, dossiers_by_id, load_reports(args.reports_dir), args.topic_ids, min_confidence, fact_check=not args.no_fact_check, source_pptx=args.source_pptx)
        if args.repair_slides:
            repair = importlib.import_module("05_agentic_editor.src.repair")
            repairs = repair.plan_repairs(repair.RepairPlanner(editor.GroqEditGenerator()), args.source_pptx, [n - 1 for n in args.repair_slides], deck)
            print(f"[planned] {len(repairs)} repair(s) of slides {args.repair_slides}")
            checked += editor.check_edits(repairs, dossiers_by_id, deck)
        editor_storage.save_plan_json(checked, skipped, plan_path)
        print(f"Wrote: {plan_path}")

    accepted = [c.edit for c in checked if c.accepted]
    for c in checked:
        status = "accepted" if c.accepted else "REJECTED: " + "; ".join(c.problems)
        print(f"  {describe(c.edit)}: {c.edit.title!r} -- {status}")
    print(f"{len(accepted)} of {len(checked)} edit(s) accepted")
    if args.plan_only or not accepted:
        return

    output_pptx = args.output_dir / f"{args.source_pptx.stem}.edited{args.source_pptx.suffix}"
    if is_pdf:
        banner = pdf_applier.find_banner(args.source_pptx)
        applied = pdf_applier.apply_edits_pdf(args.source_pptx, output_pptx, accepted, deck, banner=banner)
    else:
        applier = importlib.import_module("05_agentic_editor.src.applier")
        applied = applier.apply_edits(args.source_pptx, output_pptx, accepted, deck)
    change_log_path = args.output_dir / "change_log.json"
    editor_storage.save_change_log_json(applied, args.source_pptx, output_pptx, change_log_path)
    for a in applied:
        numbers = ", ".join(str(p + 1) for p in a.new_slide_positions)
        if a.edit.operation == "append_bullets" and not a.note:
            print(f"  added {len(a.edit.bullets)} bullet(s) to slide(s) {numbers} of the edited deck")
        elif a.edit.operation == "repair_slide":
            hidden = ", ".join(str(p + 1) for p in a.hidden_slide_positions)
            print(f"  rebuilt slide {a.edit.after_slide_id + 1} as slide {numbers}; the original, now slide {hidden}, is hidden")
        else:
            print(f"  added {a.edit.title!r} as slide(s) {numbers} of the edited deck")
        if a.note:
            print(f"    note: {a.note}")
    print(f"Wrote: {output_pptx}\nWrote: {change_log_path}")


if __name__ == "__main__":
    main()
