"""ContextWindowBuilder — the orchestrator for this folder.

For each Global_Context TopicCluster: retrieve the anchor slide for its
representative_query (via Similiarity_gen's QuestionSlideRetriever),
expand it into a window (via an injected ContextWindowStrategy —
FixedRadiusWindow today; see window_strategy.py's ModuleAwareWindow
docstring and claude.md Section 6 for why it's not ModuleAwareWindow
yet), and assemble a budget-enforced ContextBundle.

Depends on QuestionSlideRetriever and ContextWindowStrategy as
interfaces (constructor-injected), never a concrete strategy directly —
same DI posture as Similiarity_gen and Global_Context.
"""
from __future__ import annotations

from typing import List

from . import _paths  # noqa: F401
from .schema import ContextBundle
from .window_strategy import ContextWindowStrategy

from gap_detection.parsing.schema import DeckDocument
from Similiarity_gen.retriever import QuestionSlideRetriever

TRUNCATION_MARKER = " ...[truncated: exceeds max_context_chars]"

DEFAULT_MAX_CONTEXT_CHARS = 4000


class ContextWindowBuilder:
    def __init__(
        self,
        retriever: QuestionSlideRetriever,
        deck: DeckDocument,
        strategy: ContextWindowStrategy,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    ):
        self.retriever = retriever
        self.deck = deck
        self.strategy = strategy
        self.max_context_chars = max_context_chars

    def build(self, topic) -> ContextBundle:
        """`topic` is a Global_Context TopicCluster (duck-typed here —
        this module doesn't import Global_Context, keeping the
        dependency direction one-way: Global_Context and Similiarity_gen
        never need to know Context_Window exists)."""
        top_matches = self.retriever.retrieve(topic.representative_query, top_k=1)
        if not top_matches:
            # Empty SlideIndex (e.g. every slide in the deck was blank) —
            # a valid state, not a crash. Anchor to slide_id 0 by
            # convention so the bundle is still well-formed; window_text
            # is empty either way since there's nothing to embed anyway.
            anchor_slide_id = 0
            window_slide_ids = [0]
            window_text = ""
        else:
            anchor_slide_id = top_matches[0].slide_id
            window_slide_ids = self.strategy.build_window(anchor_slide_id, self.deck)
            window_text = self._assemble_within_budget(window_slide_ids, anchor_slide_id)

        return ContextBundle(
            topic_id=topic.topic_id,
            representative_query=topic.representative_query,
            anchor_slide_id=anchor_slide_id,
            window_slide_ids=window_slide_ids,
            window_text=window_text,
            source_questions=list(topic.source_questions),
            cluster_size=topic.size,
            is_noise=getattr(topic, "is_noise", False),
        )

    def build_all(self, topics: List) -> List[ContextBundle]:
        return [self.build(t) for t in topics]

    # ------------------------------------------------------------------
    def _segment_for(self, slide_id: int) -> str:
        """One slide's contribution to window_text, or "" if it has
        nothing to say.

        Design decision (claude.md Section 4): a blank slide (no title,
        no bullets) inside the window range keeps its slide_id in
        window_slide_ids — it's still part of the deck's real physical
        neighborhood, which Step 5 needs for accurate slide-range
        reporting — but contributes no text here. An empty "[Slide N]"
        line would waste budget and tell Step 4's LLM nothing.
        """
        slide = self.deck.get(slide_id)
        if slide is None or slide.is_empty:
            return ""
        return f"[Slide {slide.slide_number}] {slide.raw_text}"

    def _assemble_within_budget(self, window_slide_ids: List[int], anchor_slide_id: int) -> str:
        """Assemble window_text, enforcing max_context_chars
        deterministically rather than truncating the joined string blind:

        1. Build one segment per non-blank slide in the window.
        2. Decide which segments to KEEP by priority: closest to the
           anchor first (the anchor itself always wins ties), so a tight
           budget drops distant neighbors before it ever drops the slide
           that was actually retrieved as relevant.
        3. Render the kept segments in slide_id (ascending) order, so the
           text still reads top-to-bottom the way the deck does — kept-
           but-reordered-by-priority would read confusingly out of order.
        4. If even the single highest-priority segment doesn't fit alone,
           hard-truncate just that one segment at the last whitespace
           within budget and append TRUNCATION_MARKER — this is the one
           path that can cut mid-slide, and it's the only one, so it's
           the only place a marker is needed.
        """
        segments_by_id = {sid: self._segment_for(sid) for sid in window_slide_ids}
        non_empty_ids = [sid for sid in window_slide_ids if segments_by_id[sid]]
        if not non_empty_ids:
            return ""

        priority_order = sorted(non_empty_ids, key=lambda sid: abs(sid - anchor_slide_id))

        full_text = "\n\n".join(segments_by_id[sid] for sid in window_slide_ids if segments_by_id[sid])
        if len(full_text) <= self.max_context_chars:
            return full_text

        kept: set = set()
        length = 0
        for sid in priority_order:
            seg = segments_by_id[sid]
            add_len = len(seg) + (2 if kept else 0)  # "\n\n" between kept segments
            if length + add_len > self.max_context_chars:
                continue  # this one doesn't fit; a closer/smaller one later in
                          # priority order still might (e.g. anchor didn't fit
                          # but a short neighbor does) — keep scanning
            kept.add(sid)
            length += add_len

        if kept:
            return "\n\n".join(segments_by_id[sid] for sid in window_slide_ids if sid in kept)

        # Degenerate case: not even the anchor's own segment fits alone.
        best = segments_by_id[priority_order[0]]
        budget_for_text = max(0, self.max_context_chars - len(TRUNCATION_MARKER))
        cut = best.rfind(" ", 0, budget_for_text)
        if cut <= 0:
            cut = budget_for_text
        return best[:cut].rstrip() + TRUNCATION_MARKER
