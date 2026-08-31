"""Real Groq API integration check -- separate from test_mapper.py so
the default/fast test run never makes a network call. Requires
GROQ_API_KEY (loaded from .env at the project root)."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module", autouse=True)
def _load_env():
    from dotenv import load_dotenv

    load_dotenv(_PROJECT_ROOT / ".env")


def test_real_groq_resolver_picks_the_obviously_relevant_candidate():
    from ..src.llm_fallback import GroqAmbiguityResolver

    resolver = GroqAmbiguityResolver()
    candidates = [
        {
            "topic_id": 0,
            "text": "Kubernetes is a container orchestration platform for managing containerized "
            "applications across a cluster of machines, handling scheduling, scaling, and load balancing.",
        },
        {
            "topic_id": 1,
            "text": "DevOps combines software development and IT operations to shorten the "
            "system development life cycle and deliver features more frequently.",
        },
    ]
    result = resolver.resolve("How do I scale pods automatically in a Kubernetes cluster?", candidates)
    print(f"Groq resolved to topic_id={result}")
    assert result == 0


def test_real_groq_resolver_returns_none_when_nothing_genuinely_relates():
    from ..src.llm_fallback import GroqAmbiguityResolver

    resolver = GroqAmbiguityResolver()
    candidates = [
        {"topic_id": 5, "text": "Docker containers share the host kernel instead of virtualizing hardware."},
        {"topic_id": 6, "text": "Live migration moves a running virtual machine with minimal downtime."},
    ]
    result = resolver.resolve("What is the capital of France?", candidates)
    print(f"Groq resolved to topic_id={result}")
    assert result is None


def test_real_groq_resolver_degrades_gracefully_on_bad_key():
    from ..src.llm_fallback import GroqAmbiguityResolver

    resolver = GroqAmbiguityResolver(api_key="not-a-real-key")
    result = resolver.resolve("any question", [{"topic_id": 0, "text": "some topic"}])
    assert result is None  # never raises, even on an auth failure
