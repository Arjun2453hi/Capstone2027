"""mapper.py — QuestionMapper orchestrator.

Depends on EmbeddingModel and AmbiguityResolver as interfaces only,
never a concrete embedding library or LLM client directly. Semantic
similarity resolves the large majority of questions cheaply; the LLM
fallback is scoped to at most one call per question (only when even the
single best-scoring topic is ambiguous), never per (question, topic)
pair -- see the module-level threshold constants' docstrings for why.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Tuple

import numpy as np

from .llm_fallback import AmbiguityResolver
from .schema import MappingResult, QuestionMatch, TopicMapping, UnmatchedQuestion

# Adaptive by default, not fixed absolute numbers -- same lesson
# 02_topic_segmentation already learned: a raw cosine score's usable
# range depends entirely on the embedding model. Measured on the real
# 140 questions x 18 topics with BAAI/bge-small-en-v1.5, EVERY pair
# scored >= 0.35 and the median was 0.53 -- a fixed high=0.50 sat at
# the median (meaningless as a "confident" cutoff, ~71% of all pairs
# cleared it) and a fixed low=0.30 was below every single pair (nothing
# could ever be "confidently unrelated"). Percentiles of this run's own
# score matrix instead: HIGH_PERCENTILE marks the top slice as directly
# confident, LOW_PERCENTILE marks the bottom slice as confidently
# unrelated, and everything between is the genuinely ambiguous zone the
# LLM fallback exists for. Pass explicit high_threshold/low_threshold to
# QuestionMapper to override with fixed values instead (used by this
# module's own deterministic tests).
HIGH_PERCENTILE = 90.0
LOW_PERCENTILE = 25.0

# Budget for a topic's representative text, used both for embedding it
# and (truncated further per-candidate) for LLM fallback context.
MAX_TOPIC_CHARS = 1500

# How many top-scoring topics to show the LLM fallback for an ambiguous
# question -- bounds its context, not just performance theater: a
# smaller, curated shortlist is also a better-posed question than "pick
# from all 18."
LLM_CANDIDATE_COUNT = 3


def topic_text(topic, deck, max_chars: int = MAX_TOPIC_CHARS) -> str:
    """Assembles a topic's representative text from Stage 1's slide
    data via slide_ids -- Stage 2 deliberately excludes slide text from
    its own output, so this is the one place that resolves slide_ids
    back into real content for this stage's purposes. Public: also used
    by export.py to embed representative content directly into the
    final consolidated JSON handed to Stage 4."""
    # Real bug found in practice: this used to `break` here instead of
    # `continue` -- one early slide big enough to blow the budget (e.g.
    # a long acknowledgements paragraph on a topic's first slide) would
    # silently drop every remaining slide in the topic, no matter how
    # short, producing a "representative" text that was really just
    # boilerplate front matter. Skipping only the individual
    # over-budget slide and continuing to try the rest is the whole
    # fix -- the budget itself still bounds total length.
    parts: List[str] = []
    total = 0
    for sid in topic.slide_ids:
        slide = deck.get(sid)
        if slide is None or not slide.raw_text.strip():
            continue
        text = slide.raw_text.strip()
        if parts and total + len(text) > max_chars:
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


class QuestionMapper:
    def __init__(
        self,
        model,
        resolver: Optional[AmbiguityResolver] = None,
        high_threshold: Optional[float] = None,
        low_threshold: Optional[float] = None,
        high_percentile: float = HIGH_PERCENTILE,
        low_percentile: float = LOW_PERCENTILE,
        max_topic_chars: int = MAX_TOPIC_CHARS,
        llm_candidate_count: int = LLM_CANDIDATE_COUNT,
    ):
        """Leave high_threshold/low_threshold as None (the default) to
        compute them adaptively from this run's own score distribution
        via high_percentile/low_percentile -- the recommended mode, and
        what run_mapping.py uses. Pass explicit fixed values to override
        (used by this module's own deterministic tests, where exact
        threshold values matter for constructing precise test cases)."""
        self.model = model
        self.resolver = resolver
        self._fixed_high_threshold = high_threshold
        self._fixed_low_threshold = low_threshold
        self.high_percentile = high_percentile
        self.low_percentile = low_percentile
        self.max_topic_chars = max_topic_chars
        self.llm_candidate_count = llm_candidate_count

    def map_questions(self, questions: List[str], topics: List, deck) -> Tuple[MappingResult, dict]:
        """Returns (result, diagnostics). `diagnostics` carries the raw
        score matrix and LLM-call bookkeeping."""
        topic_texts = [topic_text(t, deck, self.max_topic_chars) for t in topics]

        self.model.fit(topic_texts + list(questions))  # no-op for most models; needed for TF-IDF
        topic_embeddings = np.asarray(self.model.embed(topic_texts))
        question_embeddings = np.asarray(self.model.embed_query(list(questions)))

        scores = _cosine_matrix(question_embeddings, topic_embeddings)  # (n_questions, n_topics)

        high_threshold = (
            self._fixed_high_threshold
            if self._fixed_high_threshold is not None
            else float(np.percentile(scores, self.high_percentile))
        )
        low_threshold = (
            self._fixed_low_threshold
            if self._fixed_low_threshold is not None
            else float(np.percentile(scores, self.low_percentile))
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

        for qi, question in enumerate(questions):
            row = scores[qi]
            high_matches = [ti for ti in range(len(topics)) if row[ti] >= high_threshold]

            if high_matches:
                for ti in high_matches:
                    topic_mappings[topics[ti].topic_id].matched_questions.append(
                        QuestionMatch(question=question, score=float(row[ti]), method="semantic")
                    )
                continue

            best_ti = int(np.argmax(row))
            best_score = float(row[best_ti])

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

            candidate_order = np.argsort(-row)[: self.llm_candidate_count]
            candidates = [{"topic_id": topics[ti].topic_id, "text": topic_texts[ti]} for ti in candidate_order]

            n_llm_calls += 1
            chosen_topic_id = self.resolver.resolve(question, candidates)

            if chosen_topic_id is None:
                unmatched.append(
                    UnmatchedQuestion(question=question, best_score=best_score, best_topic_id=topics[best_ti].topic_id)
                )
            else:
                n_llm_resolved += 1
                chosen_ti = next(ti for ti in candidate_order if topics[ti].topic_id == chosen_topic_id)
                topic_mappings[chosen_topic_id].matched_questions.append(
                    QuestionMatch(question=question, score=float(row[chosen_ti]), method="llm_fallback")
                )

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
