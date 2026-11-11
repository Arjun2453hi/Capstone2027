"""Gap detection: binary gap/no-gap and the five-way gap type."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from ..common import GAP_CLASSES, GAP_TYPES


def _safe_div(a: float, b: float) -> Optional[float]:
    return a / b if b else None


def prf(tp: int, fp: int, fn: int) -> dict:
    p, r = _safe_div(tp, tp + fp), _safe_div(tp, tp + fn)
    f1 = (2 * p * r / (p + r)) if p is not None and r is not None and (p + r) else (0.0 if p is not None and r is not None else None)
    return {"precision": p, "recall": r, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def binary_detection(gold: Sequence[str], pred: Sequence[Optional[str]]) -> dict:
    """A gap is missing/thin/scattered. covered and off_topic are not gaps.
    A missing prediction counts as "no gap"."""
    tp = fp = fn = tn = 0
    for g, p in zip(gold, pred):
        g_gap, p_gap = g in GAP_CLASSES, p in GAP_CLASSES
        tp += g_gap and p_gap
        fp += (not g_gap) and p_gap
        fn += g_gap and not p_gap
        tn += (not g_gap) and not p_gap
    out = prf(tp, fp, fn)
    out.update(tn=tn, n=len(gold), accuracy=_safe_div(tp + tn, len(gold)),
               gold_gap_rate=_safe_div(tp + fn, len(gold)),
               predicted_gap_rate=_safe_div(tp + fp, len(gold)))
    # Trivial reference: flag every question as a gap. A system that flags
    # most questions can score a high F1 without discriminating at all.
    out["always_gap_baseline"] = prf(tp + fn, fp + tn, 0)
    return out


def confusion_matrix(gold: Sequence[str], pred: Sequence[Optional[str]], classes=GAP_TYPES) -> Dict[str, Dict[str, int]]:
    cols = list(classes) + ["none"]
    matrix = {g: {p: 0 for p in cols} for g in classes}
    for g, p in zip(gold, pred):
        matrix[g][p if p in classes else "none"] += 1
    return matrix


def gap_type_scores(gold: Sequence[str], pred: Sequence[Optional[str]], classes=GAP_TYPES) -> dict:
    """Per-class P/R/F1 and macro-F1. Macro-F1 averages only classes that
    occur in gold or predictions; absent classes are listed, not scored."""
    per_class = {}
    for c in classes:
        tp = sum(g == c and p == c for g, p in zip(gold, pred))
        fp = sum(g != c and p == c for g, p in zip(gold, pred))
        fn = sum(g == c and p != c for g, p in zip(gold, pred))
        per_class[c] = prf(tp, fp, fn) | {"support": tp + fn}
    present = [c for c in classes if per_class[c]["support"] or per_class[c]["fp"]]
    f1s = [per_class[c]["f1"] or 0.0 for c in present]
    return {
        "macro_f1": sum(f1s) / len(f1s) if f1s else None,
        "accuracy": _safe_div(sum(g == p for g, p in zip(gold, pred)), len(gold)),
        "classes_scored": present,
        "classes_absent": [c for c in classes if c not in present],
        "per_class": per_class,
        "confusion_matrix": confusion_matrix(gold, pred, classes),
    }


def off_topic_scores(gold: Sequence[str], pred_off_topic: Sequence[bool]) -> dict:
    tp = sum(g == "off_topic" and p for g, p in zip(gold, pred_off_topic))
    fp = sum(g != "off_topic" and p for g, p in zip(gold, pred_off_topic))
    fn = sum(g == "off_topic" and not p for g, p in zip(gold, pred_off_topic))
    return prf(tp, fp, fn) | {"support": tp + fn}
