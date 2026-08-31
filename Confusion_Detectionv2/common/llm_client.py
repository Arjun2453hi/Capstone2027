"""llm_client.py — shared Groq client construction + startup model
validation.

Any stage that calls Groq (03_question_mapping's ambiguous-question
fallback, 04_gap_reporting_agent's investigation loop) builds its
client through here, once -- so a future model-lineup drift (already
happened once: llama-3.3-70b-versatile was silently removed from
Groq's hosted lineup) is caught in one place, not re-diagnosed and
re-patched per stage. This is `common/`'s job per the project root
CLAUDE.md: shared interfaces/contracts multiple stages depend on, no
stage owning logic another stage needs.
"""
from __future__ import annotations

import os
from typing import Optional

# Groq's hosted lineup changes over time -- llama-3.3-70b-versatile is
# gone entirely as of this build. openai/gpt-oss-20b: a solid
# instruction-following size for classification/investigation tasks,
# without the larger 120b variant's cost/latency on every call. Kept
# here only as the last-resort default -- GROQ_MODEL_NAME in .env is
# the real, expected source, precisely so a future drift is a one-line
# config change, not a code change.
DEFAULT_MODEL = "openai/gpt-oss-20b"


def resolve_model_name(model_name: Optional[str] = None) -> str:
    return model_name or os.getenv("GROQ_MODEL_NAME") or DEFAULT_MODEL


def build_groq_client(api_key: Optional[str] = None):
    """Constructs a real groq.Groq client. Raises RuntimeError if no
    API key is configured -- fails fast at construction time rather
    than deferring to a cryptic error on the first real call."""
    resolved_key = api_key or os.getenv("GROQ_API_KEY")
    if not resolved_key:
        raise RuntimeError(
            "GROQ_API_KEY not set. Copy .env.example to .env and fill in a real key."
        )
    from groq import Groq

    return Groq(api_key=resolved_key)


def validate_model(client, model_name: str) -> None:
    """Fails loudly if `model_name` isn't in Groq's current model
    lineup -- one cheap API call, so a drifted model name surfaces
    immediately and clearly instead of as a 404 the first time a real
    call actually needs it.

    Deliberately narrow: only a model-list call that *succeeds* but
    doesn't contain the configured model raises. A model-list call that
    itself fails (bad key, no network) is left alone -- that's the same
    failure mode a real call would hit, and callers already have their
    own established, tested contract for degrading gracefully on that
    (e.g. 03_question_mapping's GroqAmbiguityResolver.resolve() returns
    None rather than raising) -- this check must not become a second,
    stricter way to fail on exactly that case.
    """
    try:
        available = {m.id for m in client.models.list().data}
    except Exception as e:
        print(f"[warn] Could not verify Groq model availability ({e}); skipping startup model check.")
        return
    if model_name not in available:
        raise RuntimeError(
            f"Configured Groq model '{model_name}' is not in Groq's current model "
            f"lineup ({len(available)} models available). Update GROQ_MODEL_NAME in "
            ".env -- see https://console.groq.com/docs/models for available models."
        )


def get_validated_groq_client(api_key: Optional[str] = None, model_name: Optional[str] = None, client=None):
    """Returns (client, model_name): a constructed (or injected, for
    tests -- pass `client`) Groq client with the configured model
    already validated against its live lineup. The one place every
    Groq-calling stage in this project should get its client from."""
    resolved_model = resolve_model_name(model_name)
    resolved_client = client if client is not None else build_groq_client(api_key)
    validate_model(resolved_client, resolved_model)
    return resolved_client, resolved_model


# Generous headroom for a tool-calling response whose JSON arguments
# include a multi-paragraph report_text -- observed in practice
# (04_gap_reporting_agent): a verbose response with no explicit cap
# occasionally got cut off mid-generation, and Groq rejects the
# resulting truncated JSON as a 400 "tool_use_failed" error rather than
# returning a shorter, well-formed response. Combined with a prompt
# nudge toward conciseness (that stage's SYSTEM_PROMPT), not a
# substitute for it -- a token cap alone can't stop a model from trying
# to write something long, only reduce how badly it gets cut off if it
# does.
DEFAULT_CHAT_MAX_TOKENS = 4096


def get_validated_chat_groq(
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = DEFAULT_CHAT_MAX_TOKENS,
):
    """Returns a langchain_groq.ChatGroq instance for 04_gap_reporting_agent's
    tool-calling investigation loop, with the configured model validated
    against Groq's live lineup first via the same raw-client check
    get_validated_groq_client uses -- so a drifted model name fails
    loudly at startup here too, not as a LangChain-wrapped exception
    three tool calls into a real investigation."""
    resolved_key = api_key or os.getenv("GROQ_API_KEY")
    if not resolved_key:
        raise RuntimeError(
            "GROQ_API_KEY not set. Copy .env.example to .env and fill in a real key."
        )
    resolved_model = resolve_model_name(model_name)
    validate_model(build_groq_client(resolved_key), resolved_model)

    from langchain_groq import ChatGroq

    return ChatGroq(model=resolved_model, api_key=resolved_key, temperature=temperature, max_tokens=max_tokens)
