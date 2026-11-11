"""Score the pipeline's saved outputs against question labels.

    python eval/score.py                                # dev split, default labels
    python eval/score.py --labels path/to/labels.csv     # e.g. the answer key, same columns
    python eval/score.py --unmatched-as off_topic        # alternative conversion rule
    python eval/score.py --split held_out --labels ... --final   # once, at the end

Reads only saved pipeline outputs (question_mapping.json and the stage 4
reports); it never calls a model and never writes anything the pipeline
reads. Output: eval/reports/<run-id>/metrics.json and report.md.

Labels: one row per question with gap_type in
missing / thin / scattered / covered / off_topic, and slide_numbers
(1-indexed, as in PowerPoint; "12; 14-16" is fine). topic_ids is
optional: when empty it is derived from the gold slides.
Rows with an empty gap_type are skipped and counted as unlabelled.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "eval"

from eval.common import GAP_CLASSES, PROJECT_ROOT, SlideEquivalence, config_for_deck, load_config, load_labels, resolve  # noqa: E402
from eval.metrics.calibration import calibration_scores  # noqa: E402
from eval.metrics.detection import binary_detection, gap_type_scores, off_topic_scores  # noqa: E402
from eval.metrics.localization import localization_scores  # noqa: E402
from eval.metrics.stats import cluster_bootstrap  # noqa: E402
from eval.predictions import UNMATCHED_TOPIC, load_pipeline_predictions, topics_for_slides  # noqa: E402

FINAL_LOG = PROJECT_ROOT / "eval" / "reports" / "final_runs.log"


def _git(*args):
    try:
        return subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def score(config: dict, labels: dict, split_ids, unmatched_as: str = "report", n_resamples: int = 1000) -> dict:
    predictions = load_pipeline_predictions(config, unmatched_as)
    mapping = json.loads(resolve(config, "question_mapping").read_text(encoding="utf-8"))
    topics = mapping["topics"]
    equivalence = SlideEquivalence(config.get("equivalent_slide_ranges"))
    split = json.loads(resolve(config, "split").read_text(encoding="utf-8"))
    group_of = {qid: g for g, qids in split["groups"].items() for qid in qids}

    in_split = [qid for qid in split_ids]
    labelled = [qid for qid in in_split if qid in labels and labels[qid]["gap_type"]]
    missing_pred = [qid for qid in labelled if qid not in predictions]
    rows = []
    for qid in labelled:
        lab, pred = labels[qid], predictions.get(qid, {})
        gold_slides = lab["slide_ids"] or []
        gold_topics = lab["topic_ids"] or (topics_for_slides(gold_slides, topics, equivalence) if gold_slides else [])
        rows.append({
            "qid": qid, "group": group_of.get(qid, "unknown"),
            "gold": lab["gap_type"], "pred": pred.get("gap_type"), "confidence": pred.get("confidence"),
            "gold_topics": gold_topics, "topic_ranking": pred.get("topic_ranking", []),
            "gold_slides": gold_slides, "slide_ranking": pred.get("slide_ranking", []),
            "pred_unmatched": pred.get("source_topic") == UNMATCHED_TOPIC,
        })

    gold = [r["gold"] for r in rows]
    pred = [r["pred"] for r in rows]
    groups = [r["group"] for r in rows]
    # Localization only for questions the deck should cover (not off-topic).
    loc_rows = [r for r in rows if r["gold"] != "off_topic"]

    def f1_of(sample):
        d = binary_detection([r["gold"] for r in sample], [r["pred"] for r in sample])
        return d["f1"]

    def macro_of(sample):
        return gap_type_scores([r["gold"] for r in sample], [r["pred"] for r in sample])["macro_f1"]

    def slide_top1_of(sample):
        s = [r for r in sample if r["gold"] != "off_topic"]
        return localization_scores([r["gold_topics"] for r in s], [r["topic_ranking"] for r in s],
                                   [r["gold_slides"] for r in s], [r["slide_ranking"] for r in s], equivalence)["slide_top1"]

    return {
        "counts": {
            "questions_in_split": len(in_split), "labelled": len(rows),
            "unlabelled": len(in_split) - len(labelled), "labelled_without_prediction": len(missing_pred),
            "no_verdict": sum(r["pred"] is None for r in rows),
            "gold_distribution": {c: gold.count(c) for c in sorted(set(gold))},
            "pred_distribution": {str(c): pred.count(c) for c in sorted(set(map(str, pred)))},
            "groups": len(set(groups)),
        },
        "detection": binary_detection(gold, pred),
        "gap_type": gap_type_scores(gold, pred),
        "off_topic": off_topic_scores(gold, [r["pred"] == "off_topic" for r in rows]),
        "stage3_unmatched_vs_off_topic": off_topic_scores(gold, [r["pred_unmatched"] for r in rows]),
        "localization": localization_scores([r["gold_topics"] for r in loc_rows], [r["topic_ranking"] for r in loc_rows],
                                            [r["gold_slides"] for r in loc_rows], [r["slide_ranking"] for r in loc_rows], equivalence),
        "calibration": calibration_scores([r["confidence"] for r in rows], [r["gold"] == r["pred"] for r in rows]),
        "confidence_intervals_95": {
            "detection_f1": cluster_bootstrap(rows, groups, f1_of, n_resamples),
            "gap_type_macro_f1": cluster_bootstrap(rows, groups, macro_of, n_resamples),
            "slide_top1": cluster_bootstrap(rows, groups, slide_top1_of, n_resamples),
        },
        "errors": [
            {"question_id": r["qid"], "gold": r["gold"], "pred": r["pred"], "gold_slide_numbers": [s + 1 for s in r["gold_slides"]],
             "pred_slide_numbers": [s + 1 for s in r["slide_ranking"][:3]]}
            for r in rows if r["gold"] != r["pred"]
        ],
    }


def _fmt(v):
    return "n/a" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))


def render_markdown(meta: dict, m: dict) -> str:
    d, t, l, c, ci = m["detection"], m["gap_type"], m["localization"], m["calibration"], m["confidence_intervals_95"]

    def ci_s(x):
        return f"{_fmt(x['point'])} [{_fmt(x['low'])}, {_fmt(x['high'])}]"

    lines = [
        f"# Evaluation: {meta['deck_id']} / {meta['split']}",
        "",
        *([f"**SYNTHETIC LABELS**: scored against the question generator's intent, not human judgement. "
           "Use for development only; never report as real-student accuracy.", ""]
          if str(meta.get("label_source", "")).startswith("SYNTHETIC") else []),
        f"Run `{meta['run_id']}` at git `{meta['git_revision'][:10] or 'unknown'}`{' (uncommitted changes)' if meta['git_dirty'] else ''}. "
        f"Labels: `{meta['labels']}`. Unmatched questions scored as: `{meta['unmatched_as']}`.",
        "",
        f"Labelled {m['counts']['labelled']} of {m['counts']['questions_in_split']} questions "
        f"({m['counts']['groups']} topic groups). Gold: {m['counts']['gold_distribution']}. "
        f"Predicted: {m['counts']['pred_distribution']}. "
        f"No verdict (failed investigation or no prediction; scored as 'no gap'): {m['counts']['no_verdict']}.",
        "",
        "## Gap detection (gap = missing / thin / scattered)",
        "",
        "| Precision | Recall | F1 [95% CI] | TP | FP | FN | TN |",
        "|---|---|---|---|---|---|---|",
        f"| {_fmt(d['precision'])} | {_fmt(d['recall'])} | {ci_s(ci['detection_f1'])} | {d['tp']} | {d['fp']} | {d['fn']} | {d['tn']} |",
        "",
        f"The system flags {_fmt(d['predicted_gap_rate'])} of questions as gaps; gold rate {_fmt(d['gold_gap_rate'])}. "
        f"Flagging every question as a gap would score F1 {_fmt(d['always_gap_baseline']['f1'])}: "
        "the system's F1 means something only above that.",
        "",
        f"## Gap type: macro-F1 {ci_s(ci['gap_type_macro_f1'])}, accuracy {_fmt(t['accuracy'])}",
        "",
        f"Scored classes: {', '.join(t['classes_scored']) or 'none'}. Absent: {', '.join(t['classes_absent']) or 'none'}.",
        "",
        "| Class | Precision | Recall | F1 | Support |",
        "|---|---|---|---|---|",
        *[f"| {k} | {_fmt(v['precision'])} | {_fmt(v['recall'])} | {_fmt(v['f1'])} | {v['support']} |" for k, v in t["per_class"].items()],
        "",
        "Confusion matrix (rows gold, columns predicted):",
        "",
        "| gold \\ pred | " + " | ".join(next(iter(t["confusion_matrix"].values())).keys()) + " |",
        "|---" * (len(next(iter(t["confusion_matrix"].values()))) + 1) + "|",
        *[f"| {g} | " + " | ".join(str(v) for v in row.values()) + " |" for g, row in t["confusion_matrix"].items()],
        "",
        "## Off-topic",
        "",
        f"Stage 3 'unmatched' as an off-topic detector: precision {_fmt(m['stage3_unmatched_vs_off_topic']['precision'])}, "
        f"recall {_fmt(m['stage3_unmatched_vs_off_topic']['recall'])} (support {m['stage3_unmatched_vs_off_topic']['support']}).",
        "",
        "## Localization (questions the deck should cover)",
        "",
        f"Topic top-1 {_fmt(l['topic_top1'])}, top-3 {_fmt(l['topic_top3'])}, MRR {_fmt(l['topic_mrr'])} (n={l['n_with_gold_topics']}). "
        f"Slide top-1 {ci_s(ci['slide_top1'])}, top-3 {_fmt(l['slide_top3'])} (n={l['n_with_gold_slides']}). Duplicate slides count as the same slide.",
        "",
        "## Calibration of stage 4's self-reported confidence",
        "",
        f"n={c['n']}, ECE {_fmt(c['ece'])}, Brier {_fmt(c['brier'])}, mean confidence {_fmt(c.get('mean_confidence'))} "
        f"vs accuracy {_fmt(c.get('accuracy'))}.",
        "",
        "| Confidence bin | n | Mean confidence | Accuracy |",
        "|---|---|---|---|",
        *[f"| {b['lo']:.1f}-{b['hi']:.1f} | {b['n']} | {b['mean_confidence']:.3f} | {b['accuracy']:.3f} |" for b in c["bins"]],
        "",
        "## Notes",
        "",
        "- Stage 4 judges topics; a question takes the verdict of its highest-scoring mapped topic.",
        "- Confidence intervals resample topic groups, not questions (questions in a topic are correlated).",
        "- Small n: treat every number here as a pilot estimate.",
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deck-id", default=None, help="deck workspace to evaluate (default: $DECK_ID or the only deck)")
    ap.add_argument("--config", type=Path, default=None, help="explicit eval config JSON instead of a deck workspace")
    ap.add_argument("--labels", type=Path, default=None)
    ap.add_argument("--split", choices=["dev", "held_out", "all"], default="dev")
    ap.add_argument("--unmatched-as", choices=["report", "off_topic"], default="report")
    ap.add_argument("--final", action="store_true", help="required to score the held-out split; the run is logged")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--synthetic", action="store_true",
                    help="score every question against the synthetic generator's intent (synthetic decks only); "
                         "results are marked SYNTHETIC and are not human-label results")
    args = ap.parse_args(argv)

    if args.synthetic:
        args.split = "all"  # synthetic intent is not the withheld answer key: no held-out discipline to protect
    elif args.split in ("held_out", "all") and not args.final:
        print("The held-out split is for the final run only. Pass --final if this is that run; it will be logged.")
        return 2
    config = load_config(args.config) if args.config else config_for_deck(args.deck_id)
    if args.synthetic:
        from eval.common import synthetic_labels

        labels_path = synthetic_labels(config["deck_id"])
    else:
        labels_path = args.labels or resolve(config, "labels")
    if not resolve(config, "split").exists():
        print(f"No frozen split for '{config['deck_id']}' yet: python eval/split.py --deck-id {config['deck_id']}")
        return 2
    split = json.loads(resolve(config, "split").read_text(encoding="utf-8"))
    split_ids = split["dev"] + split["held_out"] if args.split == "all" else split[args.split]
    metrics = score(config, load_labels(labels_path), split_ids, args.unmatched_as)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{config['deck_id']}-{'synthetic' if args.synthetic else args.split}"
    meta = {"run_id": run_id, "deck_id": config["deck_id"], "split": args.split, "labels": str(labels_path),
            "label_source": "SYNTHETIC generator intent (not verified)" if args.synthetic else "human",
            "unmatched_as": args.unmatched_as, "git_revision": _git("rev-parse", "HEAD"),
            "git_dirty": bool(_git("status", "--porcelain", "--untracked-files=no"))}
    out = args.out or PROJECT_ROOT / "eval" / "reports" / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(json.dumps({"meta": meta, **metrics}, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "report.md").write_text(render_markdown(meta, metrics), encoding="utf-8")
    if args.final:
        FINAL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with FINAL_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(meta) + "\n")

    c = metrics["counts"]
    print(f"labelled {c['labelled']}/{c['questions_in_split']} questions in the {args.split} split")
    if c["labelled"]:
        d, l = metrics["detection"], metrics["localization"]
        print(f"gap detection  P={_fmt(d['precision'])} R={_fmt(d['recall'])} F1={_fmt(d['f1'])} "
              f"(always-gap baseline F1={_fmt(d['always_gap_baseline']['f1'])})")
        print(f"gap type       macro-F1={_fmt(metrics['gap_type']['macro_f1'])}")
        print(f"localization   slide top-1={_fmt(l['slide_top1'])} topic top-1={_fmt(l['topic_top1'])}")
        print(f"calibration    ECE={_fmt(metrics['calibration']['ece'])}")
    print(f"report: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
