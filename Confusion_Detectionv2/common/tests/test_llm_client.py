"""llm_client.py tests -- fully mocked (a fake Groq client), no network
call and no real API key needed. This is the shared validation logic
03_question_mapping and 04_gap_reporting_agent both build their Groq
client through; each stage's own tests cover their usage of it, this
covers the logic itself once."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from common.llm_client import DEFAULT_MODEL, get_validated_groq_client, resolve_model_name, validate_model


class _FakeModels:
    def __init__(self, ids):
        self._ids = ids

    def list(self):
        return SimpleNamespace(data=[SimpleNamespace(id=i) for i in self._ids])


class _FakeGroqClient:
    def __init__(self, ids):
        self.models = _FakeModels(ids)


def test_resolve_model_name_prefers_explicit_argument(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL_NAME", "env-model")
    assert resolve_model_name("explicit-model") == "explicit-model"


def test_resolve_model_name_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL_NAME", "env-model")
    assert resolve_model_name(None) == "env-model"


def test_resolve_model_name_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("GROQ_MODEL_NAME", raising=False)
    assert resolve_model_name(None) == DEFAULT_MODEL


def test_validate_model_passes_silently_when_model_is_available():
    client = _FakeGroqClient(ids=["openai/gpt-oss-20b"])
    validate_model(client, "openai/gpt-oss-20b")  # must not raise


def test_validate_model_raises_when_model_is_missing():
    client = _FakeGroqClient(ids=["llama-3.1-8b-instant"])
    with pytest.raises(RuntimeError, match="not in Groq's current model lineup"):
        validate_model(client, "llama-3.3-70b-versatile")


def test_validate_model_does_not_raise_when_the_list_call_itself_fails():
    class _ExplodingModels:
        def list(self):
            raise RuntimeError("401 unauthorized")

    class _ExplodingClient:
        models = _ExplodingModels()

    validate_model(_ExplodingClient(), "any-model")  # must not raise -- callers handle real failures themselves


def test_get_validated_groq_client_uses_injected_client_and_resolved_model(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL_NAME", "some-model")
    fake = _FakeGroqClient(ids=["some-model"])
    client, model = get_validated_groq_client(api_key="fake-key", client=fake)
    assert client is fake
    assert model == "some-model"


def test_get_validated_groq_client_raises_when_no_api_key_and_no_injected_client(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY not set"):
        get_validated_groq_client()
