"""GapVerifier — the orchestrator for this folder.

Depends on GapClassifier and GapContentGenerator as interfaces only,
never a concrete implementation directly — same DI posture as every
prior module.

## Section 6 decision: singleton handling, default = "classify-only"

409 of 445 topics are singletons (is_noise=True, backed by exactly 1
question). Three options were on the table:

- "full": verify all 445 identically (simplest, highest cost — ~4x more
  generator calls than needed, drafting content for topics Step 5 will
  weight lowest anyway).
- "skip": drop singletons entirely (cheapest, but violates
  Global_Context's own "never drop noise" rule — a singleton is still a
  real, individually meaningful signal, just a weak one).
- "classify-only" (chosen): every topic gets classified (cheap — one
  small zero-shot forward pass), but the generator's content-drafting
  step only runs for real clusters. Drafting a suggested_addition for a
  single unconfirmed question is the lowest-value use of a generation
  call in this pipeline, per claude.md Section 6's own framing — but
  the classification itself is worth keeping for every topic, since
  Step 5 still needs a gap_type and confidence per topic to weight
  correctly, and skipping it would be the "skip" option's traceability
  problem by another name.

Override with --singleton-mode on run_verification.py.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .classifier import GapClassifier
from .generator import GapContentGenerator
from .schema import GapVerdict

SINGLETON_MODES = ("full", "classify-only", "skip")
DEFAULT_SINGLETON_MODE = "classify-only"

# --- Confidence adjustment for known data-quality caveats (claude.md Section 3) ---
# These operate on the ORCHESTRATOR's view of bundle metadata (window
# length, cluster size, garbling), not inside the classifier itself —
# the classifier's own confidence must stay a genuine, unmodified model
# score (claude.md Section 4); combining it with what we already know
# about the bundle's reliability is the orchestrator's job.

# A window this thin, backing this many questions, is a known signature
# of FixedRadiusWindow missing real content just outside its radius
# (the "technical debt" case documented in claude.md) — not necessarily
# a genuine complete_omission. Dampen confidence rather than trust the
# classifier's face-value score in that specific scenario.
THIN_WINDOW_CHAR_THRESHOLD = 200
HIGH_BACKING_THRESHOLD = 5
THIN_WINDOW_DAMPENING = 0.7

# Signature of the still-unfixed character-duplication parsing glitch:
# real runs of the same character repeated 3+ times in a row
# ("DDDeee...", "pppaaa...") are essentially never legitimate English,
# unlike a single doubled letter. Require several occurrences (not
# just one) so a coincidental "aaargh"-style interjection in real
# content doesn't get flagged.
_GARBLE_RUN_RE = re.compile(r"(.)\1{2,}")
GARBLE_MIN_MATCHES = 3
GARBLE_DAMPENING = 0.7


def _looks_garbled(text: str) -> bool:
    if not text:
        return False
    return len(_GARBLE_RUN_RE.findall(text)) >= GARBLE_MIN_MATCHES


def _is_thin_relative_to_backing(window_text: str, cluster_size: int) -> bool:
    return len(window_text) < THIN_WINDOW_CHAR_THRESHOLD and cluster_size >= HIGH_BACKING_THRESHOLD


class GapVerifier:
    def __init__(
        self,
        classifier: GapClassifier,
        generator: GapContentGenerator,
        singleton_mode: str = DEFAULT_SINGLETON_MODE,
    ):
        if singleton_mode not in SINGLETON_MODES:
            raise ValueError(f"singleton_mode must be one of {SINGLETON_MODES}, got {singleton_mode!r}")
        self.classifier = classifier
        self.generator = generator
        self.singleton_mode = singleton_mode

    def verify(self, bundle) -> Optional[GapVerdict]:
        """`bundle` is a Context_Window ContextBundle, duck-typed here
        (topic_id, representative_query, window_slide_ids, window_text,
        source_questions, cluster_size, is_noise) — this module doesn't
        import Context_Window, keeping the dependency direction
        one-way, same as Context_Window not importing Global_Context."""
        if bundle.is_noise and self.singleton_mode == "skip":
            return None

        classification = self.classifier.classify(bundle.representative_query, bundle.window_text)
        confidence = self._adjust_confidence(classification.confidence, bundle)

        skip_generation = bundle.is_noise and self.singleton_mode == "classify-only"

        if classification.gap_type == "covered":
            guidance = "This topic appears adequately covered by the retrieved slide content."
            suggested_addition = None
        elif skip_generation:
            guidance = (
                f"Classified as '{classification.gap_type}' (backed by 1 question). "
                f"Content drafting skipped under classify-only singleton mode."
            )
            suggested_addition = None
        else:
            generated = self.generator.generate(
                bundle.representative_query,
                bundle.window_text,
                classification.gap_type,
                list(bundle.source_questions),
            )
            guidance = generated.guidance
            suggested_addition = generated.suggested_addition

        return GapVerdict(
            topic_id=bundle.topic_id,
            gap_type=classification.gap_type,
            slide_ids=list(bundle.window_slide_ids),
            guidance=guidance,
            suggested_addition=suggested_addition,
            confidence=confidence,
            backed_by_questions=bundle.cluster_size,
            is_noise=bundle.is_noise,
        )

    def verify_all(self, bundles) -> List[GapVerdict]:
        verdicts = []
        for bundle in bundles:
            verdict = self.verify(bundle)
            if verdict is not None:
                verdicts.append(verdict)
        return verdicts

    def _adjust_confidence(self, raw_confidence: float, bundle) -> float:
        factor = 1.0
        if _is_thin_relative_to_backing(bundle.window_text, bundle.cluster_size):
            factor *= THIN_WINDOW_DAMPENING
        if _looks_garbled(bundle.window_text):
            factor *= GARBLE_DAMPENING
        return round(raw_confidence * factor, 4)
