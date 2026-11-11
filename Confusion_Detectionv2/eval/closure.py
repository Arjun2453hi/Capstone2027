"""Did the edits close the gaps? Re-assess an edited deck.

    python eval/closure.py --edited path/to/edited.pptx --change-log path/to/change_log.json
    python eval/closure.py --edited ... --change-log ... --rejudge      # also re-run stage 4 (LLM)

Steps, all through the existing stage code, nothing re-implemented:

1. Parse the edited .pptx (hidden slides skipped: the editor hides the
   originals of slides it repairs, and students never see them).
2. Align its slides to the original deck by content. A slide with no
   counterpart is an added slide. This needs no record of the edits, so
   it works for hand edits too.
3. Carry the original topics over: each kept slide keeps its topic, and
   an added slide joins the topic of the slide before it.
4. Re-run stage 3 (question mapping) on the edited deck. No LLM unless
   a key is set and --llm-fallback is given.
5. With --rejudge, re-run stage 4 on the targeted topics: a fresh gap
   verdict on the edited deck. Needs GROQ_API_KEY, or
   LLM_REPLAY_MODE=replay with recordings of an earlier run.

Measures, per targeted topic (one with an accepted edit):
* Gap Closure Rate (needs --rejudge): targeted gaps whose post-edit
  verdict is "covered", divided by targeted gaps. A failed
  re-investigation (confidence 0) counts as not closed, and is listed.
* Evidence support (no LLM): for each question an edit claims to
  address, its best slide score before vs after, and whether its best
  slide is now an added slide. Better retrieval support is evidence the
  new text is relevant; it is not evidence the question is answered.
"""
from __future__ import annotations

import argparse
import difflib
import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "eval"

from eval.common import PROJECT_ROOT, normalize_question  # noqa: E402

PY = sys.executable


def _stage(name):
    return importlib.import_module(name)


def _slide_key(slide) -> str:
    return " ".join((slide.raw_text or "").lower().split())


def align_decks(original, edited) -> Dict[int, int]:
    """original slide_id -> edited slide_id, by content. Slides whose text
    is unchanged are matched in order; edited slides left over are additions.

    Empty (image-only) slides all have the same empty text; the sequence
    alignment still matches them by position between their non-empty
    neighbours, which is what an insertion-only edit preserves."""
    a = [_slide_key(s) for s in original.slides]
    b = [_slide_key(s) for s in edited.slides]
    mapping = {}
    for block in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(block.size):
            mapping[original.slides[block.a + k].slide_id] = edited.slides[block.b + k].slide_id
    return mapping


def remap_topics(topics, mapping: Dict[int, int], n_edited: int):
    """Original topics on the edited deck's slide ids. Added slides join
    the topic of the nearest kept slide before them."""
    Topic = _stage("02_topic_segmentation.src.schema").Topic
    owner = {}
    for t in topics:
        for sid in t.slide_ids:
            if sid in mapping:
                owner[mapping[sid]] = t.topic_id
    current = topics[0].topic_id if topics else 0
    for sid in range(n_edited):
        if sid in owner:
            current = owner[sid]
        else:
            owner[sid] = current
    out = []
    for t in topics:
        sids = sorted(s for s, tid in owner.items() if tid == t.topic_id)
        if sids:
            out.append(Topic(topic_id=t.topic_id, start_slide_id=sids[0], end_slide_id=sids[-1],
                             slide_ids=sids, boundary_confidence=t.boundary_confidence))
    return out


def _run(cmd: List[str], log: Path) -> None:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with log.open("a", encoding="utf-8") as f:
        f.write(f"$ {' '.join(cmd)}\n")
        f.flush()
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT, stdout=f, stderr=subprocess.STDOUT, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[1]} failed (exit {proc.returncode}); see {log}")


def _question_index(mapping_json: dict) -> Dict[str, dict]:
    """normalized question -> its best match (topic, score, slide)."""
    best = {}
    for t in mapping_json["topics"]:
        for m in t["matched_questions"]:
            key = normalize_question(m["question"])
            if key not in best or m["score"] > best[key]["score"]:
                best[key] = {"topic_id": t["topic_id"], "score": m["score"], "best_slide_id": m.get("best_slide_id")}
    for u in mapping_json.get("unmatched_questions", []):
        best.setdefault(normalize_question(u["question"]), {"topic_id": -1, "score": u["best_score"], "best_slide_id": None})
    return best


def evidence_support(before: dict, after: dict, edits: List[dict], added_slide_ids: set) -> List[dict]:
    rows = []
    b_idx, a_idx = _question_index(before), _question_index(after)
    for e in edits:
        for q in e.get("addresses_questions") or []:
            k = normalize_question(q)
            b, a = b_idx.get(k), a_idx.get(k)
            rows.append({
                "topic_id": e["topic_id"], "question": q,
                "score_before": b["score"] if b else None, "score_after": a["score"] if a else None,
                "topic_before": b["topic_id"] if b else None, "topic_after": a["topic_id"] if a else None,
                "best_slide_is_added": bool(a and a["best_slide_id"] in added_slide_ids),
            })
    return rows


def closure_table(targeted: List[int], before_reports: Dict[int, dict], after_reports: Dict[int, dict]) -> dict:
    rows = []
    for tid in targeted:
        b, a = before_reports.get(tid), after_reports.get(tid)
        failed = a is None or float(a.get("confidence", 0)) <= 0
        rows.append({
            "topic_id": tid,
            "before": b["gap_type"] if b else None, "before_confidence": b.get("confidence") if b else None,
            "after": None if failed else a["gap_type"], "after_confidence": None if failed else a.get("confidence"),
            "closed": (not failed) and a["gap_type"] == "covered",
            "reassessment_failed": failed,
        })
    gaps = [r for r in rows if r["before"] not in (None, "covered", "off_topic")]
    closed = [r for r in gaps if r["closed"]]
    return {"rows": rows, "targeted_gaps": len(gaps), "closed": len(closed),
            "gap_closure_rate": (len(closed) / len(gaps)) if gaps else None,
            "reassessment_failed": sum(r["reassessment_failed"] for r in gaps)}


def _load_reports(directory: Path) -> Dict[int, dict]:
    out = {}
    for p in sorted(Path(directory).glob("topic_*_report.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        out[int(r["topic_id"])] = r
    return out


def _map_questions(deck, topics, questions: Path, prefix: str, out_dir: Path, log: Path, llm_fallback: bool):
    deck_store = _stage("01_deck_parsing.src.storage")
    topic_store = _stage("02_topic_segmentation.src.storage")
    deck_path, topics_path = out_dir / f"{prefix}_parsed_deck.json", out_dir / f"{prefix}_topics.json"
    deck_store.save_deck_json(deck, deck_path)
    topic_store.save_topics_json(topics, topics_path)
    mapping_path, gap_input_path = out_dir / f"{prefix}_question_mapping.json", out_dir / f"{prefix}_gap_input.json"
    cmd = [PY, "03_question_mapping/run_mapping.py", "--deck", str(deck_path), "--topics", str(topics_path),
           "--questions", str(questions), "--out", str(mapping_path), "--gap-input-out", str(gap_input_path)]
    if not llm_fallback:
        cmd.append("--no-llm-fallback")
    _run(cmd, log)
    return deck_path, mapping_path, gap_input_path


def run_closure(edited_pptx: Path, change_log: Path, out_dir: Path, *, original_pptx: Optional[Path],
                original_topics: Path, original_reports: Path, questions: Path,
                rejudge: bool = False, llm_fallback: bool = False) -> dict:
    """Both decks go through the SAME (.pptx) parser and the same stage 3
    run, so a before/after difference comes from the edits, not from
    comparing a PDF parse with a .pptx parse."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "run.log"
    pptx_parser = _stage("01_deck_parsing.src.pptx_parser")
    topic_store = _stage("02_topic_segmentation.src.storage")

    changes = json.loads(Path(change_log).read_text(encoding="utf-8"))
    original_pptx = Path(original_pptx or PROJECT_ROOT / changes["source_deck"])
    if Path(original_pptx).suffix.lower() != Path(edited_pptx).suffix.lower():
        raise ValueError("The original and edited decks must be the same format (both .pptx or both .pdf)")
    # extract_any picks the parser by extension: the same parser reads both decks.
    original = pptx_parser.extract_any(original_pptx)
    edited = pptx_parser.extract_any(edited_pptx)
    topics = topic_store.load_topics_json(original_topics)
    n_topic_slides = max(sid for t in topics for sid in t.slide_ids) + 1
    if n_topic_slides != len(original.slides):
        raise ValueError(f"The topics cover {n_topic_slides} slides but {original_pptx} has {len(original.slides)} "
                         "visible slides: these are not the same deck.")
    mapping = align_decks(original, edited)
    added = sorted(set(range(len(edited.slides))) - set(mapping.values()))
    lost = sorted(set(s.slide_id for s in original.slides) - set(mapping))
    edited_topics = remap_topics(topics, mapping, len(edited.slides))

    _, before_path, _ = _map_questions(original, topics, questions, "original", out_dir, log, llm_fallback)
    deck_path, after_path, gap_input_path = _map_questions(edited, edited_topics, questions, "edited", out_dir, log, llm_fallback)

    edits = changes.get("edits", [])
    targeted = sorted({int(e["topic_id"]) for e in edits})
    before_map = json.loads(before_path.read_text(encoding="utf-8"))
    after_map = json.loads(after_path.read_text(encoding="utf-8"))
    support = evidence_support(before_map, after_map, edits, set(added))
    expected_added = int(changes.get("slides_added", len(added)))

    result = {
        "edited_deck": str(edited_pptx), "original_deck": str(original_pptx), "change_log": str(change_log),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "slides": {"original": len(original.slides), "edited": len(edited.slides), "added": len(added),
                   "added_per_change_log": expected_added,
                   "original_slides_not_found": len(lost), "added_slide_numbers": [s + 1 for s in added]},
        "targeted_topics": targeted,
        "evidence_support": {
            "rows": support,
            "questions": len(support),
            "improved": sum(1 for r in support if r["score_before"] is not None and r["score_after"] is not None
                            and r["score_after"] > r["score_before"] + 1e-6),
            "worse": sum(1 for r in support if r["score_before"] is not None and r["score_after"] is not None
                         and r["score_after"] < r["score_before"] - 1e-6),
            "best_slide_is_added": sum(r["best_slide_is_added"] for r in support),
            "stage3_llm_fallback": llm_fallback,
        },
        "closure": None,
    }
    if rejudge:
        reports_dir = out_dir / "edited_reports"
        _run([PY, "04_gap_reporting_agent/run_gap_reporting.py", "--deck", str(deck_path), "--gap-input",
              str(gap_input_path), "--output-dir", str(reports_dir), "--index-out", str(reports_dir / "index.json"),
              "--topic-ids", *map(str, targeted)], log)
        result["closure"] = closure_table(targeted, _load_reports(original_reports), _load_reports(reports_dir))
    (out_dir / "closure.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    (out_dir / "closure.md").write_text(render_markdown(result), encoding="utf-8")
    return result


def _f(v):
    return "n/a" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))


def render_markdown(r: dict) -> str:
    s, e = r["slides"], r["evidence_support"]
    lines = [
        "# Gap closure on the edited deck", "",
        f"Edited deck `{r['edited_deck']}`: {s['edited']} visible slides, {s['added']} added "
        f"(change log: {s['added_per_change_log']}), "
        f"{s['original_slides_not_found']} original slides not found unchanged (repaired, hidden or edited).", "",
        f"Targeted topics: {', '.join(map(str, r['targeted_topics'])) or 'none'}.", "",
        "## Gap Closure Rate", "",
    ]
    c = r["closure"]
    if c is None:
        lines += ["Not measured: run with `--rejudge` (needs GROQ_API_KEY, or LLM_REPLAY_MODE=replay with recordings).", ""]
    else:
        lines += [f"**{_f(c['gap_closure_rate'])}** ({c['closed']} of {c['targeted_gaps']} targeted gaps judged covered "
                  f"after editing; {c['reassessment_failed']} re-investigations failed and count as not closed).", "",
                  "| Topic | Before | After | Closed |", "|---|---|---|---|",
                  *[f"| {x['topic_id']} | {x['before']} ({_f(x['before_confidence'])}) | "
                    f"{x['after'] or 'failed'} ({_f(x['after_confidence'])}) | {'yes' if x['closed'] else 'no'} |" for x in c["rows"]], ""]
    lines += [
        "## Evidence support (no LLM)", "",
        f"{e['questions']} questions the edits claim to address. Best-slide score improved for {e['improved']}, "
        f"got worse for {e['worse']}; "
        f"the best-matching slide is a newly added slide for {e['best_slide_is_added']}. "
        "This shows the new text is relevant to the question, not that it answers it.", "",
        "| Topic | Score before | Score after | Best slide is new | Question |", "|---|---|---|---|---|",
        *[f"| {x['topic_id']} | {_f(x['score_before'])} | {_f(x['score_after'])} | {'yes' if x['best_slide_is_added'] else 'no'} | {x['question']} |"
          for x in e["rows"]], "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edited", type=Path, required=True, help="the edited deck (.pptx, or .pdf for a PDF-only deck)")
    ap.add_argument("--change-log", type=Path, required=True, help="the editor's change_log.json for that deck")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--rejudge", action="store_true", help="re-run stage 4 on targeted topics (LLM)")
    ap.add_argument("--llm-fallback", action="store_true", help="let stage 3 use its LLM tie-breaker")
    ap.add_argument("--deck-id", default=None, help="deck workspace (default: $DECK_ID or the only deck); "
                    "the paths below default to it")
    ap.add_argument("--original-pptx", type=Path, default=None, help="default: source_deck from the change log")
    ap.add_argument("--original-topics", type=Path, default=None)
    ap.add_argument("--original-reports", type=Path, default=None)
    ap.add_argument("--questions", type=Path, default=None)
    args = ap.parse_args(argv)
    from common import deck_workspace

    ws = deck_workspace.resolve(args.deck_id)
    args.original_pptx = args.original_pptx or ws.edit_source
    args.original_topics = args.original_topics or ws.topics
    args.original_reports = args.original_reports or ws.reports_dir
    args.questions = args.questions or ws.questions
    out = args.out or PROJECT_ROOT / "eval" / "reports" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-closure")
    r = run_closure(args.edited, args.change_log, out, original_pptx=args.original_pptx,
                    original_topics=args.original_topics, original_reports=args.original_reports, questions=args.questions,
                    rejudge=args.rejudge, llm_fallback=args.llm_fallback)
    s, e = r["slides"], r["evidence_support"]
    print(f"edited deck: {s['edited']} slides, {s['added']} added (change log says {s['added_per_change_log']}), "
          f"{s['original_slides_not_found']} originals not found")
    print(f"evidence support: {e['improved']}/{e['questions']} questions' best score improved; "
          f"{e['best_slide_is_added']} now best match a new slide")
    if r["closure"]:
        c = r["closure"]
        print(f"Gap Closure Rate: {_f(c['gap_closure_rate'])} ({c['closed']}/{c['targeted_gaps']})")
    print(f"report: {out / 'closure.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
