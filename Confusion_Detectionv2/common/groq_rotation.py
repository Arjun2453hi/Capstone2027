"""Bounded credential failover below ChatGroq, preserving the request payload.

Keys are project-scoped; an organization ceiling may be shared. Association
cannot be inferred from credentials. Optional GROQ_PROJECT_N / GROQ_ORGANIZATION_N
environment labels document known associations without exposing credentials.
SDK retry settings are retained. One rotation cycle visits each usable key once,
then raises into the application's existing rate-limit retry/backoff loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import re
import time

from dotenv import dotenv_values
import httpx

from .llm_monitoring import LLMMonitor, PROJECT_ROOT, estimate_request, rate_headers, sanitize_text, sanitize_value, usage_fields


@dataclass(frozen=True)
class Credential:
    identifier: str
    value: str = field(repr=False)
    project: str | None = None
    organization: str | None = None


class GroqRequestTooLarge(ValueError):
    """An estimated context/output limit violation; never a rotation signal."""


def load_credentials(path=None, fallback_key=None):
    path = Path(path) if path else PROJECT_ROOT / ".env.groq-rotation"
    file_values = dotenv_values(path, interpolate=False) if path.is_file() else {}
    numbers = sorted({int(match.group(1)) for name in set(file_values) | set(os.environ)
                      if (match := re.fullmatch(r"GROQ_API_KEY_([1-9][0-9]*)", name))})
    keys, seen = [], set()
    for number in numbers:
        name = f"GROQ_API_KEY_{number}"
        # An explicitly empty process variable disables that numbered slot.
        value = os.environ.get(name, file_values.get(name))
        value = (value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        keys.append(Credential(f"KEY_{number}", value,
                               os.getenv(f"GROQ_PROJECT_{number}"),
                               os.getenv(f"GROQ_ORGANIZATION_{number}")))
    if not keys and fallback_key:
        keys.append(Credential("PRIMARY", fallback_key))
    return keys


def failure_kind(exc):
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    body = getattr(exc, "body", None)
    text = (str(exc) + " " + str(body or "")).lower()
    if status == 413 or any(term in text for term in (
        "context_length_exceeded", "maximum context length", "context window",
        "request too large", "request is too large", "reduce the length",
        "max_tokens must be", "max_tokens is too large",
    )) or isinstance(exc, GroqRequestTooLarge):
        return "oversized_request"
    if status in (401, 403):
        return "authentication_error"
    if status == 429:
        return "rate_limit"
    if status in (400, 404, 422):
        return "invalid_request"
    from groq import APIConnectionError, APITimeoutError
    if isinstance(exc, (APIConnectionError, APITimeoutError, httpx.TransportError)) or status in (408, 409) or (status and status >= 500):
        return "transient_error"
    return "other_provider_error"


def retry_delay(headers):
    """Use provider cooldown information without adding a new sleep loop."""
    for name, factor in (("retry-after-ms", 0.001), ("retry-after", 1)):
        try:
            delay = float(headers[name]) * factor
            if math.isfinite(delay) and delay >= 0:
                return delay
        except (KeyError, ValueError, TypeError):
            pass
    durations = []
    for name in ("x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        raw = headers.get(name, "")
        matches = re.findall(r"([0-9]+(?:\.[0-9]+)?)(ms|s|m|h)", raw)
        if matches:
            durations.append(sum(float(value) * {"ms": .001, "s": 1, "m": 60, "h": 3600}[unit] for value, unit in matches))
    return max(durations, default=0)


def rate_limit_details(exc):
    """Extract quota scope/type when the provider includes it in the error."""
    body = getattr(exc, "body", None)
    error = body.get("error", {}) if isinstance(body, dict) else {}
    error = error if isinstance(error, dict) else {}
    text = str(error.get("message") or exc)
    organization = re.search(r"organization\s+[`']([^`']+)[`']", text)
    details = {
        "rate_limit_type": error.get("type"),
        "provider_error_code": error.get("code"),
        "provider_organization": organization.group(1) if organization else None,
    }
    for field_name, label in (("quota_limit", "Limit"), ("quota_used", "Used"), ("quota_requested", "Requested")):
        match = re.search(rf"\b{label}\s*[:=]?\s*(\d+)", text, re.IGNORECASE)
        details[field_name] = int(match.group(1)) if match else None
    return details


def sanitized_exception(exc, secrets):
    """Remove credentials from errors before LangSmith or callers see them."""
    from groq import APIStatusError, APIConnectionError, APITimeoutError
    message = sanitize_text(str(exc), secrets)[:2048]
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    if isinstance(exc, APIStatusError):
        body = sanitize_value(getattr(exc, "body", None), secrets)
        headers = sanitize_value(rate_headers(exc.response.headers), secrets)
        response = httpx.Response(exc.status_code, headers=headers, request=request, json=body)
        return type(exc)(message, response=response, body=body)
    if isinstance(exc, APITimeoutError):
        return APITimeoutError(request=request)
    if isinstance(exc, APIConnectionError):
        return APIConnectionError(message=message, request=request)
    try:
        safe = type(exc)(message)
    except Exception:
        safe = RuntimeError(message)
    if hasattr(exc, "status_code"):
        safe.status_code = exc.status_code
    return safe


class RotatingCompletions:
    """Synchronous completion interface injected into the existing ChatGroq.

    All clients retain SDK defaults (including its two retries). The wrapper
    records provider-call outcomes, not hidden SDK HTTP attempts. Streaming is
    intentionally unsupported by this non-streaming application integration.
    """
    def __init__(self, credentials, *, monitor=None, client_factory=None, clock=time.monotonic):
        if not credentials:
            raise RuntimeError("No Groq credentials configured.")
        self._credentials = tuple(credentials)
        self._secrets = tuple(key.value for key in credentials)
        self.monitor = monitor or LLMMonitor(secrets=self._secrets)
        if client_factory is None:
            from groq import Groq
            client_factory = lambda key: Groq(api_key=key.value)
        self._clients = [client_factory(key) for key in credentials]
        self._current = 0
        self._cooldown_until = {}
        self._clock = clock
        self.monitor.emit("rotation_configuration", key_ids=[k.identifier for k in credentials],
                          key_associations=[{"key_id": k.identifier, "project": k.project,
                                             "organization": k.organization,
                                             "association_verified": bool(k.project and k.organization)} for k in credentials],
                          sdk_max_retries=2, quota_independence="not assumed")

    def create(self, **request):
        if request.get("stream"):
            raise ValueError("Groq rotation adapter supports the application's non-streaming requests only.")
        estimates = estimate_request(request)
        model = request.get("model")
        rotations = 0
        started = self._clock()
        self.monitor.emit("request_start", model=model, key_id=self._credentials[self._current].identifier, **estimates)
        if ((estimates["context_window_tokens"] is not None and estimates["estimated_total_tokens"] is not None
             and estimates["estimated_total_tokens"] > estimates["context_window_tokens"])
            or (estimates["model_max_output_tokens"] is not None and estimates["reserved_output_tokens"] is not None
                and estimates["reserved_output_tokens"] > estimates["model_max_output_tokens"])):
            message = (f"Request exceeds estimated model/context limits: input estimate={estimates['estimated_input_tokens']}, "
                       f"reserved output={estimates['reserved_output_tokens']}, context limit={estimates['context_window_tokens']}. "
                       "The input estimate is approximate. Credential rotation cannot fix an oversized request.")
            self.monitor.emit("request_outcome", model=model, key_id=self._credentials[self._current].identifier,
                              final_outcome="oversized_request", success=False, failure_kind="oversized_request",
                              exception_type="GroqRequestTooLarge", exception_message=message,
                              rotation_count=0, request_duration_seconds=self._clock() - started, **estimates)
            raise GroqRequestTooLarge(message)

        attempted = set()
        while True:
            index = self._current
            key = self._credentials[index]
            attempted.add(index)
            call_started = self._clock()
            try:
                raw = self._clients[index].chat.completions.with_raw_response.create(**request)
                headers = rate_headers(raw.headers)
                response = raw.parse()
            except Exception as exc:
                kind = failure_kind(exc)
                headers = rate_headers(getattr(getattr(exc, "response", None), "headers", None))
                reason = sanitize_text(str(exc), self._secrets)[:2048]
                self.monitor.emit("provider_failure", model=model, key_id=key.identifier,
                                  success=False, failure_kind=kind, rate_limit_event=kind == "rate_limit",
                                  rate_limit_reason=reason if kind == "rate_limit" else None,
                                  provider_headers=headers, exception_type=type(exc).__name__, exception_message=reason,
                                  request_duration_seconds=self._clock() - call_started, **estimates,
                                  **rate_limit_details(exc))
                if kind == "rate_limit":
                    self._cooldown_until[index] = self._clock() + retry_delay(headers)
                    next_index = next((candidate for offset in range(1, len(self._credentials))
                                       if (candidate := (index + offset) % len(self._credentials)) not in attempted
                                       and self._cooldown_until.get(candidate, 0) <= self._clock()), None)
                    if next_index is not None:
                        rotations += 1
                        self._current = next_index
                        new_key = self._credentials[next_index]
                        self.monitor.emit("key_rotation", model=model, key_id=key.identifier,
                                          previous_key_id=key.identifier, new_key_id=new_key.identifier,
                                          reason="rate_limit", rotation_count=rotations, provider_headers=headers)
                        print(f"[groq] rate limit: {key.identifier} -> {new_key.identifier}; retrying the same request")
                        continue
                self.monitor.emit("request_outcome", model=model, key_id=key.identifier,
                                  final_outcome=kind, success=False, rotation_count=rotations,
                                  request_duration_seconds=self._clock() - started)
                if kind == "oversized_request":
                    # Some TPM-size rejections arrive as 429. Do not let the
                    # existing broad 429 matcher treat these as recoverable.
                    raise GroqRequestTooLarge(
                        "Groq rejected an oversized request. Credential rotation cannot fix it. "
                        "See monitoring for the sanitized provider details."
                    ) from None
                safe = sanitized_exception(exc, self._secrets)
                raise safe from None
            self.monitor.emit("provider_success", model=model, key_id=key.identifier, success=True,
                              provider_headers=headers, request_duration_seconds=self._clock() - call_started,
                              **estimates, **usage_fields(response))
            self.monitor.emit("request_outcome", model=model, key_id=key.identifier,
                              final_outcome="success", success=True, rotation_count=rotations,
                              request_duration_seconds=self._clock() - started)
            return response
