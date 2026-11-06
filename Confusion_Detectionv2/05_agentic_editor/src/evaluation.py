"""Offline editor evaluation. No model calls and no inferred human labels."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Optional

CRITERIA = ("factual_correctness", "purpose_fulfilled", "readability", "useful_change")
OPERATIONS = ("insert_slide_after", "append_bullets", "repair_slide")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def snapshot_edits(snapshot):
    """Read both saved plans and the demo's reviewed-suggestions format.

    'accepted' is deliberately called a recorded gate outcome: older demo
    artifacts can include manual decisions among the same problem strings.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("Input snapshot must be a JSON object")
    if "edits" in snapshot:
        edits = snapshot["edits"]
    elif "topics" in snapshot or "repairs" in snapshot:
        edits = [item for group in ("topics", "repairs")
                 for key in sorted(snapshot.get(group, {}))
                 for item in snapshot[group][key]]
    else:
        raise ValueError("Expected an edit plan or reviewed suggestions, not a change log.")
    if not isinstance(edits, list):
        raise ValueError("edits must be a list")
    result = []
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise ValueError("Each edit must be an object")
        for field in ("topic_id", "after_slide_id", "title", "bullets", "addresses_questions", "problems"):
            if field not in edit:
                raise ValueError(f"Edit {i} is missing {field}")
        if type(edit.get("accepted")) is not bool:
            raise ValueError(f"Edit {i}: accepted must be a boolean")
        if (not isinstance(edit["problems"], list)
                or any(not isinstance(p, str) for p in edit["problems"])):
            raise ValueError(f"Edit {i}: problems must be a list of strings")
        if edit["accepted"] == bool(edit["problems"]):
            raise ValueError(f"Edit {i}: accepted contradicts problems")
        revisions = edit.get("revisions", 0)
        if type(revisions) is not int or revisions < 0:
            raise ValueError(f"Edit {i}: revisions must be a nonnegative integer")
        operation = edit.get("operation", "insert_slide_after")
        if operation not in OPERATIONS:
            raise ValueError(f"Edit {i}: unsupported operation {operation}")
        if (not isinstance(edit["addresses_questions"], list)
                or any(not isinstance(q, str) for q in edit["addresses_questions"])):
            raise ValueError(f"Edit {i}: addresses_questions must be a list of strings")
        result.append({**edit, "operation": operation, "revisions": revisions,
                       "edit_id": f"{i:04d}-{_digest(edit)[:16]}"})
    return result


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def review_template(snapshot, questions=()):
    edits = snapshot_edits(snapshot)
    questions = list(dict.fromkeys(q.strip() for q in questions if q.strip()))
    return {
        "schema_version": 1, "snapshot_id": _digest(snapshot),
        "reviewer": "", "original_deck": "", "edited_deck": "",
        "rubric": {
            "factual_correctness": "All statements are technically correct; verify against trusted references.",
            "purpose_fulfilled": "Gap edits resolve the cited confusion; repairs preserve the original meaning.",
            "readability": "Text and rendered slide are clear and readable in the resulting deck.",
            "useful_change": "Adds useful explanation without unnecessary duplication, or fixes the broken layout.",
            "labels": "Set each criterion to true/false. All four true means usable without substantive correction. Leave null until reviewed.",
            "coverage": "Independently assess adequate answers in the original and actual edited deck. Do not copy addresses_questions as labels.",
        },
        "edits": [{"edit_id": e["edit_id"], "topic_id": e["topic_id"],
                   "operation": e["operation"], "after_slide_id": e["after_slide_id"],
                   "title": e["title"], "bullets": e["bullets"],
                   "addresses_questions": e["addresses_questions"],
                   "criteria": {key: None for key in CRITERIA}, "notes": ""} for e in edits],
        "questions": [{"question": q, "before": None, "after": None, "notes": ""} for q in questions],
    }


def _nullable_bool(value, label):
    if value is not None and type(value) is not bool:
        raise ValueError(f"{label} must be true, false, or null")


def evaluate(snapshot, reviews: Optional[dict] = None):
    edits = snapshot_edits(snapshot)
    accepted = [e for e in edits if e["accepted"]]
    revised = [e for e in edits if e["revisions"] > 0]
    problems = Counter(p.split(":", 1)[0] for e in edits for p in e["problems"])
    report = {
        "schema_version": 1, "snapshot_id": _digest(snapshot),
        "scope": "Final saved proposals, including blocked proposals; generation failures and discarded drafts are not recorded here.",
        "automatic": {
            "proposals": len(edits), "accepted": len(accepted), "blocked": len(edits) - len(accepted),
            "recorded_pass_rate": ratio(len(accepted), len(edits)),
            "revised_proposal_rate": ratio(len(revised), len(edits)),
            "accepted_among_revised": ratio(sum(e["accepted"] for e in revised), len(revised)),
            "mean_recorded_revision_round": ratio(sum(e["revisions"] for e in edits), len(edits)),
            "topics_with_accepted_gap_edits": len({e["topic_id"] for e in accepted if e["operation"] != "repair_slide"}),
            "claimed_questions_in_accepted_edits": len({q for e in accepted for q in e["addresses_questions"]}),
            "by_operation": {op: {"proposals": sum(e["operation"] == op for e in edits),
                                  "accepted": sum(e["operation"] == op for e in accepted)} for op in OPERATIONS},
            "problem_categories": dict(sorted(problems.items())),
        },
        "human": None,
        "limitations": [
            "Recorded acceptance may include earlier manual review; it is not an independent correctness score.",
            "Claimed question counts are model assertions, not measured question coverage.",
            "Revision counts describe saved final proposals, not all attempted calls or first-pass success.",
            "Mirrored copies are not counted as additional proposals.",
        ],
    }
    if reviews is None:
        return report
    if not isinstance(reviews, dict):
        raise ValueError("Review form must be a JSON object")
    if reviews.get("schema_version") != 1 or reviews.get("snapshot_id") != report["snapshot_id"]:
        raise ValueError("Review version/snapshot does not match this input. Generate a new template.")
    by_id = {e["edit_id"]: e for e in edits}
    rows = reviews.get("edits", [])
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("Review edits must be a list of objects")
    ids = [r["edit_id"] for r in rows]
    if len(set(ids)) != len(ids) or set(ids) != set(by_id):
        raise ValueError("Reviews must contain every proposal exactly once, including blocked proposals.")
    complete, usable = [], []
    criteria_counts = {key: [0, 0] for key in CRITERIA}
    any_labels = False
    for row in rows:
        criteria = row.get("criteria", {})
        if not isinstance(criteria, dict) or set(criteria) != set(CRITERIA):
            raise ValueError("Each edit review must contain all four rubric criteria")
        for key in CRITERIA:
            value = criteria[key]
            _nullable_bool(value, key)
            if value is not None:
                any_labels = True
                criteria_counts[key][0] += int(value)
                criteria_counts[key][1] += 1
        if all(criteria[key] is not None for key in CRITERIA):
            complete.append(row["edit_id"])
            if all(criteria.values()):
                usable.append(row["edit_id"])
    questions = reviews.get("questions", [])
    if not isinstance(questions, list) or any(not isinstance(r, dict) for r in questions):
        raise ValueError("Review questions must be a list of objects")
    seen, pairs = set(), []
    for row in questions:
        question = row.get("question")
        if not isinstance(question, str) or not question.strip() or question.strip() in seen:
            raise ValueError("Coverage questions must be nonempty and unique")
        seen.add(question.strip())
        for key in ("before", "after"):
            _nullable_bool(row.get(key), key)
            any_labels = any_labels or row.get(key) is not None
        if row.get("before") is not None and row.get("after") is not None:
            pairs.append(row)
    if any_labels and not str(reviews.get("reviewer") or "").strip():
        raise ValueError("Name the reviewer when supplying human labels")
    if any(r.get(k) is not None for r in questions for k in ("before", "after")):
        if not all(str(reviews.get(k) or "").strip() for k in ("original_deck", "edited_deck")):
            raise ValueError("Identify original_deck and edited_deck for coverage labels")
    complete_accepted = [key for key in complete if by_id[key]["accepted"]]
    usable_accepted = [key for key in usable if by_id[key]["accepted"]]
    before = sum(r["before"] for r in pairs)
    after = sum(r["after"] for r in pairs)
    report["human"] = {
        "reviewer": reviews.get("reviewer"),
        "edit_review_completion": ratio(len(complete), len(edits)),
        "usable_rate_among_reviewed": ratio(len(usable), len(complete)),
        "confirmed_usable_yield_all_proposals": ratio(len(usable), len(edits)),
        "accepted_edit_review_completion": ratio(len(complete_accepted), len(accepted)),
        "usable_rate_among_reviewed_accepted": ratio(len(usable_accepted), len(complete_accepted)),
        "criterion_pass_rates": {key: ratio(*counts) for key, counts in criteria_counts.items()},
        "coverage": {
            "original_deck": reviews.get("original_deck"), "edited_deck": reviews.get("edited_deck"),
            "paired_review_completion": ratio(len(pairs), len(questions)),
            "before": ratio(before, len(pairs)), "after": ratio(after, len(pairs)),
            "improvement_percentage_points": 100 * (after - before) / len(pairs) if pairs else None,
            "newly_answered": sum(not r["before"] and r["after"] for r in pairs),
            "regressions": sum(r["before"] and not r["after"] for r in pairs),
        },
    }
    report["limitations"].append("Human rates use labeled subsets; review completion must accompany them. Coverage uses only paired labels on the named decks.")
    return report


def render_markdown(report):
    def show(metric):
        if metric["value"] is None:
            return f"Not measured (0/{metric['denominator']})"
        return f"{100 * metric['value']:.1f}% ({metric['numerator']}/{metric['denominator']})"

    a = report["automatic"]
    lines = ["# Agentic editor evaluation", "", report["scope"], "",
             "| Metric | Result |", "|---|---|",
             f"| Saved proposals | {a['proposals']} |",
             f"| Recorded pass rate | {show(a['recorded_pass_rate'])} |",
             f"| Blocked proposals | {a['blocked']} |",
             f"| Proposals with revisions | {show(a['revised_proposal_rate'])} |",
             f"| Accepted among revised proposals | {show(a['accepted_among_revised'])} |",
             f"| Distinct questions claimed by accepted edits (unverified) | {a['claimed_questions_in_accepted_edits']} |"]
    human = report["human"]
    if human:
        lines += [f"| Human edit review completion | {show(human['edit_review_completion'])} |",
                  f"| Usable among reviewed proposals | {show(human['usable_rate_among_reviewed'])} |",
                  f"| Confirmed usable yield across all proposals | {show(human['confirmed_usable_yield_all_proposals'])} |",
                  f"| Accepted edit review completion | {show(human['accepted_edit_review_completion'])} |",
                  f"| Usable among reviewed accepted proposals | {show(human['usable_rate_among_reviewed_accepted'])} |"]
        for key, value in human["criterion_pass_rates"].items():
            lines.append(f"| Human {key.replace('_', ' ')} | {show(value)} |")
        c = human["coverage"]
        delta = c["improvement_percentage_points"]
        lines += [f"| Paired question review completion | {show(c['paired_review_completion'])} |",
                  f"| Original deck coverage (paired questions) | {show(c['before'])} |",
                  f"| Edited deck coverage (paired questions) | {show(c['after'])} |",
                  f"| Coverage improvement | {f'{delta:+.1f} percentage points' if delta is not None else 'Not measured'} |",
                  f"| Newly answered / regressions | {c['newly_answered']} / {c['regressions']} |",
                  "", f"Reviewer: {human['reviewer'] or 'Not supplied'}",
                  f"Original deck: {c['original_deck'] or 'Not supplied'}",
                  f"Edited deck: {c['edited_deck'] or 'Not supplied'}"]
    else:
        lines += ["", "Human usability, factual correctness, and coverage improvement: **Not measured**."]
    lines += ["", "## Interpretation", ""] + [f"- {s}" for s in report["limitations"]]
    lines += ["", f"Snapshot SHA-256: `{report['snapshot_id']}`", ""]
    return "\n".join(lines)
