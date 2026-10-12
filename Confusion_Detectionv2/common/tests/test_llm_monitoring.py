import json

from common.llm_monitoring import LLMMonitor, REQUEST_CONTEXT, estimate_request, invocation_context, rate_headers, summarize, usage_fields


def test_estimation_includes_tool_schemas_and_arguments_and_output_reserve():
    small = {"model": "openai/gpt-oss-20b", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 4096}
    larger = {**small, "tools": [{"description": "schema " * 1000}],
              "messages": small["messages"] + [{"role": "assistant", "tool_calls": [{"arguments": "argument " * 1000}]}]}
    a, b = estimate_request(small), estimate_request(larger)
    assert b["estimated_input_tokens"] > a["estimated_input_tokens"]
    assert b["estimated_total_tokens"] == b["estimated_input_tokens"] + 4096
    assert "approximate" in b["estimation_method"]


def test_unknown_model_and_missing_output_allowance_remain_unknown(monkeypatch):
    monkeypatch.delenv("GROQ_CONTEXT_WINDOW_TOKENS", raising=False)
    result = estimate_request({"model": "unknown", "messages": []})
    assert result["context_window_tokens"] is None
    assert result["reserved_output_tokens"] is None
    assert result["estimated_total_tokens"] is None


def test_provider_usage_missing_is_not_fabricated():
    result = usage_fields({"choices": []})
    assert result["actual_input_tokens"] is None
    assert result["actual_output_tokens"] is None
    assert result["actual_total_tokens"] is None


def test_context_does_not_leak_between_topics():
    with invocation_context(0, 1, 1):
        assert REQUEST_CONTEXT.get()["topic_id"] == 0
    assert REQUEST_CONTEXT.get() == {}
    with invocation_context(1, 1, 1):
        assert REQUEST_CONTEXT.get()["llm_invocation_number"] == 1
        assert REQUEST_CONTEXT.get()["topic_id"] == 1
    assert REQUEST_CONTEXT.get() == {}


def test_headers_are_allowlisted():
    assert rate_headers({"Authorization": "secret", "Set-Cookie": "secret", "Retry-After": "30"}) == {"retry-after": "30"}


def test_unknown_usage_and_failure_do_not_inflate_observed_tokens(tmp_path):
    monitor = LLMMonitor(tmp_path / "events.jsonl", secrets=["private-value"])
    with invocation_context(1, 1, 3):
        monitor.emit("provider_success", key_id="KEY_1", actual_input_tokens=100, actual_output_tokens=20, actual_total_tokens=120)
        monitor.emit("provider_success", key_id="KEY_1", **usage_fields({}))
        monitor.emit("provider_failure", key_id="KEY_1", failure_kind="rate_limit", exception_message="private-value")
    summary = summarize(monitor.path)
    assert summary["keys"]["KEY_1"]["actual_total_tokens"] == 120
    assert summary["keys"]["KEY_1"]["responses_without_usage"] == 1
    rows = [json.loads(line) for line in monitor.path.read_text().splitlines()]
    assert rows[-1]["observed_key_usage"]["total_tokens"] == 120
    assert "private-value" not in monitor.path.read_text()


def test_monitoring_io_failure_does_not_break_application(tmp_path, capsys):
    directory = tmp_path / "directory"
    directory.mkdir()
    monitor = LLMMonitor(directory)
    monitor.emit("test")
    monitor.emit("test")
    assert capsys.readouterr().out.count("could not be written") == 1
