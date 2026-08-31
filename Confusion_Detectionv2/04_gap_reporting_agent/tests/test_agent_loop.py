"""agent.py tests: the routing loop, the max_iterations cap +
forced-conclusion behavior, and rate-limit retry/backoff -- all
deterministic, via a scripted fake chat model. Mirrors claude.md
Section 11's testing requirement (mock a 429, assert backoff/retry and
the clearly-marked failed report) and Section 12's cap test."""
from __future__ import annotations

import pytest

from ..src import agent as agent_module
from ..src.agent import MAX_ITERATIONS, run_topic_investigation
from .fixtures.mock_groq_responses import FakeRateLimitError, ScriptedChatModel, ai_text, ai_tool_call, make_fake_context


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    """agent.py deliberately paces real calls with time.sleep (both the
    per-step pacing delay and rate-limit backoff) -- none of that should
    slow down this fast, deterministic test file."""
    monkeypatch.setattr(agent_module.time, "sleep", lambda seconds: None)


def test_normal_completion_stops_as_soon_as_write_report_is_called():
    ctx = make_fake_context()
    model = ScriptedChatModel(
        [
            ai_tool_call("get_topic_slides", {"topic_id": 0}, "c1"),
            ai_tool_call(
                "write_report",
                {"topic_id": 0, "slide_ids_examined": [0, 1], "gap_type": "covered", "confidence": 0.9, "report_text": "all good"},
                "c2",
            ),
        ]
    )

    result = run_topic_investigation(model, ctx, topic_id=0, kickoff_message="investigate topic 0")

    assert result["outcome"] == "completed"
    assert result["report"].gap_type == "covered"
    assert result["report"].confidence == 0.9
    assert result["n_tool_calls"] == 2
    assert model.call_count == 2  # stopped immediately -- no extra invoke after write_report


def test_plain_text_reply_is_nudged_back_instead_of_ending_the_investigation():
    ctx = make_fake_context()
    model = ScriptedChatModel(
        [
            ai_text("Let me think about this."),
            ai_tool_call(
                "write_report",
                {"topic_id": 0, "slide_ids_examined": [], "gap_type": "covered", "confidence": 0.5, "report_text": "ok"},
                "c1",
            ),
        ]
    )

    result = run_topic_investigation(model, ctx, topic_id=0, kickoff_message="investigate")

    assert result["outcome"] == "completed"  # recovered after the nudge, did not crash or stop early


def test_hitting_max_iterations_forces_a_capped_confidence_conclusion():
    ctx = make_fake_context()
    # MAX_ITERATIONS non-terminal tool calls, then one more (forced-
    # conclusion) response that DOES call write_report with a
    # deliberately high confidence -- must be clamped to <= 0.4.
    non_terminal = [ai_tool_call("get_topic_slides", {"topic_id": 0}, f"c{i}") for i in range(MAX_ITERATIONS)]
    forced = ai_tool_call(
        "write_report",
        {"topic_id": 0, "slide_ids_examined": [], "gap_type": "shallow_coverage", "confidence": 0.95, "report_text": "cut short"},
        "cf",
    )
    model = ScriptedChatModel(non_terminal + [forced])

    result = run_topic_investigation(model, ctx, topic_id=0, kickoff_message="investigate")

    assert result["outcome"] == "cap_hit"
    assert result["report"].confidence <= 0.4  # capped regardless of what the model sent
    assert model.call_count == MAX_ITERATIONS + 1


def test_invalid_write_report_arguments_are_rejected_and_retried_not_crashed():
    # GapReport(**args) is built directly from whatever the model sent
    # -- an invalid gap_type must not raise uncaught and crash the
    # investigation; it should be rejected with a corrective ToolMessage
    # and retried, same shape as the malformed-JSON case.
    ctx = make_fake_context()
    model = ScriptedChatModel(
        [
            ai_tool_call(
                "write_report",
                {"topic_id": 0, "slide_ids_examined": [], "gap_type": "not_a_real_gap_type", "confidence": 0.5, "report_text": "x"},
                "c1",
            ),
            ai_tool_call(
                "write_report",
                {"topic_id": 0, "slide_ids_examined": [0], "gap_type": "covered", "confidence": 0.5, "report_text": "fixed"},
                "c2",
            ),
        ]
    )

    result = run_topic_investigation(model, ctx, topic_id=0, kickoff_message="investigate")

    assert result["outcome"] == "completed"
    assert result["report"].gap_type == "covered"
    assert result["report"].report_text == "fixed"


def test_hitting_max_iterations_without_any_write_report_call_produces_a_flagged_report():
    ctx = make_fake_context()
    non_terminal = [ai_tool_call("get_topic_slides", {"topic_id": 0}, f"c{i}") for i in range(MAX_ITERATIONS)]
    still_no_report = ai_tool_call("get_matched_questions", {"topic_id": 0}, "cf")  # ignores the forced-conclusion nudge
    model = ScriptedChatModel(non_terminal + [still_no_report])

    result = run_topic_investigation(model, ctx, topic_id=0, kickoff_message="investigate")

    assert result["outcome"] == "cap_hit"
    assert result["report"].confidence == 0.0
    assert "cut short" in result["report"].report_text


class _FakeRateLimitErrorWithHeaders(Exception):
    """Reproduces Groq's real 429 shape: an exception carrying a
    .response with real rate-limit headers -- measured in practice
    (see agent.py's _retry_after_seconds docstring): a naive fixed
    guess was nowhere near the account's real retry-after value."""

    status_code = 429

    def __init__(self, retry_after_header: str):
        super().__init__("429 rate limited")
        self.response = type("FakeResponse", (), {"headers": {"retry-after": retry_after_header}})()


def test_retry_after_seconds_prefers_the_real_header_over_the_guessed_schedule():
    exc = _FakeRateLimitErrorWithHeaders("128")
    assert agent_module._retry_after_seconds(exc, attempt=1) == 128.0


def test_retry_after_seconds_falls_back_to_exponential_guess_without_a_header():
    exc = FakeRateLimitError("429, no headers here")
    assert agent_module._retry_after_seconds(exc, attempt=3) == 8.0  # 2**3


def test_rate_limit_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(agent_module.time, "sleep", lambda seconds: None)  # no real delay in tests
    ctx = make_fake_context()
    model = ScriptedChatModel(
        [
            FakeRateLimitError("429 rate limited"),
            ai_tool_call(
                "write_report",
                {"topic_id": 0, "slide_ids_examined": [], "gap_type": "covered", "confidence": 0.8, "report_text": "ok"},
                "c1",
            ),
        ]
    )

    result = run_topic_investigation(model, ctx, topic_id=0, kickoff_message="investigate")

    assert result["outcome"] == "completed"
    assert model.call_count == 2  # one failed attempt, one successful retry


class _FakeToolCallParseError(Exception):
    status_code = 400

    def __str__(self):
        return "Error code: 400 - tool_use_failed: Failed to parse tool call arguments as JSON"


def test_malformed_tool_call_json_is_nudged_and_retried_within_the_loop():
    # Reproduces a real failure hit against the actual Groq API: a
    # write_report call whose report_text was long enough to get cut
    # off mid-generation, making the JSON invalid. Groq surfaces this
    # as an HTTP 400 "tool_use_failed" -- distinct from a rate limit,
    # and retrying the *same* messages would just reproduce it, so this
    # must append a corrective nudge and continue, not blindly retry.
    ctx = make_fake_context()
    model = ScriptedChatModel(
        [
            _FakeToolCallParseError(),
            ai_tool_call(
                "write_report",
                {"topic_id": 0, "slide_ids_examined": [], "gap_type": "covered", "confidence": 0.8, "report_text": "concise now"},
                "c1",
            ),
        ]
    )

    result = run_topic_investigation(model, ctx, topic_id=0, kickoff_message="investigate")

    assert result["outcome"] == "completed"
    assert result["report"].report_text == "concise now"
    assert model.call_count == 2  # one malformed attempt, one successful retry -- no backoff sleep needed


def test_malformed_tool_call_json_during_forced_conclusion_falls_back_to_flagged_report():
    ctx = make_fake_context()
    non_terminal = [ai_tool_call("get_topic_slides", {"topic_id": 0}, f"c{i}") for i in range(MAX_ITERATIONS)]
    model = ScriptedChatModel(non_terminal + [_FakeToolCallParseError()])

    result = run_topic_investigation(model, ctx, topic_id=0, kickoff_message="investigate")

    assert result["outcome"] == "cap_hit"
    assert result["report"].confidence == 0.0
    assert "did not conclude" in result["report"].report_text or "cut short" in result["report"].report_text


def test_rate_limit_exhausted_produces_a_clearly_marked_failed_report(monkeypatch):
    monkeypatch.setattr(agent_module.time, "sleep", lambda seconds: None)
    ctx = make_fake_context()
    model = ScriptedChatModel([FakeRateLimitError("429") for _ in range(agent_module.MAX_RATE_LIMIT_RETRIES)])

    result = run_topic_investigation(model, ctx, topic_id=3, kickoff_message="investigate")

    assert result["outcome"] == "rate_limit_failed"
    assert result["report"].confidence == 0.0
    assert result["report"].topic_id == 3
    assert "rate limit" in result["report"].report_text.lower()
    assert model.call_count == agent_module.MAX_RATE_LIMIT_RETRIES  # never crashed the whole run
