"""GapVerifier orchestrator tests.

Singleton-mode logic and confidence adjustment are tested with fake
classifier/generator (deterministic, no model download) — same pattern
as Context_Window's fake-retriever budget tests. A separate slow test
runs the REAL classifier+generator end to end on the small hand-labeled
set to confirm the whole pipeline produces valid GapVerdicts; the full
445-bundle run lives in run_verification.py's report, not here (a
1.6GB NLI model x 445 topics inside the automated test suite would make
`pytest` itself impractically slow to run routinely).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pytest

from ..classifier import GapClassifier
from ..generator import GapContentGenerator
from ..schema import ClassificationResult, GeneratedContent
from ..verifier import GARBLE_DAMPENING, THIN_WINDOW_DAMPENING, GapVerifier
from .fixtures.ground_truth_verdicts import load_ground_truth_bundles


@dataclass
class _FakeBundle:
    topic_id: int
    representative_query: str
    window_slide_ids: List[int]
    window_text: str
    source_questions: List[str]
    cluster_size: int
    is_noise: bool


class _FixedClassifier(GapClassifier):
    """Always returns the same gap_type/confidence -- lets tests isolate
    verifier logic (singleton handling, confidence adjustment) from
    real model noise."""

    def __init__(self, gap_type: str = "shallow_coverage", confidence: float = 0.8):
        self._gap_type = gap_type
        self._confidence = confidence

    def classify(self, query, context_text):
        return ClassificationResult(gap_type=self._gap_type, confidence=self._confidence, label_scores={})


class _CountingGenerator(GapContentGenerator):
    def __init__(self):
        self.call_count = 0

    def generate(self, query, context_text, gap_type, source_questions):
        self.call_count += 1
        return GeneratedContent(guidance="fake guidance", suggested_addition="fake suggestion")


def _bundle(is_noise: bool, cluster_size: int, window_text: str = "some real slide content here") -> _FakeBundle:
    return _FakeBundle(
        topic_id=1,
        representative_query="some query",
        window_slide_ids=[5, 6, 7],
        window_text=window_text,
        source_questions=["q1"] * cluster_size,
        cluster_size=cluster_size,
        is_noise=is_noise,
    )


def test_full_mode_calls_generator_for_singletons_too():
    generator = _CountingGenerator()
    verifier = GapVerifier(_FixedClassifier(), generator, singleton_mode="full")
    verifier.verify(_bundle(is_noise=True, cluster_size=1))
    assert generator.call_count == 1


def test_classify_only_mode_skips_generator_for_singletons():
    generator = _CountingGenerator()
    verifier = GapVerifier(_FixedClassifier(), generator, singleton_mode="classify-only")
    verdict = verifier.verify(_bundle(is_noise=True, cluster_size=1))
    assert generator.call_count == 0
    assert verdict is not None  # still classified and returned, per Section 6's chosen default
    assert verdict.gap_type == "shallow_coverage"
    assert verdict.suggested_addition is None


def test_classify_only_mode_still_calls_generator_for_real_clusters():
    generator = _CountingGenerator()
    verifier = GapVerifier(_FixedClassifier(), generator, singleton_mode="classify-only")
    verifier.verify(_bundle(is_noise=False, cluster_size=40))
    assert generator.call_count == 1


def test_skip_mode_drops_singletons_entirely():
    verifier = GapVerifier(_FixedClassifier(), _CountingGenerator(), singleton_mode="skip")
    verdict = verifier.verify(_bundle(is_noise=True, cluster_size=1))
    assert verdict is None


def test_skip_mode_keeps_real_clusters():
    verifier = GapVerifier(_FixedClassifier(), _CountingGenerator(), singleton_mode="skip")
    verdict = verifier.verify(_bundle(is_noise=False, cluster_size=10))
    assert verdict is not None


def test_covered_never_calls_generator_regardless_of_mode():
    generator = _CountingGenerator()
    verifier = GapVerifier(_FixedClassifier(gap_type="covered"), generator, singleton_mode="full")
    verdict = verifier.verify(_bundle(is_noise=False, cluster_size=20))
    assert generator.call_count == 0
    assert verdict.suggested_addition is None
    assert verdict.guidance  # always non-empty


def test_cluster_size_and_is_noise_carried_through_unchanged():
    verifier = GapVerifier(_FixedClassifier(), _CountingGenerator(), singleton_mode="full")
    verdict = verifier.verify(_bundle(is_noise=True, cluster_size=1))
    assert verdict.backed_by_questions == 1
    assert verdict.is_noise is True

    verdict2 = verifier.verify(_bundle(is_noise=False, cluster_size=51))
    assert verdict2.backed_by_questions == 51
    assert verdict2.is_noise is False


def test_thin_window_high_backing_dampens_confidence():
    verifier = GapVerifier(_FixedClassifier(confidence=0.9), _CountingGenerator(), singleton_mode="full")
    thin_high_backing = _bundle(is_noise=False, cluster_size=51, window_text="Technical Debt")  # < 200 chars
    verdict = verifier.verify(thin_high_backing)
    print(f"thin+high-backing confidence: raw=0.9 adjusted={verdict.confidence}")
    assert verdict.confidence == pytest.approx(0.9 * THIN_WINDOW_DAMPENING, abs=1e-4)


def test_thin_window_low_backing_not_dampened():
    verifier = GapVerifier(_FixedClassifier(confidence=0.9), _CountingGenerator(), singleton_mode="full")
    thin_low_backing = _bundle(is_noise=True, cluster_size=1, window_text="Technical Debt")
    verdict = verifier.verify(thin_low_backing)
    assert verdict.confidence == pytest.approx(0.9, abs=1e-4)


def test_garbled_text_dampens_confidence():
    verifier = GapVerifier(_FixedClassifier(confidence=0.9), _CountingGenerator(), singleton_mode="full")
    garbled = _bundle(
        is_noise=False,
        cluster_size=2,
        window_text="DDDeeepppaaarrrtttmmmeeennnttt ooofff CCCSSSEEE some real words too",
    )
    verdict = verifier.verify(garbled)
    print(f"garbled confidence: raw=0.9 adjusted={verdict.confidence}")
    assert verdict.confidence == pytest.approx(0.9 * GARBLE_DAMPENING, abs=1e-4)


def test_clean_text_not_dampened_by_garble_check():
    verifier = GapVerifier(_FixedClassifier(confidence=0.9), _CountingGenerator(), singleton_mode="full")
    # Long enough (>200 chars) to also avoid the thin-window dampening,
    # so this test isolates the garble check specifically.
    clean_text = "This is perfectly normal slide content about testing. " * 5
    clean = _bundle(is_noise=False, cluster_size=10, window_text=clean_text)
    verdict = verifier.verify(clean)
    assert verdict.confidence == pytest.approx(0.9, abs=1e-4)


def test_invalid_singleton_mode_rejected():
    with pytest.raises(ValueError):
        GapVerifier(_FixedClassifier(), _CountingGenerator(), singleton_mode="bogus")


# ----------------------------------------------------------------------
# End-to-end sanity check with the REAL classifier + generator
# ----------------------------------------------------------------------
@pytest.mark.slow
def test_real_pipeline_produces_valid_verdicts_on_ground_truth_set():
    from ..classifier import ZeroShotNLIClassifier
    from ..generator import FlanT5Generator

    verifier = GapVerifier(ZeroShotNLIClassifier(), FlanT5Generator(), singleton_mode="full")
    for entry in load_ground_truth_bundles():
        b = entry["bundle"]
        bundle = _FakeBundle(
            topic_id=b["topic_id"],
            representative_query=b["representative_query"],
            window_slide_ids=b["window_slide_ids"],
            window_text=b["window_text"],
            source_questions=b["source_questions"],
            cluster_size=b["cluster_size"],
            is_noise=(b["cluster_size"] == 1),
        )
        verdict = verifier.verify(bundle)
        print(
            f"topic {verdict.topic_id}: gap_type={verdict.gap_type} "
            f"confidence={verdict.confidence} guidance={verdict.guidance[:80]!r}"
        )
        assert verdict.slide_ids == bundle.window_slide_ids
        assert verdict.backed_by_questions == bundle.cluster_size
        assert 0.0 <= verdict.confidence <= 1.0
        if verdict.gap_type == "covered":
            assert verdict.suggested_addition is None
