"""Turn the pipeline's outputs into one prediction per question.

Stage 4 gives one verdict per TOPIC; labels are per QUESTION. The
conversion rule is fixed here and reported with every score:

* A question mapped to several topics takes the verdict of its
  highest-scoring topic (stage 3's score). Every mapped topic is kept,
  ranked, for top-k localization.
* A question stage 3 left unmatched gets the verdict of the unmatched
  group's report (topic -1) under ``unmatched_as="report"`` (default:
  what the pipeline actually says), or ``off_topic`` under
  ``unmatched_as="off_topic"`` (treat stage 3's rejection as an
  off-topic prediction). The pipeline has no off_topic class of its own.
  When the unmatched report has per-question verdicts (reports written
  after off_topic was added), each question takes its own verdict
  instead, under either setting.
* A report with confidence 0 is a failed investigation (rate limit,
  request too large, step cap without a verifiable conclusion), not a
  content judgment: its questions get no verdict (gap_type None),
  counted separately by the scorer.
* Confidence is the stage 4 report's self-reported confidence. It is
  not a calibrated probability; calibration.py measures how far off it is.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .common import PIPELINE_TO_PAPER, question_id

UNMATCHED_TOPIC = -1


def load_reports(reports_dir: Path) -> Dict[int, dict]:
    reports = {}
    for path in sorted(Path(reports_dir).glob("topic_*_report.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        reports[int(report["topic_id"])] = report
    return reports


def _verdict(report: Optional[dict]):
    """(paper gap type, confidence) of a report; (None, None) when there
    is no report or the investigation failed."""
    if not report or float(report.get("confidence", 0)) <= 0:
        return None, None
    return PIPELINE_TO_PAPER.get(report["gap_type"], report["gap_type"]), float(report["confidence"])


def build_predictions(mapping: dict, reports: Dict[int, dict], unmatched_as: str = "report") -> Dict[str, dict]:
    if unmatched_as not in ("report", "off_topic"):
        raise ValueError("unmatched_as must be 'report' or 'off_topic'")
    ranked: Dict[str, List[dict]] = {}
    text: Dict[str, str] = {}
    for topic in mapping["topics"]:
        for m in topic["matched_questions"]:
            qid = question_id(m["question"])
            text[qid] = m["question"]
            ranked.setdefault(qid, []).append(
                {"topic_id": int(topic["topic_id"]), "score": float(m["score"]),
                 "best_slide_id": m.get("best_slide_id"), "method": m.get("method")})

    # Per-question verdicts (reports written since they were added): a
    # question takes its OWN verdict from its top topic's report.
    own = {}
    for tid, report in reports.items():
        if _verdict(report)[0] is None:
            continue
        for v in report.get("question_verdicts") or []:
            own[(tid, question_id(v["question"]))] = PIPELINE_TO_PAPER.get(v["verdict"], v["verdict"])

    predictions: Dict[str, dict] = {}
    for qid, candidates in ranked.items():
        candidates.sort(key=lambda c: -c["score"])
        top = candidates[0]
        gap_type, confidence = _verdict(reports.get(top["topic_id"]))
        if (top["topic_id"], qid) in own:
            gap_type = own[(top["topic_id"], qid)]
        predictions[qid] = {
            "question_id": qid,
            "question": text[qid],
            "matched": True,
            "topic_ranking": [c["topic_id"] for c in candidates],
            "slide_ranking": [c["best_slide_id"] for c in candidates if c["best_slide_id"] is not None],
            "gap_type": gap_type,
            "confidence": confidence,
            "source_topic": top["topic_id"],
        }

    unmatched_report = reports.get(UNMATCHED_TOPIC)
    group_type, group_confidence = _verdict(unmatched_report)
    per_question = {}
    if group_type is not None:
        for v in unmatched_report.get("question_verdicts") or []:
            per_question[question_id(v["question"])] = PIPELINE_TO_PAPER.get(v["verdict"], v["verdict"])
    for u in mapping.get("unmatched_questions", []):
        qid = question_id(u["question"])
        if qid in per_question:
            gap_type, confidence = per_question[qid], group_confidence
        elif unmatched_as == "off_topic":
            gap_type, confidence = "off_topic", None
        else:
            gap_type, confidence = group_type, group_confidence
        predictions[qid] = {
            "question_id": qid,
            "question": u["question"],
            "matched": False,
            # The closest topic is still a localization guess, ranked last.
            "topic_ranking": [int(u["best_topic_id"])] if u.get("best_topic_id") is not None else [],
            "slide_ranking": [],
            "gap_type": gap_type,
            "confidence": confidence,
            "source_topic": UNMATCHED_TOPIC,
        }
    return predictions


def topics_for_slides(slide_ids, topics: List[dict], equivalence) -> List[int]:
    """Topics containing any of these slides, either copy of a duplicate."""
    wanted = equivalence.canonical_set(slide_ids)
    out = []
    for t in topics:
        if equivalence.canonical_set(t["slide_ids"]) & wanted:
            out.append(int(t["topic_id"]))
    return out


def load_pipeline_predictions(config: dict, unmatched_as: str = "report") -> Dict[str, dict]:
    from .common import resolve

    mapping = json.loads(resolve(config, "question_mapping").read_text(encoding="utf-8"))
    return build_predictions(mapping, load_reports(resolve(config, "reports_dir")), unmatched_as)
