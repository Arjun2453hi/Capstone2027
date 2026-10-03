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
from typing import List, Optional
from uuid import uuid4

from common.llm_monitoring import invoke_monitored

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from .prompts import FORCED_CONCLUSION_NUDGE, SYSTEM_PROMPT
from .schema import GapReport
from .tools import InvestigationContext, build_tools

MAX_ITERATIONS = 10  # claude.md Section 3: AgentExecutor(max_iterations=10) -- same cap, hand-rolled
MAX_RATE_LIMIT_RETRIES = 5  # claude.md Section 11: "e.g. 3 attempts" -- raised after measuring the real backoff needed

# Remediation (Phase 3): the agent must not claim a concept is missing
# from the deck without having actually searched for it. Checked
# against both gap_type and report_text -- a model could technically
# set gap_type="shallow_coverage" while still asserting in prose that
# some specific concept is "not covered," which is the same unverified
# claim under a different label.
SEARCH_TOOL_NAMES = {"search_similar_slides", "search_expanding_context"}
OMISSION_KEYWORDS = [
    "not covered",
    "not addressed",
    "omitted",
    "completely absent",
    "does not cover",
    "doesn't cover",
    "no mention of",
    "not mentioned",
    "isn't covered",
    "isn't addressed",
    "not present in",
    "not found in the slides",
]
OMISSION_GUARD_NUDGE = (
    "You claimed something is not covered, omitted, or missing, but you haven't made any "
    "search_similar_slides or search_expanding_context call in this investigation yet. You "
    "must actually search before concluding something is missing -- it may exist elsewhere "
    "in the deck, or just outside this topic's slide range. Investigate further with one of "
    "those tools, then call write_report again."
)


def _claims_unverified_omission(gap_type: str, report_text: str) -> bool:
    if gap_type == "complete_omission":
        return True
    lowered = (report_text or "").lower()
    return any(kw in lowered for kw in OMISSION_KEYWORDS)


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
    off mid-string. Distinct from a rate limit: retrying the *same*
    messages would just reproduce the same truncation, so this needs a
    corrective nudge, not a backoff."""
    status = getattr(exc, "status_code", None)
    text = str(exc)
    return status == 400 and ("tool_use_failed" in text or "Failed to parse tool call" in text)


TOOL_CALL_PARSE_ERROR_NUDGE = (
    "Your last tool call could not be parsed as valid JSON -- most likely report_text "
    "was too long and got truncated mid-generation. Call write_report again with a more "
    "concise report_text: a few clear paragraphs summarizing the pattern across "
    "questions, not an exhaustive per-question list."
)


def _invoke_with_retry(model_with_tools, messages, topic_label: str, *, topic_id=None, invocation_number=None):
    """Exponential backoff on a detected rate limit (claude.md Section
    11): detect specifically, retry a small bounded number of times,
    logged clearly each attempt -- never silently and never
    infinitely.

    This is the ONLY real Groq call site in this stage's investigation
    loop -- confirmed by review: none of the 5 tools (tools.py) make
    their own Groq call. search_expanding_context/search_similar_slides
    call ctx.embed(), which goes to the injected embedding model (a
    local sentence-transformers/BGE model, no network call at all), not
    Groq. The only OTHER real Groq call in this whole stage is
    common/llm_client.py's one-time startup model-list validation
    (get_validated_chat_groq), which runs once per orchestrator run
    (not per topic) and already degrades gracefully on its own failure
    (warns and skips validation, never raises) -- it doesn't need this
    same retry/backoff treatment since it isn't on the per-investigation
    critical path.
    """
    request_id = uuid4().hex
    for attempt in range(1, MAX_RATE_LIMIT_RETRIES + 1):
        try:
            return invoke_monitored(
                model_with_tools, messages, topic_id=topic_id, topic_attempt=1,
                invocation_number=invocation_number, application_retry_attempt=attempt,
                request_id=request_id,
            )
        except Exception as e:
            if not _is_rate_limit_error(e) or attempt == MAX_RATE_LIMIT_RETRIES:
                raise
            wait = _retry_after_seconds(e, attempt)
            print(f"[warn] rate limit hit on {topic_label} -- waiting {wait:.1f}s, attempt {attempt}/{MAX_RATE_LIMIT_RETRIES}")
            time.sleep(wait)
    raise RuntimeError("unreachable")  # the loop above always returns or re-raises


def _rate_limit_failed_report(topic_id: int, assigned_slide_ids: List[int], discovered_slide_ids: List[int], when: str) -> GapReport:
    return GapReport(
        topic_id=topic_id,
        assigned_slide_ids=assigned_slide_ids,
        discovered_slide_ids=discovered_slide_ids,
        gap_type="shallow_coverage",
        confidence=0.0,
        report_text=(
            f"Investigation failed due to persistent Groq rate limiting {when} "
            f"after {MAX_RATE_LIMIT_RETRIES} retries; this is not a content judgment."
        ),
    )


def _extract_discovered_slide_ids(tool_name: str, result) -> List[int]:
    """Real telemetry, not a model self-report: pulls the actual
    slide_ids a search tool's result claims to have found. Only
    search_expanding_context and search_similar_slides contribute here
    -- get_topic_slides returns the topic's ASSIGNED range (tracked
    separately, from ctx, not from tool output) and
    get_matched_questions/write_report don't reference slides at all."""
    if tool_name == "search_expanding_context" and isinstance(result, dict):
        return [sid for sid in result.get("window_slide_ids", []) if isinstance(sid, int)]
    if tool_name == "search_similar_slides" and isinstance(result, list):
        return [item["slide_id"] for item in result if isinstance(item, dict) and "slide_id" in item]
    return []


def _build_report(topic_id: int, args: dict, assigned_slide_ids: List[int], discovered_slide_ids: List[int]) -> GapReport:
    return GapReport(
        topic_id=args.get("topic_id", topic_id),
        assigned_slide_ids=assigned_slide_ids,
        discovered_slide_ids=sorted(set(discovered_slide_ids)),
        gap_type=args["gap_type"],
        confidence=args["confidence"],
        report_text=args["report_text"],
    )


def run_topic_investigation(chat_model, ctx: InvestigationContext, topic_id: int, kickoff_message: str) -> dict:
    """Runs one topic's fresh, isolated investigation -- no memory
    shared with any other topic (claude.md Section 5). Returns
    {"report": GapReport, "outcome": "completed" | "cap_hit" |
    "rate_limit_failed", "n_tool_calls": int}."""
    tools = build_tools(ctx)
    tool_lookup = {t.name: t for t in tools}
    model_with_tools = chat_model.bind_tools(tools)

    dossier = ctx.dossiers_by_id.get(topic_id)
    assigned_slide_ids: List[int] = list(dossier.slide_ids) if dossier is not None else []
    discovered_slide_ids: List[int] = []
    search_tool_calls_made = 0

    messages: List = [
        SystemMessage(content=SYSTEM_PROMPT.format(MAX_STEPS=MAX_ITERATIONS)),
        HumanMessage(content=kickoff_message),
    ]
    topic_label = f"topic {topic_id}"

    for step in range(1, MAX_ITERATIONS + 1):
        try:
            response: AIMessage = _invoke_with_retry(
                model_with_tools, messages, topic_label, topic_id=topic_id, invocation_number=step
            )
        except Exception as e:
            if _is_rate_limit_error(e):
                return {
                    "report": _rate_limit_failed_report(topic_id, assigned_slide_ids, discovered_slide_ids, f"at step {step}"),
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

        write_report_call = next((c for c in tool_calls if c["name"] == "write_report"), None)
        if write_report_call is not None:
            args = write_report_call["args"]
            if _claims_unverified_omission(args.get("gap_type", ""), args.get("report_text", "")) and search_tool_calls_made == 0:
                print(f"[warn] {topic_label}: rejected an unverified omission claim (no search tool calls made yet)")
                # Respond to the pending tool_call_id with a rejection
                # (not a duplicate AIMessage) -- every tool_call in an
                # AIMessage needs a matching ToolMessage response before
                # the next AIMessage, or the API rejects the next call.
                messages.append(
                    ToolMessage(
                        content=json.dumps({"error": "rejected", "reason": OMISSION_GUARD_NUDGE}),
                        tool_call_id=write_report_call["id"],
                    )
                )
                continue
            try:
                report = _build_report(topic_id, args, assigned_slide_ids, discovered_slide_ids)
            except ValidationError as e:
                # e.g. an invalid gap_type -- GapReportArgs types it as
                # a plain str (the tool schema can't express the
                # Literal constraint as a hard reject), so this is
                # where an invalid value actually surfaces. Reject and
                # retry, same shape as the other corrective nudges --
                # must not crash the whole investigation over one bad
                # argument.
                print(f"[warn] {topic_label}: write_report arguments failed validation -- asking for a retry")
                messages.append(
                    ToolMessage(
                        content=json.dumps({"error": "invalid_arguments", "reason": str(e)}),
                        tool_call_id=write_report_call["id"],
                    )
                )
                continue
            return {"report": report, "outcome": "completed", "n_tool_calls": step}

        if not tool_calls:
            # Plain-text reply instead of a tool call -- nudge back
            # toward the checklist rather than silently treating a
            # non-terminal message as the end of the investigation.
            messages.append(HumanMessage(content="Continue your investigation, or call write_report to conclude."))
            continue

        for call in tool_calls:
            tool_fn = tool_lookup.get(call["name"])
            result = {"error": f"unknown tool {call['name']}"} if tool_fn is None else tool_fn.invoke(call["args"])
            if call["name"] in SEARCH_TOOL_NAMES:
                search_tool_calls_made += 1
                discovered_slide_ids.extend(_extract_discovered_slide_ids(call["name"], result))
            messages.append(ToolMessage(content=json.dumps(result, default=str), tool_call_id=call["id"]))

    # Hit MAX_ITERATIONS without the model calling write_report on its own.
    messages.append(HumanMessage(content=FORCED_CONCLUSION_NUDGE))
    response = None
    try:
        response = _invoke_with_retry(
            model_with_tools, messages, topic_label, topic_id=topic_id, invocation_number=MAX_ITERATIONS + 1
        )
    except Exception as e:
        if _is_rate_limit_error(e):
            return {
                "report": _rate_limit_failed_report(
                    topic_id, assigned_slide_ids, discovered_slide_ids, "during forced conclusion"
                ),
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
        # An unverified omission claim is rejected here too -- there's no
        # further loop iteration to nudge into, so it falls through to
        # the generic cut-short report below instead of being accepted.
        if not (_claims_unverified_omission(args.get("gap_type", ""), args.get("report_text", "")) and search_tool_calls_made == 0):
            try:
                report = _build_report(topic_id, args, assigned_slide_ids, discovered_slide_ids)
                return {"report": report, "outcome": "cap_hit", "n_tool_calls": MAX_ITERATIONS + 1}
            except ValidationError:
                # No further loop iteration to retry into here either --
                # fall through to the generic cut-short report below.
                print(f"[warn] {topic_label}: forced-conclusion write_report arguments failed validation -- discarded")
        else:
            print(f"[warn] {topic_label}: forced-conclusion write_report claimed unverified omission -- discarded")

    # Model still didn't call write_report even after the forced nudge
    # (or its forced conclusion was an unverified omission claim, which
    # is not accepted either) -- construct a minimal, clearly-flagged
    # report ourselves rather than crash or silently drop this topic.
    report = GapReport(
        topic_id=topic_id,
        assigned_slide_ids=assigned_slide_ids,
        discovered_slide_ids=sorted(set(discovered_slide_ids)),
        gap_type="shallow_coverage",
        confidence=0.0,
        report_text=(
            "Investigation was cut short at the maximum step count and the model did not "
            "conclude with a verifiable report even after a forced-conclusion nudge."
        ),
    )
    return {"report": report, "outcome": "cap_hit", "n_tool_calls": MAX_ITERATIONS + 1}
