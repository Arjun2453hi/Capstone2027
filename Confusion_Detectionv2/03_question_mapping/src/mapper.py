"""mapper.py — QuestionMapper orchestrator.

Depends on EmbeddingModel and AmbiguityResolver as interfaces only,
never a concrete embedding library or LLM client directly. Semantic
similarity resolves the large majority of questions cheaply; the LLM
fallback is scoped to at most one call per question (only when even the
single best-scoring slide is ambiguous), never per (question, topic)
pair -- see the module-level threshold constants' docstrings for why.

Scoring is slide-level, not topic-level: every question is scored
against every individual slide, and a topic's score for a question is
its single best member slide's score. A question is assigned to the
topic holding its best slide. See HIGH_MADS below for what the earlier
topic-level design got wrong.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Tuple

import numpy as np

from .llm_fallback import AmbiguityResolver
from .schema import MappingResult, QuestionMatch, TopicMapping, UnmatchedQuestion

# Adaptive by default, not fixed absolute numbers -- a raw cosine
# score's usable range depends entirely on the embedding model. The
# thresholds are robust-outlier cutoffs on this run's own distribution
# of per-question BEST scores: median - k * (1.4826 * MAD). Most real
# questions are about the deck, so the bulk of best scores describes
# "what a real match looks like here"; a question whose best slide
# scores far below that bulk has no real home in the deck.
#
# This replaces two things the earlier design did, both measured as
# wrong on the real 140 questions x 23 topics (BAAI/bge-small-en-v1.5):
#   1. Thresholds were percentiles of the whole question x topic score
#      matrix (high = 90th). That makes the top 10% of ALL pairs
#      "match" by construction, however relevant they are: 322 pairs
#      cleared it, one question matched 10 topics, and a 6-slide
#      paravirtualization topic collected 32 questions about migration,
#      EPT and Kubernetes.
#   2. Each topic was one embedding of its first 1500 chars. "What is a
#      Pod in Kubernetes?" matched 4 topics, none of them the one whose
#      slides define a Pod; scored per slide, its best 3 slides are all
#      in that topic.
# Measured with the values below: best-score median 0.796, spread
# 0.065, high = 0.603. Exactly 6 questions fall below high -- the 5
# off-topic ones (CSS, quicksort, ...) plus "What is a hypercall?",
# which is why that band goes to the LLM fallback rather than straight
# to unmatched. Pass explicit high_threshold/low_threshold to
# QuestionMapper to override with fixed values instead (used by this
# module's own deterministic tests).
HIGH_MADS = 3.0
LOW_MADS = 6.0
_MAD_TO_SIGMA = 1.4826

# Two topics "tie" for a question when their best slides score the same
# to within float noise -- in practice, when a deck repeats a slide
# verbatim (this deck repeats a whole 61-slide lecture). Both copies
# are equally the question's home, so both topics get it. This is a
# float-equality tolerance, not a relevance margin: widening it to
# 0.05 put the average question back in 2.3 topics.
TIE_TOLERANCE = 1e-4

# Default budget for topic_text() below. No longer used for scoring
# (that is per slide now); kept for callers that want a compact
# per-topic excerpt.
MAX_TOPIC_CHARS = 1500

# How many top-scoring topics to show the LLM fallback for an ambiguous
# question -- bounds its context, not just performance theater: a
# smaller, curated shortlist is also a better-posed question than "pick
# from all 18." Each candidate is shown as its best-matching slide.
LLM_CANDIDATE_COUNT = 3


def topic_text(topic, deck, max_chars: Optional[int] = MAX_TOPIC_CHARS) -> str:
    """Assembles a topic's representative text from Stage 1's slide
    data via slide_ids -- Stage 2 deliberately excludes slide text from
    its own output, so this is the one place that resolves slide_ids
    back into real content for this stage's purposes. Public: also used
    by export.py to embed representative content directly into the
    final consolidated JSON handed to Stage 4 (called there with
    max_chars=None -- see export.py's docstring for why a bounded
    budget is wrong for that caller specifically).

    max_chars=None means unlimited: every assigned slide's real content
    is included, full stop. A bounded budget still has two real
    per-slide protections: (1) an individual over-budget slide is
    skipped, not treated as a hard stop (the earlier `break`-instead-
    of-`continue` bug: one long early slide, e.g. an acknowledgements
    paragraph, would silently drop every remaining slide no matter how
    short); (2) that per-slide skip does NOT mean "the topic's total
    content always fits" -- measured in practice against the real deck:
    a 6000-char default budget silently dropped 10 of 22 slides from a
    real topic's real content (mostly its substantive middle, not just
    trailing boilerplate), because the topic's true total (12,494
    chars) simply exceeded the budget. A fixed budget can protect
    against one pathological slide; it cannot make an inherently large
    topic's real content fit without dropping some of it -- only a
    caller that genuinely doesn't need every slide's content (Stage 3's
    own bulk-embedding-plus-LLM-prompt use, which needs compactness
    across many topics at once) should pass a bound at all.
    """
    parts: List[str] = []
    total = 0
    for sid in topic.slide_ids:
        slide = deck.get(sid)
        if slide is None or not slide.raw_text.strip():
            continue
        text = slide.raw_text.strip()
        if max_chars is not None and parts and total + len(text) > max_chars:
            continue
        parts.append(text)
        total += len(text)
    return "\n\n".join(parts)


def _l2_normalize(embeddings: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return embeddings / norms


def _cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = _l2_normalize(a)
    b = _l2_normalize(b)
    return a @ b.T


def _robust_threshold(best_scores: np.ndarray, n_mads: float) -> float:
    center = float(np.median(best_scores))
    spread = _MAD_TO_SIGMA * float(np.median(np.abs(best_scores - center)))
    return center - n_mads * spread


class QuestionMapper:
    def __init__(
        self,
        model,
        resolver: Optional[AmbiguityResolver] = None,
        high_threshold: Optional[float] = None,
        low_threshold: Optional[float] = None,
        high_mads: float = HIGH_MADS,
        low_mads: float = LOW_MADS,
        llm_candidate_count: int = LLM_CANDIDATE_COUNT,
    ):
        """Leave high_threshold/low_threshold as None (the default) to
        compute them adaptively from this run's own best-score
        distribution via high_mads/low_mads -- the recommended mode, and
        what run_mapping.py uses. Pass explicit fixed values to override
        (used by this module's own deterministic tests, where exact
        threshold values matter for constructing precise test cases)."""
        self.model = model
        self.resolver = resolver
        self._fixed_high_threshold = high_threshold
        self._fixed_low_threshold = low_threshold
        self.high_mads = high_mads
        self.low_mads = low_mads
        self.llm_candidate_count = llm_candidate_count

    def map_questions(self, questions: List[str], topics: List, deck) -> Tuple[MappingResult, dict]:
        """Returns (result, diagnostics). `diagnostics` carries the raw
        question x topic score matrix and LLM-call bookkeeping."""
        # One row per non-empty slide that belongs to a topic; image-only
        # slides have nothing to score against.
        slide_ids: List[int] = []
        slide_topic_index: List[int] = []
        slide_texts: List[str] = []
        for ti, topic in enumerate(topics):
            for sid in topic.slide_ids:
                slide = deck.get(sid)
                if slide is None or not slide.raw_text.strip():
                    continue
                slide_ids.append(sid)
                slide_topic_index.append(ti)
                slide_texts.append(slide.raw_text.strip())

        scores = np.full((len(questions), len(topics)), -np.inf)  # (n_questions, n_topics)
        best_slide = np.full((len(questions), len(topics)), -1, dtype=int)  # row into slide_ids
        if slide_texts and questions:
            self.model.fit(slide_texts + list(questions))  # no-op for most models; needed for TF-IDF
            slide_embeddings = np.asarray(self.model.embed(slide_texts))
            question_embeddings = np.asarray(self.model.embed_query(list(questions)))
            slide_scores = _cosine_matrix(question_embeddings, slide_embeddings)  # (n_questions, n_slides)

            topic_of_row = np.asarray(slide_topic_index)
            for ti in range(len(topics)):
                rows = np.flatnonzero(topic_of_row == ti)
                if rows.size == 0:
                    continue  # topic of image-only slides: stays -inf, can never match
                scores[:, ti] = slide_scores[:, rows].max(axis=1)
                best_slide[:, ti] = rows[slide_scores[:, rows].argmax(axis=1)]

        has_any_topic = bool(np.isfinite(scores).any())
        best_scores = scores.max(axis=1) if has_any_topic else np.zeros(len(questions))

        high_threshold = (
            self._fixed_high_threshold
            if self._fixed_high_threshold is not None
            else _robust_threshold(best_scores, self.high_mads)
        )
        low_threshold = (
            self._fixed_low_threshold
            if self._fixed_low_threshold is not None
            else _robust_threshold(best_scores, self.low_mads)
        )

        topic_mappings = {
            t.topic_id: TopicMapping(
                t.topic_id, t.start_slide_id, t.end_slide_id, list(t.slide_ids), t.boundary_confidence
            )
            for t in topics
        }
        unmatched: List[UnmatchedQuestion] = []
        n_llm_calls = 0
        n_llm_resolved = 0

        def assign(qi: int, anchor_ti: int, method: str) -> None:
            """Assigns the question to anchor_ti and to every topic tied
            with it (see TIE_TOLERANCE)."""
            row = scores[qi]
            for ti in range(len(topics)):
                if abs(row[ti] - row[anchor_ti]) <= TIE_TOLERANCE:
                    topic_mappings[topics[ti].topic_id].matched_questions.append(
                        QuestionMatch(
                            question=questions[qi],
                            score=float(row[ti]),
                            method=method,
                            best_slide_id=slide_ids[best_slide[qi, ti]],
                        )
                    )

        for qi, question in enumerate(questions):
            row = scores[qi]
            best_ti = int(np.argmax(row))
            best_score = float(row[best_ti])

            if not has_any_topic or not np.isfinite(best_score):
                unmatched.append(UnmatchedQuestion(question=question, best_score=0.0, best_topic_id=None))
                continue

            if best_score >= high_threshold:
                assign(qi, best_ti, "semantic")
                continue

            if best_score < low_threshold:
                unmatched.append(
                    UnmatchedQuestion(question=question, best_score=best_score, best_topic_id=topics[best_ti].topic_id)
                )
                continue

            # Ambiguous zone: [low_threshold, high_threshold).
            if self.resolver is None:
                # No resolver configured -- don't guess at insufficient
                # evidence (same precedent as this project's other
                # "don't silently force an answer" edge cases). Recorded
                # as unmatched with its best candidate for visibility,
                # not silently assigned.
                unmatched.append(
                    UnmatchedQuestion(question=question, best_score=best_score, best_topic_id=topics[best_ti].topic_id)
                )
                continue

            candidate_order = [ti for ti in np.argsort(-row)[: self.llm_candidate_count] if np.isfinite(row[ti])]
            candidates = [
                {"topic_id": topics[ti].topic_id, "text": slide_texts[best_slide[qi, ti]]} for ti in candidate_order
            ]

            n_llm_calls += 1
            chosen_topic_id = self.resolver.resolve(question, candidates)

            if chosen_topic_id is None:
                unmatched.append(
                    UnmatchedQuestion(question=question, best_score=best_score, best_topic_id=topics[best_ti].topic_id)
                )
            else:
                n_llm_resolved += 1
                chosen_ti = next(ti for ti in candidate_order if topics[ti].topic_id == chosen_topic_id)
                assign(qi, chosen_ti, "llm_fallback")

        result = MappingResult(
            generated_at=datetime.now(timezone.utc).isoformat(),
            total_questions=len(questions),
            total_topics=len(topics),
            high_threshold=high_threshold,
            low_threshold=low_threshold,
            topics=[topic_mappings[t.topic_id] for t in topics],
            unmatched_questions=unmatched,
        )
        diagnostics = {
            "scores": scores,
            "n_llm_calls": n_llm_calls,
            "n_llm_resolved": n_llm_resolved,
        }
        return result, diagnostics
