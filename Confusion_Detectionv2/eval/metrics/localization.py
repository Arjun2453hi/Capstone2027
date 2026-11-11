"""Localization: did the system point at the right topic and slide?

Stage 3 records one best slide per mapped topic, so the slide ranking is
those best slides ordered by mapping score. Duplicate slides count as
the same slide (SlideEquivalence).
"""
from __future__ import annotations

from typing import List, Optional, Sequence


def hit_at_k(gold_sets: Sequence[set], rankings: Sequence[List[int]], k: int) -> Optional[float]:
    pairs = [(g, r) for g, r in zip(gold_sets, rankings) if g]
    if not pairs:
        return None
    return sum(bool(set(r[:k]) & g) for g, r in pairs) / len(pairs)


def mrr(gold_sets: Sequence[set], rankings: Sequence[List[int]]) -> Optional[float]:
    pairs = [(g, r) for g, r in zip(gold_sets, rankings) if g]
    if not pairs:
        return None
    total = 0.0
    for g, r in pairs:
        for i, item in enumerate(r, 1):
            if item in g:
                total += 1.0 / i
                break
    return total / len(pairs)


def localization_scores(gold_topics, topic_rankings, gold_slides, slide_rankings, equivalence) -> dict:
    canon_gold_slides = [equivalence.canonical_set(s) if s else set() for s in gold_slides]
    canon_rankings = [[equivalence.canonical(s) for s in r] for r in slide_rankings]
    gold_topic_sets = [set(t) if t else set() for t in gold_topics]
    return {
        "topic_top1": hit_at_k(gold_topic_sets, topic_rankings, 1),
        "topic_top3": hit_at_k(gold_topic_sets, topic_rankings, 3),
        "topic_mrr": mrr(gold_topic_sets, topic_rankings),
        "slide_top1": hit_at_k(canon_gold_slides, canon_rankings, 1),
        "slide_top3": hit_at_k(canon_gold_slides, canon_rankings, 3),
        "n_with_gold_topics": sum(bool(g) for g in gold_topic_sets),
        "n_with_gold_slides": sum(bool(g) for g in canon_gold_slides),
    }
