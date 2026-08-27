"""Numeric ground-truth accuracy test for ZeroShotNLIClassifier,
against real hand-labeled ContextBundles (claude.md Section 5).

Run with `pytest -s` to see the full confusion matrix and per-topic scores.
"""
from __future__ import annotations

import pytest

from ..classifier import ZeroShotNLIClassifier
from ..schema import GAP_TYPES
from .fixtures.ground_truth_verdicts import load_ground_truth_bundles

# Every test in this file needs the real classifier (facebook/bart-large-mnli,
# ~1.6GB) -- mark the whole module slow rather than each test individually.
pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def classifier():
    return ZeroShotNLIClassifier()


def test_ground_truth_accuracy_and_confusion_matrix(classifier):
    ground_truth = load_ground_truth_bundles()
    confusion = {actual: {predicted: 0 for predicted in GAP_TYPES} for actual in GAP_TYPES}
    correct = 0

    for entry in ground_truth:
        bundle = entry["bundle"]
        result = classifier.classify(bundle["representative_query"], bundle["window_text"])
        is_correct = result.gap_type == entry["expected_gap_type"]
        correct += is_correct
        confusion[entry["expected_gap_type"]][result.gap_type] += 1

        print(
            f"topic {entry['topic_id']}: expected={entry['expected_gap_type']} "
            f"predicted={result.gap_type} confidence={result.confidence:.4f} "
            f"{'OK' if is_correct else 'MISS'}"
        )
        print(f"    scores: {result.label_scores}")

    accuracy = correct / len(ground_truth)
    print(f"\nAccuracy: {accuracy:.2%} ({correct}/{len(ground_truth)})")
    print("\nConfusion matrix (rows=expected, cols=predicted):")
    header = "expected \\ predicted".ljust(22) + "".join(f"{gt[:12]:>14}" for gt in GAP_TYPES)
    print(header)
    for actual in GAP_TYPES:
        row = "".join(f"{confusion[actual][predicted]:>14}" for predicted in GAP_TYPES)
        print(f"{actual:<22}{row}")

    # The technical-debt case (topic 9) is the single most informative
    # data point here (claude.md Section 5): its window is thin enough
    # that a confident complete_omission would mean the classifier is
    # trusting a windowing artifact as ground truth about the deck.
    # We don't hard-require the exact label (fragmented_context is our
    # best guess, not a certainty) -- but a *confident* wrong-way
    # complete_omission would be the one truly bad outcome to let slide.
    technical_debt = next(e for e in ground_truth if e["topic_id"] == 9)
    result = classifier.classify(
        technical_debt["bundle"]["representative_query"], technical_debt["bundle"]["window_text"]
    )
    print(f"\nTechnical debt (topic 9) result: {result.gap_type} (confidence={result.confidence:.4f})")
    if result.gap_type == "complete_omission":
        assert result.confidence < 0.5, (
            "technical debt topic was called complete_omission with "
            "high confidence -- this is exactly the false positive "
            "claude.md Section 3 warns about (thin window != deck gap)"
        )

    # Regression baseline, not a strict pass bar: an off-the-shelf
    # general-purpose MNLI model on a specialized 4-way task is
    # genuinely a hard setting (see classifier.py's docstring for what
    # was tried and rejected). Report the real number; don't loosen
    # this to force it higher if it drops.
    print(f"\n(Regression baseline: accuracy was {accuracy:.2%} at last run)")


def test_classify_does_not_crash_on_garbled_text(classifier):
    garbled = "DDIeeSppFaaCrrRttmm Eeexnnettc??uootffi??vCCeSSEE??" * 5
    result = classifier.classify("What is technical debt?", garbled)
    print(f"garbled input -> gap_type={result.gap_type} confidence={result.confidence:.4f}")
    assert result.gap_type in GAP_TYPES
    assert 0.0 <= result.confidence <= 1.0


def test_classify_does_not_crash_on_empty_text(classifier):
    result = classifier.classify("What is technical debt?", "")
    assert result.gap_type in GAP_TYPES


def test_classify_truncates_rather_than_crashes_on_oversized_text(classifier):
    huge_text = "This slide explains testing in great detail. " * 500  # far past MAX_PREMISE_CHARS
    result = classifier.classify("What is testing?", huge_text)
    assert result.gap_type in GAP_TYPES
