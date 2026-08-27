"""GapClassifier abstraction + a zero-shot NLI implementation.

Same DI posture as every prior module: the concrete model library
import (transformers) lives only in this file, so swapping the
classifier — a bigger NLI model, a fine-tuned one, eventually a real
LLM call — means writing one new class here, not touching verifier.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .schema import ClassificationResult

# NLI models truncate silently past their max sequence length; slicing
# here ourselves (rather than trusting the pipeline's own truncation
# kwarg, which isn't consistent across transformers versions/pipelines)
# guarantees classify() never crashes on a long window_text, per
# claude.md Section 3's "must not crash" requirement.
#
# Set close to Context_Window's own max_context_chars (4000): a lower
# value was tried first (1200) and empirically cut off exactly the
# slide holding the real answer for one ground-truth case (the COCOMO
# formula, on the third slide of a 3-slide window) -- silently
# discarding real content is worse than a slightly longer premise.
MAX_PREMISE_CHARS = 3800


class GapClassifier(ABC):
    @abstractmethod
    def classify(self, query: str, context_text: str) -> ClassificationResult:
        """Must return the model's own label probability as confidence
        — never an invented heuristic layered on top. Must not raise on
        garbled or truncation-worthy input; degrade gracefully instead."""
        raise NotImplementedError


class ZeroShotNLIClassifier(GapClassifier):
    """Zero-shot NLI classifier, decomposed into two independent binary
    entailment checks rather than one 4-way classification.

    ## Why two checks instead of a single 4-way zero-shot call

    The first design tried was a single `zero-shot-classification` call
    across all 4 gap-type hypotheses at once. Empirically (checked
    against 6 hand-labeled real bundles, see tests/test_classifier.py),
    that approach converged on "fragmented_context" for nearly every
    input regardless of content — its hypothesis phrasing ("seems
    related but incomplete") is a hedge that's *partially* true of
    almost any text, so it systematically won the multi-way comparison.
    Accuracy on the hand-labeled set was 1/6.

    Decomposing into two separate, simpler binary judgments —
    "is this text relevant to the question" and "does this text fully
    explain it" — matches what general-purpose MNLI models are actually
    trained to do (single entailment judgments) far better than one
    compound meta-classification. This raised hand-labeled accuracy to
    3/6, including correctly avoiding a confident complete_omission on
    the deliberately hard "technical debt" case (claude.md Section 3's
    single most important requirement). It's still not highly accurate
    — the two misses were both cases where the real content was
    tangential/short and the model's relevance check didn't penalize
    that as strongly as a human would — documented honestly rather than
    hidden behind a rephrased test.

    ## The four gap types, as a decision tree over two real model scores

    - `relevance` < RELEVANCE_THRESHOLD -> complete_omission
      confidence = 1 - relevance (genuinely low relevance = genuinely
      confident it's an omission)
    - `completeness` >= COMPLETENESS_THRESHOLD -> covered
      confidence = completeness
    - otherwise (relevant, not complete) -> shallow_coverage if the
      window has substantial text, fragmented_context if it's thin
      (thin + relevant is the known FixedRadiusWindow-missed-content
      signature from claude.md Section 3 — this is the one place a
      length heuristic makes the classification decision, and it's
      exactly the signal Section 3 asks for, not an arbitrary addition)
      confidence = 1 - completeness in both cases
    """

    RELEVANCE_THRESHOLD = 0.5
    COMPLETENESS_THRESHOLD = 0.35
    # Below this many characters, "relevant but not complete" reads as
    # a windowing artifact (real explanation likely just outside the
    # window) rather than genuinely shallow slide content. Matches
    # verifier.py's THIN_WINDOW_CHAR_THRESHOLD on purpose -- "thin"
    # should mean the same thing everywhere in this folder.
    FRAGMENTED_VS_SHALLOW_CHAR_THRESHOLD = 200

    def __init__(self, model_name: str = "facebook/bart-large-mnli"):
        from transformers import pipeline

        self._model_name = model_name
        self._pipe = pipeline("zero-shot-classification", model=model_name)

    @property
    def name(self) -> str:
        return f"zero-shot-nli-{self._model_name}"

    def _entailment_score(self, premise: str, true_label: str, false_label: str) -> float:
        """Binary entailment as a single softmax-normalized pair, rather
        than an independent (multi_label=True) score per label — pairing
        a claim against its explicit negation gives the model a clear
        contrast to judge, which is closer to what MNLI was trained on
        than scoring one claim in isolation."""
        result = self._pipe(premise, [true_label, false_label], multi_label=False)
        return dict(zip(result["labels"], result["scores"]))[true_label]

    def classify(self, query: str, context_text: str) -> ClassificationResult:
        content = (context_text or "").strip() or "(no slide content available)"
        premise = content[:MAX_PREMISE_CHARS]

        relevance = self._entailment_score(
            premise,
            f"This text discusses the topic: {query}",
            f"This text is unrelated to the topic: {query}",
        )
        completeness = self._entailment_score(
            premise,
            f"This text gives a complete, detailed explanation for: {query}",
            f"This text gives only a brief or incomplete explanation for: {query}",
        )
        label_scores = {"relevance": relevance, "completeness": completeness}

        if relevance < self.RELEVANCE_THRESHOLD:
            gap_type = "complete_omission"
            confidence = 1.0 - relevance
        elif completeness >= self.COMPLETENESS_THRESHOLD:
            gap_type = "covered"
            confidence = completeness
        elif len(premise) < self.FRAGMENTED_VS_SHALLOW_CHAR_THRESHOLD:
            gap_type = "fragmented_context"
            confidence = 1.0 - completeness
        else:
            gap_type = "shallow_coverage"
            confidence = 1.0 - completeness

        return ClassificationResult(gap_type=gap_type, confidence=float(confidence), label_scores=label_scores)
