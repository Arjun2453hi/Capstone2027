"""Is the reported confidence a usable probability?

Confidence is stage 4's self-reported number for its gap-type verdict,
so "correct" here means the predicted gap type equals the gold type.
With few labelled questions these estimates are noisy; n is reported.
"""
from __future__ import annotations

from typing import List, Optional, Sequence


def calibration_scores(confidences: Sequence[Optional[float]], correct: Sequence[bool], n_bins: int = 5) -> dict:
    pairs = [(c, bool(k)) for c, k in zip(confidences, correct) if c is not None]
    if not pairs:
        return {"n": 0, "ece": None, "brier": None, "bins": []}
    bins: List[dict] = []
    ece = 0.0
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        members = [(c, k) for c, k in pairs if (lo <= c < hi) or (b == n_bins - 1 and c == 1.0)]
        if not members:
            continue
        mean_conf = sum(c for c, _ in members) / len(members)
        acc = sum(k for _, k in members) / len(members)
        ece += len(members) / len(pairs) * abs(acc - mean_conf)
        bins.append({"lo": lo, "hi": hi, "n": len(members), "mean_confidence": mean_conf, "accuracy": acc})
    brier = sum((c - k) ** 2 for c, k in pairs) / len(pairs)
    return {"n": len(pairs), "ece": ece, "brier": brier,
            "mean_confidence": sum(c for c, _ in pairs) / len(pairs),
            "accuracy": sum(k for _, k in pairs) / len(pairs), "bins": bins}
