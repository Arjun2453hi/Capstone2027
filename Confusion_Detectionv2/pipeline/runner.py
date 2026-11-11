"""Run stages 1-4 for one deck workspace, through each stage's own CLI
with explicit paths (no stage default is ever used).

Stages:  parse -> segment -> questions -> map -> report

A stage is skipped when its output is newer than its inputs, so a run
can be repeated after an interruption or a quota limit; --force reruns
from a given stage. LLM calls are recorded (LLM_REPLAY_MODE=auto unless
set), so a resumed run replays finished calls instead of paying again.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

from common.deck_workspace import PROJECT_ROOT, DeckWorkspace, _rel as rel  # stage files record relative paths

STAGES = ("parse", "segment", "questions", "map", "report")


class PipelineError(RuntimeError):
    pass


def _fresh(output: Path, *inputs: Path) -> bool:
    if not output.exists():
        return False
    newest = max((p.stat().st_mtime for p in inputs if p and p.exists()), default=0)
    return output.stat().st_mtime >= newest


def _run(cmd: List[str], log: Path, progress: Callable[[str], None]) -> None:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.setdefault("LLM_REPLAY_MODE", "auto")
    with log.open("a", encoding="utf-8") as f:
        f.write(f"\n$ {' '.join(cmd)}\n")
        f.flush()
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT, stdout=f, stderr=subprocess.STDOUT, env=env)
    if proc.returncode != 0:
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        raise PipelineError(f"{Path(cmd[1]).name} failed (exit {proc.returncode}). Last log lines:\n" + "\n".join(tail))


def run(ws: DeckWorkspace, *, start: Optional[str] = None, stop: Optional[str] = None, force: bool = False,
        synthetic_questions: bool = False, llm_fallback: bool = True, topic_ids: Optional[List[int]] = None,
        progress: Callable[[str], None] = print) -> dict:
    stages = list(STAGES)
    lo = stages.index(start) if start else 0
    hi = stages.index(stop) + 1 if stop else len(stages)
    wanted = stages[lo:hi]
    ws.stages.mkdir(parents=True, exist_ok=True)
    log = ws.root / "pipeline.log"
    py = sys.executable
    done = {}

    def forced(stage):
        return force and stage in wanted

    if "parse" in wanted:
        if forced("parse") or not _fresh(ws.parsed_deck, ws.parse_source):
            progress(f"[parse] {ws.parse_source.name}")
            parser = importlib.import_module("01_deck_parsing.src")
            deck = parser.extract_any(ws.parse_source)
            deck.source_pdf = rel(ws.parse_source)  # never an absolute, machine-specific path
            parser.save_deck_json(deck, ws.parsed_deck)
            empty = sum(s.is_empty() for s in deck.slides)
            done["parse"] = f"{len(deck.slides)} slides ({empty} without text)"
        else:
            done["parse"] = "up to date"
        progress(f"[parse] {done['parse']}")

    if "segment" in wanted:
        if forced("segment") or not _fresh(ws.topics, ws.parsed_deck):
            progress("[segment] finding topic boundaries")
            _run([py, "02_topic_segmentation/run_segmentation.py", "--deck", rel(ws.parsed_deck),
                  "--topics-out", rel(ws.topics), "--plot-out", rel(ws.segmentation_plot)], log, progress)
        n = len(importlib.import_module("02_topic_segmentation.src.storage").load_topics_json(ws.topics))
        done["segment"] = f"{n} topics"
        progress(f"[segment] {done['segment']}")

    if "questions" in wanted:
        if ws.has_questions() and not (forced("questions") and ws.question_source == "synthetic"):
            done["questions"] = f"{ws.question_source} ({ws.questions.name})"
        elif synthetic_questions:
            progress("[questions] no questions: generating synthetic ones (LLM)")
            from .synthetic_questions import generate_for_workspace

            n = generate_for_workspace(ws, progress=lambda m: progress(f"[questions] {m}"))
            done["questions"] = f"{n} synthetic questions"
        else:
            raise PipelineError(
                f"Deck '{ws.deck_id}' has no questions. Add real ones with "
                f"`run_pipeline.py questions {ws.deck_id} <file.txt>`, or rerun with --synthetic-questions.")
        progress(f"[questions] {done['questions']}")

    if "map" in wanted:
        if forced("map") or not _fresh(ws.gap_input, ws.parsed_deck, ws.topics, ws.questions):
            progress("[map] matching questions to topics")
            cmd = [py, "03_question_mapping/run_mapping.py", "--deck", rel(ws.parsed_deck), "--topics", rel(ws.topics),
                   "--questions", rel(ws.questions), "--out", rel(ws.question_mapping),
                   "--gap-input-out", rel(ws.gap_input)]
            if not llm_fallback:
                cmd.append("--no-llm-fallback")
            _run(cmd, log, progress)
        done["map"] = "done"
        progress("[map] done")

    if "report" in wanted:
        todo = topic_ids or (None if forced("report") else pending_topics(ws))
        if todo == []:
            done["report"] = f"up to date ({ws.status()['reports']} reports)"
        else:
            label = "all topics" if todo is None else f"topics {', '.join(map(str, todo))}"
            progress(f"[report] investigating {label} (LLM agent)")
            cmd = [py, "04_gap_reporting_agent/run_gap_reporting.py", "--deck", rel(ws.parsed_deck),
                   "--gap-input", rel(ws.gap_input), "--output-dir", rel(ws.reports_dir),
                   "--index-out", rel(ws.reports_index)]
            if todo is not None:
                cmd += ["--topic-ids", *map(str, todo)]
            _run(cmd, log, progress)
            left = pending_topics(ws)
            done["report"] = f"{ws.status()['reports']} reports" + (
                f"; still pending (quota?): {', '.join(map(str, left))} -- run again to resume" if left else "")
        progress(f"[report] {done['report']}")
    return done


def pending_topics(ws: DeckWorkspace) -> Optional[List[int]]:
    """Topics that still need a stage 4 investigation: no report yet, or
    the last attempt failed on rate limits / request size. None means
    "all of them" (the question mapping changed since the reports)."""
    import json

    if not ws.reports_index.exists() or not _fresh(ws.reports_index, ws.gap_input):
        return None
    gap_input = json.loads(ws.gap_input.read_text(encoding="utf-8"))
    wanted = [t["topic_id"] for t in gap_input["topics"] if t.get("matched_questions")]
    if gap_input.get("unmatched_questions"):
        wanted.append(-1)
    outcome = {e["topic_id"]: e.get("outcome") for e in json.loads(ws.reports_index.read_text(encoding="utf-8"))}
    pending = []
    for tid in wanted:
        name = "topic_unmatched_report.json" if tid == -1 else f"topic_{tid:02d}_report.json"
        if not (ws.reports_dir / name).exists() or outcome.get(tid) in ("rate_limit_failed", "request_too_large", "error"):
            pending.append(tid)
    return pending
