"""llm_fallback.py — AmbiguityResolver ABC + GroqAmbiguityResolver.

Only called for questions whose semantic similarity to every topic is
genuinely ambiguous (see mapper.py) -- most questions resolve from
embeddings alone, so this is a rare path, not the common case, kept
behind a swappable interface like every other model choice in this
project.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import List, Optional, TypedDict


class Candidate(TypedDict):
    topic_id: int
    text: str


class AmbiguityResolver(ABC):
    @abstractmethod
    def resolve(self, question: str, candidates: List[Candidate]) -> Optional[int]:
        """Returns the chosen topic_id, or None if the question
        doesn't genuinely relate to any candidate. Must degrade
        gracefully (return None) on a malformed/unparseable model
        response rather than raise -- a flaky LLM call must not crash
        the whole mapping run."""
        raise NotImplementedError


class GroqAmbiguityResolver(AmbiguityResolver):
    """Calls the Groq API for the rare ambiguous case. Requires
    GROQ_API_KEY in the environment (see .env.example) -- raises at
    construction time if missing, so a misconfigured run fails fast
    and loudly rather than silently degrading every ambiguous question
    to "unmatched" partway through a long run.

    Client construction + startup model validation live in
    common/llm_client.py, shared with 04_gap_reporting_agent -- not
    duplicated here (see that module's docstring for why: this
    project's Groq model name has already drifted once,
    llama-3.3-70b-versatile silently disappearing from Groq's hosted
    lineup, and that check belongs in exactly one place)."""

    MAX_CANDIDATE_CHARS = 500  # per-candidate context budget in the prompt

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None, client=None):
        from common.llm_client import get_validated_groq_client

        self._client, self.model = get_validated_groq_client(api_key=api_key, model_name=model_name, client=client)

    @property
    def name(self) -> str:
        return f"groq-{self.model}"

    def resolve(self, question: str, candidates: List[Candidate]) -> Optional[int]:
        valid_ids = {c["topic_id"] for c in candidates}
        prompt = self._build_prompt(question, candidates)

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You match a student's question to the single most relevant "
                            "lecture topic from a short candidate list, or determine that "
                            "none of them genuinely apply. Respond with strict JSON only: "
                            '{"topic_id": <int or null>}.'
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            raw = response.choices[0].message.content
        except Exception:
            # A transient API failure degrades to "unresolved", never a crash.
            return None

        return self._parse(raw, valid_ids)

    def _build_prompt(self, question: str, candidates: List[Candidate]) -> str:
        lines = [f"Question: {question}", "", "Candidate topics:"]
        for c in candidates:
            excerpt = c["text"][: self.MAX_CANDIDATE_CHARS]
            lines.append(f"[topic_id={c['topic_id']}] {excerpt}")
        lines.append("")
        lines.append(
            "Which topic_id does this question best relate to? If none "
            'genuinely relate, respond with {"topic_id": null}.'
        )
        return "\n".join(lines)

    @staticmethod
    def _parse(raw: Optional[str], valid_ids) -> Optional[int]:
        if not raw:
            return None
        try:
            data = json.loads(raw.strip())
            topic_id = data.get("topic_id")
        except (json.JSONDecodeError, AttributeError, TypeError):
            return None
        return topic_id if topic_id in valid_ids else None
