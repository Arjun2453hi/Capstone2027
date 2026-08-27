"""Tests for FlanT5Generator: degenerate-output detection, graceful
handling of garbled input, and a by-eye spot-check on real bundles
(claude.md Section 5 — "this half isn't fully automatable")."""
from __future__ import annotations

import pytest

from ..generator import FlanT5Generator
from .fixtures.ground_truth_verdicts import load_ground_truth_bundles

pytestmark = pytest.mark.slow  # loads google/flan-t5-small


@pytest.fixture(scope="module")
def generator():
    return FlanT5Generator()


def test_is_degenerate_catches_empty_and_short_and_refusals():
    assert FlanT5Generator._is_degenerate("", "prompt text")
    assert FlanT5Generator._is_degenerate("   ", "prompt text")
    assert FlanT5Generator._is_degenerate("ok", "prompt text")  # too short to be a usable draft
    assert FlanT5Generator._is_degenerate("I don't know", "prompt text")
    assert FlanT5Generator._is_degenerate("N/A", "prompt text")
    assert not FlanT5Generator._is_degenerate(
        "Add a bullet explaining that technical debt accrues interest over time.",
        "some unrelated prompt text",
    )


def test_is_degenerate_catches_prompt_echo():
    prompt = "Students asked something about testing. Related slide content: XYZ."
    echoed = "students asked something about testing"
    assert FlanT5Generator._is_degenerate(echoed, prompt)


def test_generate_does_not_crash_on_garbled_context(generator):
    garbled = "DDIeeSppFaaCrrRttmm Eeexnnettc??uootffi??vCCeSSEE??" * 5
    result = generator.generate("What is technical debt?", garbled, "complete_omission", ["q1"])
    print(f"garbled -> guidance={result.guidance!r} suggested={result.suggested_addition!r}")
    assert result.guidance  # always non-empty per the interface contract
    assert result.suggested_addition is None or isinstance(result.suggested_addition, str)


def test_generate_does_not_crash_on_empty_context(generator):
    result = generator.generate("What is technical debt?", "", "complete_omission", ["q1"])
    assert result.guidance


def test_spot_check_on_real_bundles(generator):
    # Not a pass/fail assertion beyond "doesn't crash and returns
    # something" -- this is the manual-review half claude.md Section 5
    # explicitly says isn't fully automatable. Print for a human to read.
    for entry in load_ground_truth_bundles():
        if entry["expected_gap_type"] == "covered":
            continue  # generator is only ever called for non-covered gap types
        bundle = entry["bundle"]
        result = generator.generate(
            bundle["representative_query"],
            bundle["window_text"],
            entry["expected_gap_type"],
            bundle["source_questions"],
        )
        print(f"\ntopic {entry['topic_id']} ({entry['expected_gap_type']}):")
        print(f"  guidance: {result.guidance!r}")
        print(f"  suggested_addition: {result.suggested_addition!r}")
        assert result.guidance
