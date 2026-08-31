"""GroqAmbiguityResolver startup model-validation tests -- fully mocked
(a fake Groq client), no network call and no real API key needed, so
these run in the default fast suite. The real-API behavior (actual
resolve() calls) is covered separately in test_llm_fallback.py, marked
slow."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from common.llm_client import DEFAULT_MODEL

from ..src.llm_fallback import GroqAmbiguityResolver


class _FakeModels:
    def __init__(self, ids):
        self._ids = ids

    def list(self):
        return SimpleNamespace(data=[SimpleNamespace(id=i) for i in self._ids])


class _FakeGroqClient:
    def __init__(self, ids):
        self.models = _FakeModels(ids)


def test_construction_succeeds_when_configured_model_is_in_groqs_lineup():
    client = _FakeGroqClient(ids=["openai/gpt-oss-20b", "llama-3.1-8b-instant"])
    resolver = GroqAmbiguityResolver(api_key="fake-key", model_name="openai/gpt-oss-20b", client=client)
    assert resolver.model == "openai/gpt-oss-20b"


def test_construction_fails_loudly_when_configured_model_is_missing_from_groqs_lineup():
    # Reproduces the actual bug that happened: a model Groq has since
    # removed (or renamed) from its hosted lineup, caught here instead
    # of surfacing as a cryptic 404 deep inside the first real
    # ambiguous-question fallback call.
    client = _FakeGroqClient(ids=["llama-3.1-8b-instant", "some-other-model"])
    with pytest.raises(RuntimeError, match="not in Groq's current model lineup"):
        GroqAmbiguityResolver(api_key="fake-key", model_name="llama-3.3-70b-versatile", client=client)


def test_model_name_defaults_from_env_when_not_passed_explicitly(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL_NAME", "some-configured-model")
    client = _FakeGroqClient(ids=["some-configured-model"])
    resolver = GroqAmbiguityResolver(api_key="fake-key", client=client)
    assert resolver.model == "some-configured-model"


def test_model_name_falls_back_to_default_when_unset(monkeypatch):
    monkeypatch.delenv("GROQ_MODEL_NAME", raising=False)
    client = _FakeGroqClient(ids=[DEFAULT_MODEL])
    resolver = GroqAmbiguityResolver(api_key="fake-key", client=client)
    assert resolver.model == DEFAULT_MODEL


def test_a_failed_model_list_call_does_not_block_construction():
    # Established, tested contract this check must not break: a bad key
    # (or any model-list failure) degrades gracefully -- resolve() calls
    # already handle that -- rather than becoming a second, stricter way
    # to raise on exactly the case test_llm_fallback.py's
    # test_real_groq_resolver_degrades_gracefully_on_bad_key covers.
    class _ExplodingModels:
        def list(self):
            raise RuntimeError("401 unauthorized")

    class _ExplodingClient:
        models = _ExplodingModels()

    resolver = GroqAmbiguityResolver(api_key="not-a-real-key", client=_ExplodingClient())
    assert resolver.model  # constructed successfully despite the failed validation call
