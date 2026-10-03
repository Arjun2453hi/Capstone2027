"""Mocked provider integration: failover must not change the request."""
import copy
import importlib
import json
from types import SimpleNamespace

import httpx
import pytest
from groq import AuthenticationError, BadRequestError, RateLimitError, APIConnectionError, InternalServerError
from langchain_core.messages import HumanMessage
from langchain_groq import ChatGroq

from common.groq_rotation import Credential, GroqRequestTooLarge, RotatingCompletions, failure_kind, load_credentials, rate_limit_details
from common.llm_monitoring import LLMMonitor, invocation_context, summarize


def error(cls=RateLimitError, message="rate limit", status=429, headers=None):
    response = httpx.Response(status, headers=headers or {}, request=httpx.Request(
        "POST", "https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": "Bearer fake-secret-one"}))
    return cls(message, response=response, body={"error": {"message": message}})


def completion():
    return {"id": "test", "object": "chat.completion", "created": 0,
            "model": "openai/gpt-oss-20b",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}


class FakeClient:
    def __init__(self, script, calls, identifier):
        self.script, self.calls, self.identifier = list(script), calls, identifier
        self.chat = SimpleNamespace(completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=self.create)))

    def create(self, **request):
        self.calls.append((self.identifier, copy.deepcopy(request)))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(headers={"x-ratelimit-remaining-tokens": "7000"}, parse=lambda: item)


def adapter(tmp_path, scripts):
    calls = []
    keys = [Credential(f"KEY_{i + 1}", f"fake-secret-{i + 1}") for i in range(len(scripts))]
    clients = {k.identifier: FakeClient(scripts[i], calls, k.identifier) for i, k in enumerate(keys)}
    monitor = LLMMonitor(tmp_path / "events.jsonl", secrets=[k.value for k in keys])
    result = RotatingCompletions(keys, monitor=monitor, client_factory=lambda k: clients[k.identifier])
    return result, calls, monitor


REQUEST = {"model": "openai/gpt-oss-20b", "messages": [{"role": "user", "content": "same question"}],
           "tools": [{"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}],
           "temperature": 0.0, "max_tokens": 4096, "tool_choice": "auto"}


def test_rotation_preserves_payload_and_accounts_success_once(tmp_path):
    model, calls, monitor = adapter(tmp_path, [[error()], [completion()]])
    request = copy.deepcopy(REQUEST)
    with invocation_context(6, 1, 3, "logical-request", 2):
        assert model.create(**request) == completion()
    assert calls[0][1] == calls[1][1] == REQUEST
    assert request == REQUEST
    rows = [json.loads(line) for line in monitor.path.read_text().splitlines()]
    rotations = [row for row in rows if row["event"] == "key_rotation"]
    assert len(rotations) == 1
    assert rotations[0]["previous_key_id"] == "KEY_1"
    assert rotations[0]["new_key_id"] == "KEY_2"
    assert rotations[0]["topic_id"] == 6
    assert rotations[0]["application_retry_attempt"] == 2
    assert all(row.get("request_id") == "logical-request" for row in rows[1:])
    summary = summarize(monitor.path)
    assert summary["keys"]["KEY_2"]["actual_total_tokens"] == 120
    assert summary["topics"]["6"]["requests_succeeded_after_rotation"] == 1
    # A successful key remains active on the next independent request.
    assert model._current == 1


def test_all_keys_exhausted_is_bounded_and_preserves_rate_limit_type(tmp_path):
    model, calls, monitor = adapter(tmp_path, [[error()], [error()], [error()]])
    with pytest.raises(RateLimitError):
        model.create(**REQUEST)
    assert [k for k, _ in calls] == ["KEY_1", "KEY_2", "KEY_3"]
    assert len(calls) == 3


@pytest.mark.parametrize("exc", [
    error(AuthenticationError, "bad key", 401),
    error(BadRequestError, "invalid argument", 400),
    error(BadRequestError, "tool_use_failed: Failed to parse tool call", 400),
    error(BadRequestError, "context_length_exceeded", 400),
    error(message="Request too large for this model TPM limit", status=429),
    error(InternalServerError, "server unavailable", 500),
    APIConnectionError(message="connection failed", request=httpx.Request("POST", "https://api.groq.com")),
])
def test_only_recoverable_rate_limits_rotate(tmp_path, exc):
    model, calls, monitor = adapter(tmp_path, [[exc], [completion()]])
    expected_type = GroqRequestTooLarge if failure_kind(exc) == "oversized_request" else type(exc)
    with pytest.raises(expected_type):
        model.create(**REQUEST)
    assert len(calls) == 1
    assert "key_rotation" not in monitor.path.read_text()


def test_oversized_estimate_never_reaches_provider(tmp_path):
    model, calls, monitor = adapter(tmp_path, [[completion()], [completion()]])
    request = {**REQUEST, "messages": [{"role": "user", "content": "x" * 400000}]}
    with pytest.raises(GroqRequestTooLarge, match="approximate"):
        model.create(**request)
    assert calls == []
    assert "oversized_request" in monitor.path.read_text()


def test_exception_and_telemetry_redact_credentials_and_auth_headers(tmp_path):
    model, calls, monitor = adapter(tmp_path, [[error(AuthenticationError, "bad fake-secret-1 gsk_TESTSECRET", 401)]])
    with pytest.raises(AuthenticationError) as captured:
        model.create(**REQUEST)
    serialized = str(captured.value) + str(captured.value.body) + str(captured.value.response.headers) + monitor.path.read_text()
    assert "fake-secret-1" not in serialized
    assert "gsk_TESTSECRET" not in serialized
    assert "Authorization" not in str(captured.value.request.headers)
    assert "[REDACTED]" in serialized


def test_loading_numeric_keys_empty_slots_duplicates_and_environment_precedence(tmp_path, monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("GROQ_API_KEY_"):
            monkeypatch.delenv(name)
    path = tmp_path / ".env.groq-rotation"
    path.write_text("GROQ_API_KEY_1=file-one\nGROQ_API_KEY_2=\nGROQ_API_KEY_3=file-three\nGROQ_API_KEY_5=file-five\nGROQ_API_KEY_10=file-one\n")
    monkeypatch.setenv("GROQ_API_KEY_3", "process-three")
    keys = load_credentials(path, fallback_key="fallback")
    assert [(k.identifier, k.value) for k in keys] == [("KEY_1", "file-one"), ("KEY_3", "process-three"), ("KEY_5", "file-five")]
    assert "file-one" not in repr(keys)


def test_placeholder_file_uses_existing_primary_key(tmp_path, monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("GROQ_API_KEY_"):
            monkeypatch.delenv(name)
    path = tmp_path / ".env.groq-rotation"
    path.write_text("GROQ_API_KEY_1=\nGROQ_API_KEY_2=\n")
    assert load_credentials(path, "primary")[0].identifier == "PRIMARY"


def test_cooldown_skips_recently_limited_keys(tmp_path):
    model, calls, monitor = adapter(tmp_path, [[error(headers={"retry-after": "120"})],
                                               [completion(), error()], [completion()]])
    model.create(**REQUEST)
    model.create(**REQUEST)
    assert [key for key, _ in calls] == ["KEY_1", "KEY_2", "KEY_2", "KEY_3"]


def test_real_chatgroq_binding_preserves_response_metadata(tmp_path):
    model, calls, monitor = adapter(tmp_path, [[error()], [completion()]])
    chat = ChatGroq(model="openai/gpt-oss-20b", api_key="fake-key", client=model)
    response = chat.bind_tools(REQUEST["tools"]).invoke([HumanMessage(content="hello")])
    assert response.content == "ok"
    assert response.usage_metadata["total_tokens"] == 120
    assert response.response_metadata["token_usage"]["prompt_tokens"] == 100
    assert calls[0][1] == calls[1][1]


def test_existing_application_retry_loop_still_owns_exhaustion(tmp_path, monkeypatch):
    agent = importlib.import_module("04_gap_reporting_agent.src.agent")
    model, calls, monitor = adapter(tmp_path, [[error() for _ in range(5)], [error() for _ in range(5)]])
    chat = ChatGroq(model="openai/gpt-oss-20b", api_key="fake-key", client=model)
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: None)
    with pytest.raises(RateLimitError):
        agent._invoke_with_retry(chat, [HumanMessage(content="same")], "topic 4", topic_id=4, invocation_number=2)
    rows = [json.loads(line) for line in monitor.path.read_text().splitlines()]
    outcomes = [row for row in rows if row["event"] == "invocation_outcome"]
    assert len(outcomes) == agent.MAX_RATE_LIMIT_RETRIES == 5
    assert len({row["request_id"] for row in outcomes}) == 1
    assert [row["application_retry_attempt"] for row in outcomes] == [1, 2, 3, 4, 5]
    assert len(calls) == 10  # bounded failover is internal, not a topic restart


def test_oversized_provider_429_bypasses_existing_backoff(tmp_path, monkeypatch):
    agent = importlib.import_module("04_gap_reporting_agent.src.agent")
    model, calls, monitor = adapter(tmp_path, [[error(message="Request too large")], [completion()]])
    chat = ChatGroq(model="openai/gpt-oss-20b", api_key="fake-key", client=model)
    sleeps = []
    monkeypatch.setattr(agent.time, "sleep", sleeps.append)
    with pytest.raises(GroqRequestTooLarge):
        agent._invoke_with_retry(chat, [HumanMessage(content="same")], "topic 4", topic_id=4, invocation_number=2)
    assert len(calls) == 1
    assert sleeps == []


def test_rate_limit_details_extract_provider_scope_and_quota():
    exc = error(message="organization `org_test` tokens per minute: Limit 8000, Used 2500, Requested 6000")
    exc.body["error"].update(type="tokens", code="rate_limit_exceeded")
    details = rate_limit_details(exc)
    assert details["provider_organization"] == "org_test"
    assert details["quota_limit"] == 8000
    assert details["quota_used"] == 2500
    assert details["quota_requested"] == 6000
    assert details["rate_limit_type"] == "tokens"
