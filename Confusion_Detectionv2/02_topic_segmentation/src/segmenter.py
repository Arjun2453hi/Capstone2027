"""segmenter.py — TopicSegmenter orchestrator.

Combines the content-dominant depth score (similarity.py) with the
minor, bounded structural boost (structural.py), cuts at an adaptive
threshold computed from this deck's own score distribution, merges
short segments, and assembles the final Topic list. Depends on the
`EmbeddingModel` interface only, never a concrete embedding library
directly (common/embeddings' contract).
"""
from __future__ import annotations

import math
from typing import List, Tuple

import numpy as np

from ._upstream import find_near_duplicate_groups
from .schema import Topic
from .similarity import combined_depth, cosine_similarity
from .structural import compute_structural_boost_mask, is_divider_like

# "No more than ~10-15% of the typical depth-score range for this deck"
# (claude.md Section 3) -- picked the middle of that range as a
# starting default; tune empirically per Section 3's own instruction.
STRUCTURAL_BOOST_FRACTION = 0.12

# threshold = mean + THRESHOLD_Z_SCORE * stdev of this deck's own
# boundary-score distribution (claude.md Section 4). Lowered from an
# initial 1.0 to 0.5 on direct request, for a more sensitive cut --
# catches subtler dips at the cost of a few more borderline/spurious
# boundaries (which min-segment merging then cleans up). Still a
# tunable first draft per Section 5.1's own framing, not a final value.
THRESHOLD_Z_SCORE = 0.5

# "e.g. 2-3 slides" (claude.md Section 4's post-processing note).
MIN_SEGMENT_LENGTH = 3

# Scale-coverage-bucketed threshold (bugfix, see CLAUDE.md's "Stage 2
# boundary gap" section): similarity.py's own documented edge-handling
# rule means the largest scale (k=round(sqrt(N)), the strongest
# discriminator for a real topic-level shift -- claude.md Section 2.3)
# is structurally unavailable for roughly the first/last k positions of
# any deck -- there simply aren't enough slides beyond the edge to
# compute that scale's block similarity there. A single threshold
# computed across the WHOLE deck is dominated by full-scale-coverage
# positions (most of the deck), silently setting the bar higher than an
# edge position can ever structurally reach, even for a genuinely real
# topic shift there. Measured on the real deck: topic 17's internal
# Kubernetes-general/-specific split scored 0.55 against a global
# threshold of 0.66 (never cut), while comparable real interior cuts
# cleared 0.7-0.9 on the strength of the largest scale alone.
#
# Fix: judge each position only against others in the same scale-
# coverage bucket (has the largest scale contributed, or not), rather
# than a single deck-wide pool. Tried a plain sliding-window local
# threshold first -- rejected: it also re-judges comfortably-interior,
# full-coverage positions against a much smaller local sample (missing
# the deck's occasional large peaks, which lower the global std),
# silently lowering the bar almost everywhere and not just at the
# edges -- verified this by measuring it directly: it produced 26
# topics on the real deck instead of 18, almost all spurious.
MIN_BUCKET_SAMPLES = 5  # below this many valid samples in a coverage bucket, fall back to the global threshold

# Slide-level near-duplicate detection: only substantial content
# repeating counts (a two-word slide title matching elsewhere isn't
# meaningful evidence of a boundary the way a whole repeated template
# slide is).
DUPLICATE_MIN_CHARS = 40


def _l2_normalize(embeddings: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return embeddings / norms


def _default_scales(n: int) -> List[int]:
    return sorted(set([2, 3, round(math.sqrt(n))]))


def _scale_coverage_thresholds(
    boundary_score: np.ndarray,
    depth_by_scale: dict,
    scales: List[int],
    z_score: float,
    min_bucket_samples: int = MIN_BUCKET_SAMPLES,
) -> np.ndarray:
    """Per-position adaptive threshold, computed separately for
    positions that do vs don't have the largest scale's contribution --
    see MIN_BUCKET_SAMPLES' comment above for why a single deck-wide
    threshold, or a plain sliding-window local one, both get this
    wrong. Falls back to the deck-wide (global) threshold wherever a
    bucket doesn't have enough samples to trust on its own (e.g. a very
    short deck).
    """
    n = len(boundary_score)
    global_valid = boundary_score[~np.isnan(boundary_score)]
    global_threshold = (
        float(global_valid.mean() + z_score * global_valid.std()) if len(global_valid) else float("inf")
    )

    largest_depth = depth_by_scale[max(scales)]
    full_mask = ~np.isnan(largest_depth)
    reduced_mask = np.isnan(largest_depth) & ~np.isnan(boundary_score)

    def _bucket_threshold(mask: np.ndarray):
        values = boundary_score[mask]
        values = values[~np.isnan(values)]
        if len(values) < min_bucket_samples:
            return None
        return float(values.mean() + z_score * values.std())

    full_threshold = _bucket_threshold(full_mask)
    reduced_threshold = _bucket_threshold(reduced_mask)

    thresholds = np.full(n, global_threshold)
    if full_threshold is not None:
        thresholds[full_mask] = full_threshold
    if reduced_threshold is not None:
        thresholds[reduced_mask] = reduced_threshold
    return thresholds


class TopicSegmenter:
    def __init__(
        self,
        model,
        min_segment_length: int = MIN_SEGMENT_LENGTH,
        structural_boost_fraction: float = STRUCTURAL_BOOST_FRACTION,
        threshold_z_score: float = THRESHOLD_Z_SCORE,
    ):
        self.model = model
        self.min_segment_length = min_segment_length
        self.structural_boost_fraction = structural_boost_fraction
        self.threshold_z_score = threshold_z_score

    def segment(self, deck) -> Tuple[List[Topic], dict]:
        """Returns (topics, diagnostics). `diagnostics` carries the
        combined-depth curve, boundary-score curve, threshold, and the
        structural-boost mask -- everything the required visualization
        (claude.md Section 6) needs to plot."""
        slides = deck.slides
        n = len(slides)
        if n < 2:
            if n == 0:
                return [], {
                    "scales": [], "combined_depth": np.array([]), "boundary_score": np.array([]),
                    "threshold": float("inf"), "thresholds": np.array([]), "boost_mask": [],
                }
            topic = Topic(0, slides[0].slide_id, slides[0].slide_id, [slides[0].slide_id], 0.0)
            return [topic], {
                "scales": [], "combined_depth": np.array([]), "boundary_score": np.array([]),
                "threshold": float("inf"), "boost_mask": [],
            }

        texts = [s.raw_text for s in slides]
        self.model.fit(texts)
        embeddings = _l2_normalize(np.asarray(self.model.embed(texts)))

        scales = _default_scales(n)
        depth_by_scale = combined_depth(embeddings, scales)
        stacked = np.vstack(list(depth_by_scale.values()))
        with np.errstate(all="ignore"):
            combined = np.nanmax(stacked, axis=0)  # length n-1, one per boundary

        duplicate_groups = find_near_duplicate_groups(
            [s.raw_text for s in slides], min_chars=DUPLICATE_MIN_CHARS
        )
        duplicate_indices = {idx for idxs in duplicate_groups.values() for idx in idxs}
        boost_mask = compute_structural_boost_mask(slides, duplicate_indices)

        valid_combined = combined[~np.isnan(combined)]
        depth_range = float(valid_combined.max() - valid_combined.min()) if len(valid_combined) else 0.0
        boost_cap = self.structural_boost_fraction * depth_range

        boundary_score = combined.copy()
        for i in range(len(boundary_score)):
            # A NaN position has no content signal at all (not even a
            # weak one) -- claude.md Section 3: no structural signal may
            # create a boundary the content signal doesn't at least
            # weakly support, so a boost never lifts a NaN into a cut.
            if boost_mask[i] and not np.isnan(boundary_score[i]):
                boundary_score[i] += boost_cap

        valid_scores = boundary_score[~np.isnan(boundary_score)]
        global_threshold = (
            float(valid_scores.mean() + self.threshold_z_score * valid_scores.std())
            if len(valid_scores)
            else float("inf")
        )
        thresholds = (
            _scale_coverage_thresholds(boundary_score, depth_by_scale, scales, self.threshold_z_score)
            if len(valid_scores)
            else np.full(len(boundary_score), float("inf"))
        )

        # Empty-slide boundary-candidacy exclusion (chosen design
        # decision): a boundary flanked by an image-only/blank slide on
        # either side never becomes an actual cut, regardless of score
        # -- there isn't enough real content there to trust the call.
        # The slide still participates normally in neighboring blocks'
        # averages (nothing here masks it out of `embeddings`) and
        # still ends up inside whichever topic surrounds it.
        candidate_mask = [
            not slides[i].is_empty() and not slides[i + 1].is_empty() for i in range(n - 1)
        ]

        cut_positions = [
            i
            for i in range(len(boundary_score))
            if candidate_mask[i] and not np.isnan(boundary_score[i]) and boundary_score[i] > thresholds[i]
        ]

        topics = self._build_topics(slides, cut_positions, boundary_score)
        topics = self._merge_short_segments(topics, embeddings, slides)

        diagnostics = {
            "scales": scales,
            "combined_depth": combined,
            "boundary_score": boundary_score,
            "threshold": global_threshold,  # scalar, for simple reporting/back-compat
            "thresholds": thresholds,  # per-position array, what cut_positions actually used
            "boost_mask": boost_mask,
            "candidate_mask": candidate_mask,
        }
        return topics, diagnostics

    @staticmethod
    def _build_topics(slides, cut_positions: List[int], boundary_score: np.ndarray) -> List[Topic]:
        n = len(slides)
        starts = [0] + [i + 1 for i in cut_positions]
        topics = []
        for topic_id, start in enumerate(starts):
            end = (starts[topic_id + 1] - 1) if topic_id + 1 < len(starts) else n - 1
            confidence = 0.0 if topic_id == 0 else float(boundary_score[start - 1])
            topics.append(
                Topic(
                    topic_id=topic_id,
                    start_slide_id=slides[start].slide_id,
                    end_slide_id=slides[end].slide_id,
                    slide_ids=[slides[j].slide_id for j in range(start, end + 1)],
                    boundary_confidence=confidence,
                )
            )
        return topics

    def _merge_short_segments(self, topics: List[Topic], embeddings: np.ndarray, slides) -> List[Topic]:
        """Merge any segment shorter than min_segment_length into
        whichever neighbor it's more similar to (claude.md Section 4's
        post-processing step), to avoid spurious single-slide segments
        from noisy threshold crossings. Exception: a short segment made
        up entirely of divider slides defaults to merging left -- see
        the inline comment below for why."""
        slide_id_to_row = {s.slide_id: row for row, s in enumerate(slides)}
        slides_by_id = {s.slide_id: s for s in slides}

        def mean_embedding(topic: Topic) -> np.ndarray:
            rows = [slide_id_to_row[sid] for sid in topic.slide_ids]
            return embeddings[rows].mean(axis=0)

        topics = list(topics)
        while True:
            short_idx = next(
                (i for i, t in enumerate(topics) if len(t.slide_ids) < self.min_segment_length and len(topics) > 1),
                None,
            )
            if short_idx is None:
                break

            topic = topics[short_idx]
            topic_mean = mean_embedding(topic)

            # A short segment made up entirely of divider slides (e.g. a
            # single "THANK YOU") has no real topical content of its
            # own -- its embedding is close to whatever it happens to
            # resemble by chance, which is noise, not signal. A bare
            # sign-off conventionally *closes* the section before it
            # far more often than it *opens* the one after (found via a
            # real case: "THANK YOU" measured 0.20 similarity to its
            # left neighbor and 0.67 to its right, and merging right put
            # a Docker-section closer at the front of an unrelated
            # DevOps topic). So: default to the left neighbor for a
            # pure-divider short segment, skipping the similarity
            # comparison entirely, unless there's no left neighbor to
            # merge into at all.
            only_dividers = all(is_divider_like(slides_by_id[sid]) for sid in topic.slide_ids)
            if only_dividers and short_idx > 0:
                merge_left = True
            elif only_dividers:  # no left neighbor -- must merge right
                merge_left = False
            else:
                left_sim = cosine_similarity(topic_mean, mean_embedding(topics[short_idx - 1])) if short_idx > 0 else -2.0
                right_sim = (
                    cosine_similarity(topic_mean, mean_embedding(topics[short_idx + 1]))
                    if short_idx < len(topics) - 1
                    else -2.0
                )
                merge_left = left_sim >= right_sim

            if merge_left:
                left = topics[short_idx - 1]
                topics[short_idx - 1] = Topic(
                    topic_id=left.topic_id,
                    start_slide_id=left.start_slide_id,
                    end_slide_id=topic.end_slide_id,
                    slide_ids=left.slide_ids + topic.slide_ids,
                    boundary_confidence=left.boundary_confidence,
                )
            else:
                right = topics[short_idx + 1]
                topics[short_idx + 1] = Topic(
                    topic_id=right.topic_id,
                    start_slide_id=topic.start_slide_id,
                    end_slide_id=right.end_slide_id,
                    slide_ids=topic.slide_ids + right.slide_ids,
                    # The merged span's start is still the short topic's
                    # original start -- keep *its* triggering score, not
                    # the neighbor's, since that's still the cut that
                    # actually begins this (now longer) segment.
                    boundary_confidence=topic.boundary_confidence,
                )
            del topics[short_idx]

        for new_id, t in enumerate(topics):
            t.topic_id = new_id
        return topics
