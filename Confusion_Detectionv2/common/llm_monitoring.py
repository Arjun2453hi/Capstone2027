"""Local request/rotation telemetry complementing existing LangSmith traces.

Run ``python -m common.llm_monitoring --path <events.jsonl>`` for a summary.
Provider usage is authoritative; estimates and unknown usage remain separate.
No prompts, request headers, or credentials are written to telemetry.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUEST_CONTEXT = ContextVar("groq_request_context", default={})
RATE_HEADERS = {
    "retry-after", "retry-after-ms", "x-ratelimit-limit-requests",
    "x-ratelimit-limit-tokens", "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens", "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens", "x-request-id",
}
# Verified model limits; other models must supply an explicit context setting.
MODEL_LIMITS = {"openai/gpt-oss-20b": (131072, 65536)}


def sanitize_text(value, secrets=()):
    text = str(value)
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"gsk_[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)(bearer\s+)[^\s\"']+", r"\1[REDACTED]", text)
    return text


def sanitize_value(value, secrets=()):
    if isinstance(value, dict):
        return {sanitize_text(k, secrets): sanitize_value(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_value(v, secrets) for v in value]
    return sanitize_text(value, secrets) if isinstance(value, str) else value


def rate_headers(headers):
    return {k.lower(): str(v) for k, v in (headers or {}).items() if k.lower() in RATE_HEADERS}


@contextmanager
def invocation_context(topic_id, topic_attempt, invocation_number, request_id=None, application_retry_attempt=None):
    metadata = {
        "topic_id": topic_id, "topic_attempt": topic_attempt,
        "llm_invocation_number": invocation_number,
        "request_id": request_id or uuid4().hex,
        "application_retry_attempt": application_retry_attempt,
    }
    token = REQUEST_CONTEXT.set(metadata)
    try:
        yield metadata
    finally:
        REQUEST_CONTEXT.reset(token)


def estimate_request(request):
    """Estimate serialized message/tool size without downloading a tokenizer.

    UTF-8 bytes / 3 is a conservative heuristic for ordinary English, not an
    exact tokenizer. Record the method so these numbers cannot pass as usage.
    Include schemas, tool arguments/results and non-content message fields.
    """
    payload = {k: request[k] for k in ("messages", "tools", "tool_choice", "response_format") if k in request}
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    byte_count = len(serialized.encode("utf-8"))
    estimate = math.ceil(byte_count / 3) + 12 * len(request.get("messages", [])) + 32
    reserved = request.get("max_completion_tokens", request.get("max_tokens"))
    reserved = int(reserved) if reserved is not None else None
    limits = MODEL_LIMITS.get(request.get("model"), (None, None))
    context_limit = os.getenv("GROQ_CONTEXT_WINDOW_TOKENS")
    return {
        "estimated_input_tokens": estimate,
        "reserved_output_tokens": reserved,
        "estimated_total_tokens": estimate + reserved if reserved is not None else None,
        "estimation_method": "utf8_bytes_div_3_plus_message_overhead (approximate)",
        "serialized_context_bytes": byte_count,
        "message_count": len(request.get("messages", [])),
        "tool_count": len(request.get("tools", [])),
        "context_window_tokens": int(context_limit) if context_limit else limits[0],
        "model_max_output_tokens": limits[1],
    }


def usage_fields(response):
    if hasattr(response, "model_dump"):
        response = response.model_dump()
    usage = response.get("usage") if isinstance(response, dict) else None
    usage = usage or {}
    return {
        "actual_input_tokens": usage.get("prompt_tokens"),
        "actual_output_tokens": usage.get("completion_tokens"),
        "actual_total_tokens": usage.get("total_tokens"),
        "provider_usage": usage,
    }


class LLMMonitor:
    def __init__(self, path=None, secrets=()):
        self.run_id = uuid4().hex
        configured_path = os.getenv("GROQ_MONITORING_PATH")
        self.path = Path(path or configured_path or PROJECT_ROOT / ".cache" / "groq-monitoring" / f"events-{self.run_id}.jsonl")
        self._secrets = tuple(secrets)
        self._lock = threading.Lock()
        self._totals = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "responses_with_usage": 0, "responses_without_usage": 0})
        self._warned = False

    def emit(self, event, **fields):
        with self._lock:
            key = fields.get("key_id")
            if event == "provider_success" and key:
                totals = self._totals[key]
                if fields.get("actual_total_tokens") is None:
                    totals["responses_without_usage"] += 1
                else:
                    totals["responses_with_usage"] += 1
                    for name in ("input", "output", "total"):
                        value = fields.get(f"actual_{name}_tokens")
                        if value is not None:
                            totals[f"{name}_tokens"] += value
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "run_id": self.run_id, **REQUEST_CONTEXT.get(),
                "event": event, **fields,
                "observed_key_usage": dict(self._totals.get(key, {})),
            }
            record = sanitize_value(record, self._secrets)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError:
                # Observability must not turn a completed generation into a retry.
                if not self._warned:
                    print("[warn] Groq monitoring file could not be written; application execution continues.")
                    self._warned = True


def invoke_monitored(model, messages, *, topic_id, topic_attempt, invocation_number, application_retry_attempt, request_id):
    """Metadata-only bridge; the completion adapter owns usage accounting."""
    bound = getattr(model, "bound", model)
    adapter = getattr(bound, "client", None)
    monitor = getattr(adapter, "monitor", None)
    with invocation_context(topic_id, topic_attempt, invocation_number, request_id, application_retry_attempt) as metadata:
        started = time.monotonic()
        try:
            if monitor is None:
                return model.invoke(messages)  # Preserve injected test models.
            response = model.invoke(messages, config={"metadata": metadata})
        except Exception as exc:
            if monitor:
                monitor.emit("invocation_outcome", final_outcome="failure", success=False,
                             exception_type=type(exc).__name__, exception_message=str(exc)[:2048],
                             request_duration_seconds=time.monotonic() - started)
            raise
        if monitor:
            monitor.emit("invocation_outcome", final_outcome="success", success=True,
                         request_duration_seconds=time.monotonic() - started)
        return response


def summarize(path):
    """Summarize observed calls, not organization-wide billing/quota."""
    keys, topics = defaultdict(lambda: defaultdict(int)), defaultdict(lambda: defaultdict(int))
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            key, topic = keys[row.get("key_id", "unknown")], topics[str(row.get("topic_id"))]
            event = row["event"]
            if event == "provider_success":
                for group in (key, topic):
                    group["successful_provider_requests"] += 1
                    for name in ("input", "output", "total"):
                        count = row.get(f"actual_{name}_tokens")
                        if count is not None:
                            group[f"actual_{name}_tokens"] += count
                    if row.get("actual_total_tokens") is None:
                        group["responses_without_usage"] += 1
            if event == "provider_failure":
                for group in (key, topic):
                    group["failed_provider_requests"] += 1
                    group[row.get("failure_kind", "other_provider_error")] += 1
            if event == "key_rotation":
                key["rotations_away"] += 1
                topic["rotations"] += 1
            if event == "request_outcome":
                topic[f"requests_{row['final_outcome']}"] += 1
                if row.get("success") and row.get("rotation_count", 0):
                    topic["requests_succeeded_after_rotation"] += 1
    return {"keys": {k: dict(v) for k, v in keys.items() if k != "unknown"},
            "topics": {k: dict(v) for k, v in topics.items() if k != "None"},
            "accounting_scope": "Observed successful responses only; missing usage and other processes are not counted."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    print(json.dumps(summarize(parser.parse_args().path), indent=2))
