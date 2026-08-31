"""orchestrator.py — loops over every real topic plus the synthetic
unmatched-questions topic, running one fresh, isolated investigation
each, and writes each result to output/ (claude.md Sections 9-10).
"""
from __future__ import annotations

import importlib
import json
import time
from pathlib import Path
from typing import List

import numpy as np

from .agent import run_topic_investigation
from .prompts import TOPIC_KICKOFF_TEMPLATE, UNMATCHED_KICKOFF_TEMPLATE
from .schema import GapReport
from .tools import InvestigationContext, l2_normalize

FOUND_PERCENTILE = 50.0  # "roughly as relevant as a typical real match" -- see compute_found_threshold

# Deliberate pacing between topics, on top of agent.py's own
# between-step delay -- a fresh topic's first call has no preceding
# delay of its own, so without this, back-to-back topics would still
# burst right at the seam between one investigation ending and the
# next beginning.
INTER_TOPIC_DELAY_SECONDS = 5.0


def compute_found_threshold(dossiers, percentile: float = FOUND_PERCENTILE) -> float:
    """Adaptive, from this run's own real matched-question scores
    (Stage 3's actual output) -- not a guessed constant (claude.md
    Section 6). The median of real matches is a reasonable "this counts
    as genuinely found" bar: a search_expanding_context hit that scores
    at least as well as a typical real Stage-3 match is credible
    evidence, not a coincidental token overlap."""
    scores = [m.score for d in dossiers for m in d.matched_questions]
    if not scores:
        return 0.5
    return float(np.percentile(scores, percentile))


def compute_switch_threshold(deck, embedding_model) -> float:
    """Reuses 02_topic_segmentation's own adaptive threshold directly
    -- claude.md Section 6: "reuse Stage 2's own adaptive threshold,
    don't invent a new one." Re-running the segmenter here (rather than
    persisting its diagnostics from Stage 2's own run) keeps this stage
    correct even if Stage 2 is re-run with a different embedding model
    than this stage uses."""
    seg_module = importlib.import_module("02_topic_segmentation.src.segmenter")
    segmenter = seg_module.TopicSegmenter(embedding_model)
    _, diagnostics = segmenter.segment(deck)
    return float(diagnostics["threshold"])


def build_investigation_context(deck, dossiers, unmatched_questions, embedding_model) -> InvestigationContext:
    dossiers_by_id = {d.topic_id: d for d in dossiers}
    found_threshold = compute_found_threshold(dossiers)
    switch_threshold = compute_switch_threshold(deck, embedding_model)
    deck_embeddings = l2_normalize(np.asarray(embedding_model.embed([s.raw_text for s in deck.slides])))
    return InvestigationContext(
        deck=deck,
        dossiers_by_id=dossiers_by_id,
        unmatched_questions=unmatched_questions,
        embedding_model=embedding_model,
        found_threshold=found_threshold,
        switch_threshold=switch_threshold,
        deck_embeddings=deck_embeddings,
    )


def _format_question_list(questions: List[dict]) -> str:
    return "\n".join(f"- {q['question']} (score {q.get('score', 0):.3f})" for q in questions) or "(none)"


def report_filename(topic_id: int) -> str:
    return "topic_unmatched_report.json" if topic_id == -1 else f"topic_{topic_id:02d}_report.json"


def write_report_json(report: GapReport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / report_filename(report.topic_id)
    with path.open("w", encoding="utf-8") as f:
        json.dump(report.model_dump(), f, indent=2, ensure_ascii=False)
    return path


def run_all_topics(chat_model, ctx: InvestigationContext, dossiers, unmatched_questions, output_dir: Path) -> List[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for i, dossier in enumerate(dossiers):
        if i > 0:
            time.sleep(INTER_TOPIC_DELAY_SECONDS)
        questions = [{"question": m.question, "score": m.score} for m in dossier.matched_questions]
        kickoff = TOPIC_KICKOFF_TEMPLATE.format(
            topic_id=dossier.topic_id,
            start=dossier.slide_ids[0] if dossier.slide_ids else "?",
            end=dossier.slide_ids[-1] if dossier.slide_ids else "?",
            n=len(questions),
            question_list=_format_question_list(questions),
        )
        result = run_topic_investigation(chat_model, ctx, dossier.topic_id, kickoff)
        write_report_json(result["report"], output_dir)
        results.append({"topic_id": dossier.topic_id, **result})
        print(
            f"[{result['outcome']}] topic {dossier.topic_id}: gap_type={result['report'].gap_type} "
            f"confidence={result['report'].confidence:.2f} (tool calls: {result['n_tool_calls']})"
        )

    # Unmatched questions get their own real investigation, not a
    # lightweight side bucket (claude.md Section 9) -- run every time
    # there's at least one, even a single leftover question.
    if unmatched_questions:
        if dossiers:
            time.sleep(INTER_TOPIC_DELAY_SECONDS)
        questions = [{"question": u.question, "score": u.best_score} for u in unmatched_questions]
        kickoff = UNMATCHED_KICKOFF_TEMPLATE.format(n=len(questions), question_list=_format_question_list(questions))
        result = run_topic_investigation(chat_model, ctx, -1, kickoff)
        write_report_json(result["report"], output_dir)
        results.append({"topic_id": -1, **result})
        print(
            f"[{result['outcome']}] unmatched-questions topic: gap_type={result['report'].gap_type} "
            f"confidence={result['report'].confidence:.2f} (tool calls: {result['n_tool_calls']})"
        )

    return results


def summarize_outcomes(results: List[dict]) -> dict:
    """Three genuinely different outcomes that must never be conflated
    (claude.md Section 11)."""
    summary = {"completed": 0, "cap_hit": 0, "rate_limit_failed": 0}
    for r in results:
        summary[r["outcome"]] = summary.get(r["outcome"], 0) + 1
    return summary
