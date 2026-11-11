"""Freeze a dev / held-out split of the questions, and write blank
labelling forms.

    python eval/split.py --deck-id <deck_id>

Questions are split by GROUP, not one by one: a group is the topic a
question maps to, with the two copies of the duplicated lecture merged
into one group, and stage 3's unmatched questions as their own group.
Otherwise near-identical questions about the same slides would land on
both sides and the held-out score would be inflated.

Use only the dev split for tuning prompts and thresholds. Score the
held-out split once, at the end (score.py --split held_out --final).

The split is frozen: rerunning refuses to overwrite it unless --force.
The forms contain no predictions, so labels are not anchored to what the
system said.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "eval"

from eval.common import PROJECT_ROOT, SlideEquivalence, config_for_deck, load_config, load_questions, question_id, resolve  # noqa: E402
from eval.predictions import UNMATCHED_TOPIC, load_pipeline_predictions  # noqa: E402

FORM_COLUMNS = ["question_id", "question", "gap_type", "slide_numbers", "topic_ids", "annotator", "notes"]


def topic_groups(topics, equivalence) -> dict:
    """topic_id -> group id. Topics sharing a (canonical) slide are one group."""
    parent = {int(t["topic_id"]): int(t["topic_id"]) for t in topics}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner = {}
    for t in topics:
        for s in equivalence.canonical_set(t["slide_ids"]):
            if s in owner:
                parent[find(int(t["topic_id"]))] = find(owner[s])
            else:
                owner[s] = int(t["topic_id"])
    return {tid: f"topics_{find(tid)}" for tid in parent}


def make_split(questions, predictions, topics, equivalence, held_out_fraction, seed) -> dict:
    groups_of_topic = topic_groups(topics, equivalence)
    group_of = {}
    for q in questions:
        qid = question_id(q)
        pred = predictions.get(qid)
        if pred is None or pred["source_topic"] == UNMATCHED_TOPIC:
            group_of[qid] = "unmatched"
        else:
            group_of[qid] = groups_of_topic.get(pred["source_topic"], f"topics_{pred['source_topic']}")
    members = {}
    for qid, g in group_of.items():
        members.setdefault(g, []).append(qid)
    order = sorted(members)
    random.Random(seed).shuffle(order)
    target = held_out_fraction * len(questions)
    held_out, dev = [], []
    for g in order:
        (held_out if sum(len(members[x]) for x in held_out) < target else dev).append(g)
    text = {question_id(q): q for q in questions}
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "held_out_fraction": held_out_fraction,
        "grouping": "stage-3 top topic; duplicate-lecture topics merged; unmatched questions are one group",
        "groups": {g: sorted(members[g]) for g in sorted(members)},
        "dev": sorted(q for g in dev for q in members[g]),
        "held_out": sorted(q for g in held_out for q in members[g]),
        "questions": text,
    }


def write_form(path: Path, question_ids, text, seed) -> None:
    rows = list(question_ids)
    random.Random(seed + 1).shuffle(rows)  # not grouped by the system's topics
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FORM_COLUMNS)
        w.writeheader()
        for qid in rows:
            w.writerow({"question_id": qid, "question": text[qid]})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deck-id", default=None, help="deck workspace to evaluate (default: $DECK_ID or the only deck)")
    ap.add_argument("--config", type=Path, default=None, help="explicit eval config JSON instead of a deck workspace")
    ap.add_argument("--force", action="store_true", help="overwrite an existing frozen split")
    args = ap.parse_args(argv)

    config = load_config(args.config) if args.config else config_for_deck(args.deck_id)
    split_path = resolve(config, "split")
    if split_path.exists() and not args.force:
        print(f"Split already frozen at {split_path}. Use --force only if you mean to discard it.")
        return 1
    questions = load_questions(resolve(config, "questions"))
    predictions = load_pipeline_predictions(config)
    topics = json.loads(resolve(config, "question_mapping").read_text(encoding="utf-8"))["topics"]
    equivalence = SlideEquivalence(config.get("equivalent_slide_ranges"))
    split = make_split(questions, predictions, topics, equivalence, config["held_out_fraction"], config["seed"])

    split_path.parent.mkdir(parents=True, exist_ok=True)
    split_path.write_text(json.dumps(split, ensure_ascii=False, indent=1), encoding="utf-8")
    gold_dir = resolve(config, "labels").parent
    deck_id = config["deck_id"]
    write_form(gold_dir / f"{deck_id}.dev.labels.csv", split["dev"], split["questions"], config["seed"])
    write_form(gold_dir / f"{deck_id}.held_out.labels.csv", split["held_out"], split["questions"], config["seed"])
    print(f"{len(questions)} questions in {len(split['groups'])} groups -> "
          f"dev {len(split['dev'])}, held-out {len(split['held_out'])}")
    print(f"split: {split_path}")
    print(f"forms: {gold_dir / f'{deck_id}.dev.labels.csv'}, {gold_dir / f'{deck_id}.held_out.labels.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
