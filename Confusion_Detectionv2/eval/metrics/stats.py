"""Cluster bootstrap confidence intervals.

Questions about the same topic are not independent, so resampling is
done over groups (topics), not individual questions.
"""
from __future__ import annotations

import random
from typing import Callable, Dict, List, Optional, Sequence


def cluster_bootstrap(items: Sequence, groups: Sequence, statistic: Callable[[List], Optional[float]],
                      n_resamples: int = 1000, seed: int = 2027, alpha: float = 0.05) -> Dict[str, Optional[float]]:
    by_group: Dict = {}
    for item, g in zip(items, groups):
        by_group.setdefault(g, []).append(item)
    keys = sorted(by_group, key=str)
    point = statistic(list(items))
    if len(keys) < 2:
        return {"point": point, "low": None, "high": None, "n_groups": len(keys)}
    rng = random.Random(seed)
    values = []
    for _ in range(n_resamples):
        sample = [x for k in (rng.choice(keys) for _ in keys) for x in by_group[k]]
        v = statistic(sample)
        if v is not None:
            values.append(v)
    if not values:
        return {"point": point, "low": None, "high": None, "n_groups": len(keys)}
    values.sort()
    lo = values[int(alpha / 2 * (len(values) - 1))]
    hi = values[int((1 - alpha / 2) * (len(values) - 1))]
    return {"point": point, "low": lo, "high": hi, "n_groups": len(keys)}
