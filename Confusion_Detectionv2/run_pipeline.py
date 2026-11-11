"""Add decks and run the whole pipeline for any of them.

    python run_pipeline.py add "SE - UNIT 4.pdf" --title "Software Engineering - Unit 4"
    python run_pipeline.py add deck.pptx deck.pdf --questions questions.txt
    python run_pipeline.py questions <deck_id> questions.txt     # set real student questions
    python run_pipeline.py list
    python run_pipeline.py run <deck_id>                         # stages 1-4
    python run_pipeline.py run <deck_id> --synthetic-questions   # no real questions yet
    python run_pipeline.py run <deck_id> --stop map              # everything except the LLM agent
    python run_pipeline.py run <deck_id> --from map --force      # redo from a stage

Then open it in the editor:  python 05_agentic_editor/serve.py   (pick the deck in the UI)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
# Record every LLM call this command makes -- in-process ones (synthetic
# questions) as well as the stage subprocesses -- so reruns replay them.
os.environ.setdefault("LLM_REPLAY_MODE", "auto")

from common import deck_workspace as dw  # noqa: E402
from pipeline import runner  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="register a deck (.pdf and/or .pptx of the same deck)")
    a.add_argument("files", nargs="+", type=Path)
    a.add_argument("--id", dest="deck_id")
    a.add_argument("--title")
    a.add_argument("--subject")
    a.add_argument("--questions", type=Path, help="real student questions, one per line")
    a.add_argument("--in-place", action="store_true", help="reference the files instead of copying them")

    q = sub.add_parser("questions", help="set a deck's real student questions")
    q.add_argument("deck_id")
    q.add_argument("file", type=Path)

    sub.add_parser("list", help="list decks and their status")

    r = sub.add_parser("run", help="run stages for a deck")
    r.add_argument("deck_id")
    r.add_argument("--from", dest="start", choices=runner.STAGES)
    r.add_argument("--stop", choices=runner.STAGES)
    r.add_argument("--force", action="store_true", help="rerun the selected stages even if up to date")
    r.add_argument("--synthetic-questions", action="store_true", help="generate questions if the deck has none")
    r.add_argument("--no-llm-fallback", action="store_true", help="stage 3 without its LLM tie-breaker")
    r.add_argument("--topic-ids", type=int, nargs="+", help="stage 4 for these topics only (-1 = unmatched)")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "add":
            ws = dw.register(args.files, deck_id=args.deck_id, title=args.title, subject=args.subject,
                             questions=args.questions, copy=not args.in_place)
            print(f"Added deck '{ws.deck_id}' ({ws.editable_format}) at {ws.root}")
            print(f"Next: python run_pipeline.py run {ws.deck_id}" + ("" if ws.has_questions() else " --synthetic-questions"))
        elif args.cmd == "questions":
            ws = dw.load(args.deck_id)
            dw.set_questions(ws, args.file, source="real")
            print(f"'{ws.deck_id}' now uses real questions from {args.file} ({ws.questions})")
        elif args.cmd == "list":
            decks = dw.list_decks()
            if not decks:
                print(f"No decks yet in {dw.decks_root()}. Add one: python run_pipeline.py add <file>")
            for ws in decks:
                s = ws.status()
                print(f"{ws.deck_id:28} {ws.editable_format:5} questions={ws.question_source if s['questions'] else 'none':9} "
                      f"parsed={'y' if s['parsed'] else 'n'} topics={'y' if s['segmented'] else 'n'} "
                      f"mapped={'y' if s['mapped'] else 'n'} reports={s['reports']}  {ws.title}")
        elif args.cmd == "run":
            ws = dw.load(args.deck_id)
            done = runner.run(ws, start=args.start, stop=args.stop, force=args.force,
                              synthetic_questions=args.synthetic_questions, llm_fallback=not args.no_llm_fallback,
                              topic_ids=args.topic_ids)
            print(f"\n'{ws.deck_id}': " + "; ".join(f"{k}: {v}" for k, v in done.items()))
    except (FileNotFoundError, FileExistsError, ValueError, runner.PipelineError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
