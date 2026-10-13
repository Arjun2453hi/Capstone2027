"""orchestrator.py — loops over every real topic plus the synthetic
unmatched-questions topic, running one fresh, isolated investigation
each, and writes each result to output/ (claude.md Sections 9-10).
"""
from __future__ import annotations

import copy
import dataclasses
import importlib
import json
import time
from pathlib import Path
from typing import List

import numpy as np

from .agent import run_topic_investigation
from .prompts import TOPIC_KICKOFF_TEMPLATE, UNMATCHED_KICKOFF_TEMPLATE
from .schema import GapReport
from .severity import GAP_TYPE_WEIGHT
from .tools import InvestigationContext, l2_normalize

FOUND_PERCENTILE = 10.0  # see compute_found_threshold

# Deliberate pacing between topics, on top of agent.py's own
# between-step delay -- a fresh topic's first call has no preceding
# delay of its own, so without this, back-to-back topics would still
# burst right at the seam between one investigation ending and the
# next beginning.
INTER_TOPIC_DELAY_SECONDS = 5.0

# A topic with many matched questions is investigated in parts, each
# covering the questions that land on one stretch of its slides, and
# the part reports are merged into the topic's single report. Measured
# on the real deck: every topic with 13+ questions and 20+ slides
# (topics 5, 7, 11) ran out of its step budget twice in a row and ended
# at confidence 0.35, because one investigation had to resend ~3000
# tokens of slides on every step while searching for a dozen different
# things. Topics with up to 10 questions completed normally.
MAX_QUESTIONS_PER_PART = 8
MAX_SLIDE_SPAN_PER_PART = 10


def compute_found_threshold(dossiers, deck, embedding_model, percentile: float = FOUND_PERCENTILE) -> float:
    """Adaptive, from this run's own real matches (Stage 3's actual
    output) -- not a guessed constant (claude.md Section 6). Calibrated
    on the exact quantity search_expanding_context compares against it:
    for every real (question, best-matching slide) pair, the relevance
    of the radius-1 window around that slide to the question, embedded
    the same way the tool embeds both sides. The threshold is a low
    percentile of those, i.e. "a window around a slide that is known to
    answer the question scores at least this."

    This used to be the median of Stage 3's own match scores. That only
    worked while those scores happened to sit on a similar scale; once
    Stage 3 began scoring single slides against query-prefixed
    questions its median rose to ~0.80, a bar this tool's multi-slide
    windows clear only 28% of the time even when anchored on the right
    slide. Measured on the real deck at the 10th percentile: 0.69,
    which is found at the right anchor 90% of the time by construction
    and at an arbitrary anchor about 7% of the time."""
    matches = [m for d in dossiers for m in d.matched_questions]
    if not matches:
        return 0.5
    pairs = sorted({(m.question, m.best_slide_id) for m in matches if m.best_slide_id is not None})
    if not pairs:
        # Input written before Stage 3 recorded best_slide_id.
        return float(np.percentile([m.score for m in matches], 50.0))

    row_of = {s.slide_id: i for i, s in enumerate(deck.slides)}
    windows = []
    for _, slide_id in pairs:
        row = row_of[slide_id]
        window = deck.slides[max(0, row - 1) : row + 2]
        windows.append("\n\n".join(s.raw_text for s in window if s.raw_text.strip()))
    window_vecs = l2_normalize(np.asarray(embedding_model.embed(windows), dtype=float))
    question_vecs = l2_normalize(np.asarray(embedding_model.embed([q for q, _ in pairs]), dtype=float))
    relevances = np.sum(window_vecs * question_vecs, axis=1)
    return float(np.percentile(relevances, percentile))


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
    found_threshold = compute_found_threshold(dossiers, deck, embedding_model)
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


def split_into_parts(dossier, deck) -> list:
    """Splits a large topic into part-dossiers (same topic_id, a
    sub-range of its slides, the questions whose best slide is in that
    sub-range). Returns [dossier] unchanged when it is small enough, or
    when its matches carry no best_slide_id to split on."""
    matches = dossier.matched_questions
    if len(matches) <= MAX_QUESTIONS_PER_PART or any(m.best_slide_id is None for m in matches):
        return [dossier]

    ordered = sorted(matches, key=lambda m: m.best_slide_id)
    groups = [[ordered[0]]]
    for match in ordered[1:]:
        group = groups[-1]
        too_many = len(group) >= MAX_QUESTIONS_PER_PART
        too_wide = match.best_slide_id - group[0].best_slide_id >= MAX_SLIDE_SPAN_PER_PART
        if too_many or too_wide:
            groups.append([match])
        else:
            group.append(match)

    parts = []
    for group in groups:
        # One slide of margin either side: the slide before/after the
        # best match usually carries the lead-in or the continuation.
        low, high = group[0].best_slide_id - 1, group[-1].best_slide_id + 1
        slide_ids = [sid for sid in dossier.slide_ids if low <= sid <= high]
        slides = [deck.get(sid) for sid in slide_ids]
        text = "\n\n".join(s.raw_text.strip() for s in slides if s is not None and s.raw_text.strip())
        parts.append(
            dataclasses.replace(
                dossier,
                slide_ids=slide_ids,
                slide_range=f"Slides {slide_ids[0] + 1}-{slide_ids[-1] + 1}",
                window_text=text,
                cluster_size=len(group),
                matched_questions=group,
            )
        )
    return parts


def merge_part_results(dossier, part_results: list) -> dict:
    """Combines the (part_dossier, result) pairs of one topic into a
    single result. Deterministic, no LLM: the topic takes the most
    severe gap_type any part concluded with (a part that failed outright
    at confidence 0 has no say), the confidence of the parts that
    reached it, and every part's own text under a heading saying which
    slides and how many questions it covered."""
    concluded = [r["report"] for _, r in part_results if r["report"].confidence > 0]
    if concluded:
        gap_type = max((r.gap_type for r in concluded), key=lambda g: GAP_TYPE_WEIGHT.get(g, 0.0))
        agreeing = [r.confidence for r in concluded if r.gap_type == gap_type]
        confidence = float(sum(agreeing) / len(agreeing))
    else:
        gap_type, confidence = "shallow_coverage", 0.0

    sections = []
    for i, (part, r) in enumerate(part_results, start=1):
        report = r["report"]
        sections.append(
            f"Part {i} of {len(part_results)} -- slides {part.slide_ids[0]}-{part.slide_ids[-1]}, "
            f"{len(part.matched_questions)} question(s): {report.gap_type} "
            f"(confidence {report.confidence:.2f})\n\n{report.report_text.strip()}"
        )

    outcomes = {r["outcome"] for _, r in part_results}
    outcome = next((o for o in ("request_too_large", "cap_hit") if o in outcomes), "completed")
    report = GapReport(
        topic_id=dossier.topic_id,
        assigned_slide_ids=list(dossier.slide_ids),
        discovered_slide_ids=sorted({sid for _, r in part_results for sid in r["report"].discovered_slide_ids}),
        gap_type=gap_type,
        confidence=confidence,
        report_text="\n\n".join(sections),
    )
    return {"report": report, "outcome": outcome, "n_tool_calls": sum(r["n_tool_calls"] for _, r in part_results)}


def _investigate_topic(chat_model, ctx: InvestigationContext, dossier) -> dict:
    parts = split_into_parts(dossier, ctx.deck)
    part_results = []
    for part in parts:
        if part_results:
            time.sleep(INTER_TOPIC_DELAY_SECONDS)
        part_ctx = ctx
        if len(parts) > 1:
            # Same tools, same deck-wide search; only what this topic_id
            # resolves to is narrowed to the part.
            part_ctx = copy.copy(ctx)
            part_ctx.dossiers_by_id = {**ctx.dossiers_by_id, part.topic_id: part}
        questions = [{"question": m.question, "score": m.score} for m in part.matched_questions]
        kickoff = TOPIC_KICKOFF_TEMPLATE.format(
            topic_id=part.topic_id,
            start=part.slide_ids[0] if part.slide_ids else "?",
            end=part.slide_ids[-1] if part.slide_ids else "?",
            n=len(questions),
            question_list=_format_question_list(questions),
        )
        result = run_topic_investigation(chat_model, part_ctx, part.topic_id, kickoff)
        if result["outcome"] == "rate_limit_failed":
            return result
        part_results.append((part, result))
        if len(parts) > 1:
            print(
                f"  [{result['outcome']}] topic {part.topic_id} part {len(part_results)}/{len(parts)} "
                f"(slides {part.slide_ids[0]}-{part.slide_ids[-1]}): gap_type={result['report'].gap_type} "
                f"confidence={result['report'].confidence:.2f} (tool calls: {result['n_tool_calls']})"
            )
    return part_results[0][1] if len(parts) == 1 else merge_part_results(dossier, part_results)


def run_all_topics(chat_model, ctx: InvestigationContext, dossiers, unmatched_questions, output_dir: Path) -> List[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for dossier in dossiers:
        if not dossier.matched_questions:
            # No student question points at this topic, so there is no
            # evidence of a gap to investigate.
            print(f"[skipped] topic {dossier.topic_id}: no matched questions")
            continue
        if results:
            time.sleep(INTER_TOPIC_DELAY_SECONDS)
        result = _investigate_topic(chat_model, ctx, dossier)
        if result["outcome"] == "rate_limit_failed":
            # Every later topic would hit the same wall. Stop here, and
            # leave this topic's existing report (if any) alone rather
            # than overwriting it with a "failed" placeholder.
            print(f"[rate_limit_failed] topic {dossier.topic_id}: stopping the run; this and later topics were not investigated")
            results.append({"topic_id": dossier.topic_id, **result})
            return results
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
        if results:
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
    summary = {"completed": 0, "cap_hit": 0, "rate_limit_failed": 0, "request_too_large": 0}
    for r in results:
        summary[r["outcome"]] = summary.get(r["outcome"], 0) + 1
    return summary
