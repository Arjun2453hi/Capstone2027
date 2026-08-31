"""mock_groq_responses.py — scripted tool-call sequences + small fake
deck/context builders shared across this stage's fast tests. None of
this touches the real Groq API or a real embedding model.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
from langchain_core.messages import AIMessage

from ...src._upstream import Deck, QuestionMatch, Slide, TopicDossier, UnmatchedQuestion
from ...src.tools import InvestigationContext


def ai_tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    """One AIMessage carrying exactly one tool call -- the shape a real
    ChatGroq response takes after .bind_tools() parses it."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def ai_text(content: str) -> AIMessage:
    """A plain-text reply with no tool call -- exercises agent.py's
    "nudge back toward the checklist" branch."""
    return AIMessage(content=content, tool_calls=[])


class ScriptedChatModel:
    """Fakes exactly the two methods agent.py calls on a real ChatGroq
    instance: .bind_tools(tools) and .invoke(messages). Each scripted
    response is returned in order; an Exception in the script is
    raised instead of returned, for exercising retry/backoff paths."""

    def __init__(self, responses: List):
        self._responses = list(responses)
        self.call_count = 0

    def bind_tools(self, tools):
        return self  # ignore real tool binding -- tests fully control the response sequence

    def invoke(self, messages):
        response = self._responses[self.call_count]
        self.call_count += 1
        if isinstance(response, Exception):
            raise response
        return response


class FakeRateLimitError(Exception):
    status_code = 429


class _FakeFixedEmbeddingModel:
    """Returns a pre-supplied embedding matrix keyed by exact text, for
    tests needing precise control over cosine scores. Falls back to a
    zero vector for unrecognized text (e.g. empty-string queries)."""

    name = "fake-fixed"

    def __init__(self, vectors_by_text: dict, dim: int):
        self._vectors_by_text = vectors_by_text
        self._dim = dim

    def fit(self, corpus):
        pass

    def embed(self, texts):
        vectors = []
        for t in texts:
            if t in self._vectors_by_text:
                vectors.append(self._vectors_by_text[t])
            else:
                # search_expanding_context embeds a *concatenation* of
                # several slides' raw_text ("\n\n"-joined) -- fall back
                # to averaging any registered per-slide vectors found
                # inside it, so tests only need to register per-slide
                # vectors, not every possible window concatenation.
                parts = t.split("\n\n")
                part_vectors = [self._vectors_by_text[p] for p in parts if p in self._vectors_by_text]
                vectors.append(list(np.mean(part_vectors, axis=0)) if part_vectors else [0.0] * self._dim)
        return np.array(vectors, dtype=float)

    def embed_query(self, texts):
        return self.embed(texts)


def make_fake_deck(n_slides: int = 10) -> Deck:
    slides = [
        Slide(slide_id=i, slide_number=i + 1, title=f"Slide {i}", title_font_size=18.0, bullets=[f"content {i}"])
        for i in range(n_slides)
    ]
    return Deck(source_pdf="fake.pdf", num_pages=n_slides, slides=slides)


def make_fake_context(
    deck: Optional[Deck] = None,
    dossiers: Optional[List[TopicDossier]] = None,
    unmatched_questions: Optional[List[UnmatchedQuestion]] = None,
    vectors_by_text: Optional[dict] = None,
    dim: int = 3,
    found_threshold: float = 0.5,
    switch_threshold: float = 0.5,
) -> InvestigationContext:
    deck = deck if deck is not None else make_fake_deck()
    dossiers = dossiers if dossiers is not None else []
    unmatched_questions = unmatched_questions if unmatched_questions is not None else []
    vectors_by_text = vectors_by_text if vectors_by_text is not None else {}
    model = _FakeFixedEmbeddingModel(vectors_by_text, dim)

    dossiers_by_id = {d.topic_id: d for d in dossiers}
    deck_embeddings = model.embed([s.raw_text for s in deck.slides])
    norms = np.linalg.norm(deck_embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    deck_embeddings = deck_embeddings / norms

    return InvestigationContext(
        deck=deck,
        dossiers_by_id=dossiers_by_id,
        unmatched_questions=unmatched_questions,
        embedding_model=model,
        found_threshold=found_threshold,
        switch_threshold=switch_threshold,
        deck_embeddings=deck_embeddings,
    )


def make_dossier(topic_id: int, slide_ids: List[int], window_text: str, matched_questions=None) -> TopicDossier:
    return TopicDossier(
        topic_id=topic_id,
        slide_ids=slide_ids,
        slide_range=f"Slides {slide_ids[0] + 1}-{slide_ids[-1] + 1}" if slide_ids else "(no slides)",
        boundary_confidence=0.0,
        window_text=window_text,
        cluster_size=len(matched_questions or []),
        matched_questions=matched_questions or [],
    )
