"""tools.py — the 5 tools each topic-investigation agent can call.

Each is a thin wrapper over logic that already exists elsewhere in this
pipeline: get_topic_slides/get_matched_questions read Stage 3's own
consolidated gap_verification_input.json (via InvestigationContext,
already assembled per-topic content + matches -- nothing to
re-derive); search_expanding_context reuses 02_topic_segmentation's own
block_similarity/depth_score as its switch signal; search_similar_slides
reuses the same injected embedding model + cosine_similarity everywhere
else in this project uses. This module does not reimplement retrieval,
embedding, or boundary-scoring logic (claude.md Section 6).
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ._upstream import block_similarity, cosine_similarity, depth_score

MAX_RADIUS = 8  # claude.md Section 6: "pin MAX_RADIUS, e.g. 8"


def l2_normalize(embeddings: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return embeddings / norms


class InvestigationContext:
    """Everything the 5 tools need, injected once per orchestrator run
    -- not global state, so tests construct a small fake context
    instead of loading the real deck. `found_threshold`/
    `switch_threshold` must already be computed adaptively by the
    caller (see orchestrator.py) -- this class just holds and uses
    them, it doesn't compute them itself."""

    def __init__(
        self,
        deck,
        dossiers_by_id: dict,
        unmatched_questions,
        embedding_model,
        found_threshold: float,
        switch_threshold: float,
        deck_embeddings: Optional[np.ndarray] = None,
    ):
        self.deck = deck
        self.dossiers_by_id = dossiers_by_id  # topic_id -> TopicDossier
        self.unmatched_questions = unmatched_questions
        self.embedding_model = embedding_model
        self.found_threshold = found_threshold
        self.switch_threshold = switch_threshold
        # Precomputed once (whole-deck embed is cheap relative to doing
        # it per search_expanding_context call) -- lazily built from
        # embedding_model if the caller didn't already supply it.
        self.deck_embeddings = (
            deck_embeddings
            if deck_embeddings is not None
            else l2_normalize(np.asarray(embedding_model.embed([s.raw_text for s in deck.slides])))
        )
        self._embedding_cache: dict = {}

    def embed(self, text: str) -> np.ndarray:
        if not text.strip():
            return np.zeros(self.deck_embeddings.shape[1] if self.deck_embeddings.size else 1)
        if text not in self._embedding_cache:
            vec = np.asarray(self.embedding_model.embed([text])[0], dtype=float)
            self._embedding_cache[text] = vec
        return self._embedding_cache[text]

    def boundary_switch_signal(self, anchor_row: int, radius: int) -> Optional[float]:
        """depth_k(i) at the boundary nearest the anchor slide, scale
        pinned to the current search radius -- claude.md Section 6:
        "reuse 02_topic_segmentation's own boundary-scoring function as
        the switch signal, do not reimplement." NaN (similarity.py's
        own edge-handling / insufficient-window signal) surfaces as
        None -- "no reliable switch signal here" is a real, distinct
        answer, not zero."""
        sim = block_similarity(self.deck_embeddings, radius)
        depth = depth_score(sim, radius)
        if len(depth) == 0:
            return None
        boundary_i = min(max(anchor_row, 0), len(depth) - 1)
        value = depth[boundary_i]
        return None if np.isnan(value) else float(value)


class GapReportArgs(BaseModel):
    topic_id: int
    slide_ids_examined: List[int] = Field(default_factory=list)
    gap_type: str
    confidence: float
    report_text: str


def build_tools(ctx: InvestigationContext) -> list:
    @tool
    def get_topic_slides(topic_id: int) -> dict:
        """Returns {"slide_ids": [...], "text": "..."} -- the topic's
        own slide range's stitched text (Stage 1's parsed content within
        Stage 2's topic boundaries, already assembled by Stage 3) along
        with the exact slide_ids that text came from. Pass -1 for the
        synthetic unmatched-questions "topic" (which has no assigned
        slide range and returns an empty list/string). Always the
        obvious first call in any real-topic investigation. Record
        slide_ids from here (plus any window_slide_ids from
        search_expanding_context you actually used) in write_report's
        slide_ids_examined -- don't guess at the topic's range, report
        exactly what these tools gave you."""
        dossier = ctx.dossiers_by_id.get(topic_id)
        if dossier is None or not dossier.slide_ids:
            return {"slide_ids": [], "text": ""}
        return {"slide_ids": list(dossier.slide_ids), "text": dossier.window_text}

    @tool
    def get_matched_questions(topic_id: int) -> List[dict]:
        """Returns the real student questions matched to this topic by
        Stage 3's semantic similarity + LLM fallback, each with its
        score. Pass -1 for the synthetic unmatched-questions "topic" to
        get every question that didn't confidently match any real
        topic."""
        if topic_id == -1:
            return [{"question": u.question, "score": u.best_score} for u in ctx.unmatched_questions]
        dossier = ctx.dossiers_by_id.get(topic_id)
        if dossier is None:
            return []
        return [{"question": m.question, "score": m.score, "method": m.method} for m in dossier.matched_questions]

    @tool
    def search_expanding_context(anchor_slide_id: int, what_am_i_looking_for: str) -> dict:
        """Grows an examination window outward from an anchor slide,
        checking at each radius whether the described content has been
        found, or whether expansion has crossed into a genuinely
        different topic. Use this when a topic's own slides seem to
        only partially address something -- construct
        what_am_i_looking_for from what you've specifically noticed is
        missing or thin (e.g. "explanation of how dropout works during
        training vs inference"), not a generic restatement of the
        topic's title."""
        target_vec = ctx.embed(what_am_i_looking_for)
        deck_slides = ctx.deck.slides
        n = len(deck_slides)
        anchor_row = next((i for i, s in enumerate(deck_slides) if s.slide_id == anchor_slide_id), None)
        if anchor_row is None:
            return {"status": "gave_up_at_max_radius", "window_slide_ids": []}

        radius = 1
        window_slide_ids: List[int] = []
        while radius <= MAX_RADIUS:
            lo, hi = max(0, anchor_row - radius), min(n - 1, anchor_row + radius)
            window = deck_slides[lo : hi + 1]
            window_slide_ids = [s.slide_id for s in window]
            window_text = "\n\n".join(s.raw_text for s in window if s.raw_text.strip())
            window_vec = ctx.embed(window_text)
            relevance = cosine_similarity(window_vec, target_vec)
            switch_signal = ctx.boundary_switch_signal(anchor_row, radius)

            if relevance > ctx.found_threshold:
                return {"status": "found", "window_slide_ids": window_slide_ids, "relevance": float(relevance)}
            if switch_signal is not None and switch_signal > ctx.switch_threshold:
                return {
                    "status": "hit_topic_switch",
                    "window_slide_ids": window_slide_ids,
                    "relevance": float(relevance),
                }
            radius += 1

        return {"status": "gave_up_at_max_radius", "window_slide_ids": window_slide_ids}

    @tool
    def search_similar_slides(query_text: str, top_k: int = 5) -> List[dict]:
        """Full-deck semantic search over every slide's own content,
        independent of topic boundaries. Use this when a question seems
        entirely unaddressed by this topic and its neighbors, to check
        the rest of the deck before concluding it's a true omission
        rather than a segmentation error. Freely searchable across the
        whole deck, including slides that belong to other topics'
        ranges -- that's expected, not a bug."""
        query_vec = ctx.embed(query_text)
        scored = []
        for s in ctx.deck.slides:
            if not s.raw_text.strip():
                continue
            score = cosine_similarity(ctx.embed(s.raw_text), query_vec)
            scored.append((score, s))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [{"slide_id": s.slide_id, "title": s.title, "score": float(score)} for score, s in scored[:top_k]]

    @tool(args_schema=GapReportArgs)
    def write_report(
        topic_id: int, slide_ids_examined: List[int], gap_type: str, confidence: float, report_text: str
    ) -> dict:
        """Terminal tool -- ends this topic's investigation. Call this
        only after you've actually investigated per the system prompt's
        checklist. gap_type must be one of: complete_omission,
        shallow_coverage, fragmented_context, covered."""
        return {
            "topic_id": topic_id,
            "slide_ids_examined": slide_ids_examined,
            "gap_type": gap_type,
            "confidence": confidence,
            "report_text": report_text,
        }

    return [get_topic_slides, get_matched_questions, search_expanding_context, search_similar_slides, write_report]
