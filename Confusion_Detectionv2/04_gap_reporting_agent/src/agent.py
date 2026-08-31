"""agent.py — builds and runs one topic's investigation loop.

**Deviation from claude.md Section 3, flagged explicitly**: the spec
names `create_tool_calling_agent` + `AgentExecutor` as the agent
framework. Both were removed from LangChain in the 1.0 rewrite (this
project installed langchain 1.3.18 -- current at build time) in favor
of LangGraph-based agents. Rather than pin an old, unmaintained
LangChain version to match a spec written against a now-superseded API
-- exactly the kind of stale-assumption bug this project has already
hit twice (Groq's model lineup, this stage's own claude.md assuming a
"common/llm_client.py" that didn't exist yet) -- this hand-rolls the
same loop directly against `ChatGroq.bind_tools()`. This isn't a
compromise: claude.md Section 8 already describes the orchestrator as
"the dumb dispatcher... look at which tool the model chose to call and
route accordingly," which a hand-rolled loop matches more directly than
either the removed AgentExecutor or LangGraph's create_react_agent
(neither has an obvious "this specific tool is terminal" concept built
in). LangSmith tracing still works transparently -- it instruments
every ChatGroq call made through langchain-core, with no dependency on
AgentExecutor specifically.
"""
from __future__ import annotations

import json
import time
from typing import List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from .prompts import FORCED_CONCLUSION_NUDGE, SYSTEM_PROMPT
from .schema import GapReport
from .tools import InvestigationContext, build_tools

MAX_ITERATIONS = 10  # claude.md Section 3: AgentExecutor(max_iterations=10) -- same cap, hand-rolled
MAX_RATE_LIMIT_RETRIES = 5  # claude.md Section 11: "e.g. 3 attempts" -- raised after measuring the real backoff needed

# Deliberate pacing between successive LLM calls within one
# investigation -- on request, to spread requests out rather than
# bursting through the account's real token budget as fast as possible.
# Doesn't fix an exhausted daily quota by itself (see
# _retry_after_seconds' docstring for that story), but reduces the
# chance of tripping a shorter-window burst limit on top of it, and is
# generally more considerate of the API than firing calls back-to-back.
INTER_STEP_DELAY_SECONDS = 3.0


def _is_rate_limit_error(exc: Exception) -> bool:
    try:
        from groq import RateLimitError as GroqRateLimitError

        if isinstance(exc, GroqRateLimitError):
            return True
    except ImportError:
        pass
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return status == 429 or "429" in str(exc) or "rate_limit" in str(exc).lower()


def _retry_after_seconds(exc: Exception, attempt: int) -> float:
    """Prefer Groq's own real retry-after response header (claude.md
    Section 11: "Groq returns a standard HTTP 429 with retry-after
    information") over a fixed guessed exponential schedule -- measured
    in practice on a real 24-topic run: a naive 2s/4s guess was nowhere
    near enough for the account's actual per-minute quota, and every
    topic after the first two failed as a result. Falls back to
    exponential backoff only when the header genuinely isn't present."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is not None:
        raw_ms = headers.get("retry-after-ms")
        if raw_ms is not None:
            try:
                return float(raw_ms) / 1000
            except ValueError:
                pass
        raw = headers.get("retry-after")
        if raw is not None:
            try:
                return float(raw)
            except ValueError:
                pass
    return float(2**attempt)


def _is_tool_call_parse_error(exc: Exception) -> bool:
    """Groq returns HTTP 400 "tool_use_failed" when the model's own
    generated tool-call arguments aren't valid JSON -- observed in
    practice when report_text is long enough that generation gets cut
    off mid-string before the closing braces. Distinct from a rate
    limit: retrying the *same* messages would just reproduce the same
    truncation, so this needs a corrective nudge, not a backoff."""
    status = getattr(exc, "status_code", None)
    text = str(exc)
    return status == 400 and ("tool_use_failed" in text or "Failed to parse tool call" in text)


TOOL_CALL_PARSE_ERROR_NUDGE = (
    "Your last tool call could not be parsed as valid JSON -- most likely report_text "
    "was too long and got truncated mid-generation. Call write_report again with a more "
    "concise report_text: a few clear paragraphs summarizing the pattern across "
    "questions, not an exhaustive per-question list."
)


def _try_build_gap_report(args: dict):
    """GapReport(**args) is built directly from whatever the model sent
    write_report -- Pydantic validation (an out-of-range confidence, an
    invalid gap_type string, a missing field) must not be allowed to
    raise uncaught here: that would crash this one topic's
    investigation, or worse the entire multi-topic run, over a
    malformed argument the model can simply be asked to fix. Returns
    (report, None) on success, (None, error_message) on failure."""
    try:
        return GapReport(**args), None
    except Exception as e:
        return None, str(e)


def _invoke_with_retry(model_with_tools, messages, topic_label: str):
    """Exponential backoff on a detected rate limit (claude.md Section
    11): detect specifically, retry a small bounded number of times,
    logged clearly each attempt -- never silently and never
    infinitely."""
    for attempt in range(1, MAX_RATE_LIMIT_RETRIES + 1):
        try:
            return model_with_tools.invoke(messages)
        except Exception as e:
            if not _is_rate_limit_error(e) or attempt == MAX_RATE_LIMIT_RETRIES:
                raise
            wait = _retry_after_seconds(e, attempt)
            print(f"[warn] rate limit hit on {topic_label} -- waiting {wait:.1f}s, attempt {attempt}/{MAX_RATE_LIMIT_RETRIES}")
            time.sleep(wait)
    raise RuntimeError("unreachable")  # the loop above always returns or re-raises


def _rate_limit_failed_report(topic_id: int, when: str) -> GapReport:
    return GapReport(
        topic_id=topic_id,
        slide_ids_examined=[],
        gap_type="shallow_coverage",
        confidence=0.0,
        report_text=(
            f"Investigation failed due to persistent Groq rate limiting {when} "
            f"after {MAX_RATE_LIMIT_RETRIES} retries; this is not a content judgment."
        ),
    )


def run_topic_investigation(chat_model, ctx: InvestigationContext, topic_id: int, kickoff_message: str) -> dict:
    """Runs one topic's fresh, isolated investigation -- no memory
    shared with any other topic (claude.md Section 5). Returns
    {"report": GapReport, "outcome": "completed" | "cap_hit" |
    "rate_limit_failed", "n_tool_calls": int}."""
    tools = build_tools(ctx)
    tool_lookup = {t.name: t for t in tools}
    model_with_tools = chat_model.bind_tools(tools)

    messages: List = [
        SystemMessage(content=SYSTEM_PROMPT.format(MAX_STEPS=MAX_ITERATIONS)),
        HumanMessage(content=kickoff_message),
    ]
    topic_label = f"topic {topic_id}"

    for step in range(1, MAX_ITERATIONS + 1):
        if step > 1:
            time.sleep(INTER_STEP_DELAY_SECONDS)
        try:
            response: AIMessage = _invoke_with_retry(model_with_tools, messages, topic_label)
        except Exception as e:
            if _is_rate_limit_error(e):
                return {
                    "report": _rate_limit_failed_report(topic_id, f"at step {step}"),
                    "outcome": "rate_limit_failed",
                    "n_tool_calls": step - 1,
                }
            if _is_tool_call_parse_error(e):
                print(f"[warn] malformed tool-call JSON on {topic_label}, step {step} -- asking for a more concise retry")
                messages.append(HumanMessage(content=TOOL_CALL_PARSE_ERROR_NUDGE))
                continue
            raise

        messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []

        if not tool_calls:
            # Plain-text reply instead of a tool call -- nudge back
            # toward the checklist rather than silently treating a
            # non-terminal message as the end of the investigation.
            messages.append(HumanMessage(content="Continue your investigation, or call write_report to conclude."))
            continue

        # Every tool_call in this response needs exactly one ToolMessage
        # reply before the next invoke -- including a write_report call
        # that fails Pydantic validation, which is why that case appends
        # an error ToolMessage and keeps looping rather than returning.
        for call in tool_calls:
            if call["name"] == "write_report":
                report, error = _try_build_gap_report(call["args"])
                if report is not None:
                    return {"report": report, "outcome": "completed", "n_tool_calls": step}
                print(f"[warn] invalid write_report arguments on {topic_label}, step {step}: {error}")
                messages.append(
                    ToolMessage(
                        content=(
                            f"Invalid write_report arguments: {error}. Call write_report again with valid "
                            "fields (gap_type must be exactly one of complete_omission, shallow_coverage, "
                            "fragmented_context, covered; confidence must be a number between 0 and 1)."
                        ),
                        tool_call_id=call["id"],
                    )
                )
                continue

            tool_fn = tool_lookup.get(call["name"])
            result = {"error": f"unknown tool {call['name']}"} if tool_fn is None else tool_fn.invoke(call["args"])
            messages.append(ToolMessage(content=json.dumps(result, default=str), tool_call_id=call["id"]))

    # Hit MAX_ITERATIONS without the model calling write_report on its own.
    messages.append(HumanMessage(content=FORCED_CONCLUSION_NUDGE))
    time.sleep(INTER_STEP_DELAY_SECONDS)
    response = None
    try:
        response = _invoke_with_retry(model_with_tools, messages, topic_label)
    except Exception as e:
        if _is_rate_limit_error(e):
            return {
                "report": _rate_limit_failed_report(topic_id, "during forced conclusion"),
                "outcome": "rate_limit_failed",
                "n_tool_calls": MAX_ITERATIONS,
            }
        if not _is_tool_call_parse_error(e):
            raise
        # A parse error here has no further loop iteration to retry
        # into -- fall through to the "model didn't conclude" flagged
        # report below rather than raising past the step budget.
        print(f"[warn] malformed tool-call JSON on {topic_label} during forced conclusion -- giving up on this topic")

    tool_calls = getattr(response, "tool_calls", None) or []
    write_report_call = next((c for c in tool_calls if c["name"] == "write_report"), None)
    if write_report_call is not None:
        args = dict(write_report_call["args"])
        args["confidence"] = min(float(args.get("confidence", 0.4)), 0.4)  # cap enforced regardless of what the model sent
        report, error = _try_build_gap_report(args)
        if report is not None:
            return {"report": report, "outcome": "cap_hit", "n_tool_calls": MAX_ITERATIONS + 1}
        # No further loop iteration to retry into here -- fall through
        # to the flagged fallback below, same as any other forced-
        # conclusion failure.
        print(f"[warn] invalid write_report arguments on {topic_label} during forced conclusion: {error}")

    # Model still didn't call write_report even after the forced nudge --
    # construct a minimal, clearly-flagged report ourselves rather than
    # crash or silently drop this topic from the run.
    report = GapReport(
        topic_id=topic_id,
        slide_ids_examined=[],
        gap_type="shallow_coverage",
        confidence=0.0,
        report_text=(
            "Investigation was cut short at the maximum step count and the model did not "
            "conclude with a report even after a forced-conclusion nudge."
        ),
    )
    return {"report": report, "outcome": "cap_hit", "n_tool_calls": MAX_ITERATIONS + 1}
