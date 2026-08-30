"""GapContentGenerator abstraction + a small FLAN-T5 implementation.

A separate model from the classifier, on purpose (see claude.md Section
4): the earlier version of this project saw a single small generative
model produce unreliable "mushy" output when asked to also hold a
structured classification together. Splitting lets the classifier's
real label-probability become `confidence` directly, and lets this
generator focus on one narrower job — conditioned on an already-decided
gap_type, never asked to also decide it.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections import Counter
from typing import List

from .schema import GeneratedContent

MAX_CONTEXT_CHARS = 1500  # keep the prompt well inside a small model's real window
MIN_USABLE_LENGTH = 8  # shorter than this isn't a usable draft, just noise
# A small generator's classic failure mode isn't just "too short" or a
# refusal -- it can also loop a short phrase past the point of
# usefulness (e.g. "X [Slide N] X [Slide N] X [Slide N] ..."). Found by
# reading a real generated gap_report.md end to end (Gap_Reporting
# claude.md's own instruction) and seeing exactly this in a live
# guidance field -- not a hypothetical case.
MAX_TRIGRAM_REPEATS = 3

_REFUSAL_RE = re.compile(
    r"^(i don't know|i do not know|n/a|none|unknown|unclear|cannot|unable to|no information)\b",
    re.IGNORECASE,
)

_GAP_TYPE_DESCRIPTIONS = {
    "complete_omission": "the slides do not address this topic at all",
    "shallow_coverage": "the slides mention this topic but do not explain it in depth",
    "fragmented_context": "the slides seem to reference this topic, but a full explanation may be split across slides not shown here",
}


class GapContentGenerator(ABC):
    @abstractmethod
    def generate(
        self, query: str, context_text: str, gap_type: str, source_questions: List[str]
    ) -> GeneratedContent:
        """`guidance` is always returned. `suggested_addition` is `None`
        when the generator isn't confident enough to draft real content,
        or when its raw output is degenerate (empty, a refusal, or an
        echo of the prompt) — never forced non-null just to fill the
        field."""
        raise NotImplementedError


class FlanT5Generator(GapContentGenerator):
    """google/flan-t5-small: instruction-tuned, ~80M params — small
    enough to run on CPU for hundreds of calls in a reasonable time,
    per this project's "free and lightweight" constraint.
    """

    def __init__(self, model_name: str = "google/flan-t5-small"):
        from transformers import pipeline

        self._model_name = model_name
        self._pipe = pipeline("text2text-generation", model=model_name)

    @property
    def name(self) -> str:
        return f"flan-t5-{self._model_name}"

    def generate(
        self, query: str, context_text: str, gap_type: str, source_questions: List[str]
    ) -> GeneratedContent:
        content = (context_text or "").strip()[:MAX_CONTEXT_CHARS] or "(no slide content available)"
        gap_description = _GAP_TYPE_DESCRIPTIONS.get(gap_type, "the slides may not fully address this topic")

        guidance_prompt = (
            f"Students asked: \"{query}\"\n"
            f"Related slide content:\n{content}\n\n"
            f"Assessment: {gap_description}. "
            f"In one or two sentences, explain to the instructor what is missing or unclear."
        )
        guidance = self._run(guidance_prompt)
        if self._is_degenerate(guidance, guidance_prompt):
            guidance = f"Review needed: {gap_description}."  # deterministic fallback, never blank

        suggestion_prompt = (
            f"Students asked: \"{query}\"\n"
            f"Related slide content:\n{content}\n\n"
            f"Write one short slide bullet point that would answer this question. "
            f"Only output the bullet point text."
        )
        raw_suggestion = self._run(suggestion_prompt)
        suggested_addition = None if self._is_degenerate(raw_suggestion, suggestion_prompt) else raw_suggestion

        return GeneratedContent(guidance=guidance, suggested_addition=suggested_addition)

    def _run(self, prompt: str) -> str:
        result = self._pipe(prompt, max_new_tokens=100, do_sample=False)
        return result[0]["generated_text"].strip()

    @staticmethod
    def _is_degenerate(text: str, prompt: str) -> bool:
        if not text:
            return True
        normalized = text.strip().lower()
        if len(normalized) < MIN_USABLE_LENGTH:
            return True
        if _REFUSAL_RE.match(normalized):
            return True
        if normalized in prompt.lower():
            # The model just echoed a chunk of the prompt back instead
            # of producing new content -- a known small-model failure
            # mode, not a usable draft.
            return True
        if FlanT5Generator._has_excessive_repetition(normalized):
            return True
        return False

    @staticmethod
    def _has_excessive_repetition(normalized_text: str) -> bool:
        """Catches a small model looping the same short phrase past the
        point of usefulness (e.g. "X [Slide N] X [Slide N] ..."), which
        `len() < MIN_USABLE_LENGTH` and the refusal/echo checks above
        don't catch -- the output can be long and not an echo of the
        prompt, just repetitive garbage."""
        words = normalized_text.split()
        if len(words) < 6:
            return False
        trigrams = [" ".join(words[i : i + 3]) for i in range(len(words) - 2)]
        most_common_count = Counter(trigrams).most_common(1)[0][1]
        return most_common_count > MAX_TRIGRAM_REPEATS
