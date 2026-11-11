"""llm_replay.py -- record every LLM request/response, and replay them
without network calls.

Every Groq call in the pipeline goes through a ``.create(**request)``
method: stages 3 and 5 through ``client.chat.completions.create`` (the
client comes from ``common.llm_client.build_groq_client``), stage 4
through the ``RotatingCompletions`` adapter that ChatGroq is given as
its ``client``. Wrapping those two places covers every stage.

Mode is chosen with the ``LLM_REPLAY_MODE`` environment variable:

  off     (default) no wrapping at all -- identical to the old behaviour
  record  call the live API and save every successful response
  replay  never call the API; a request with no recording raises ReplayMiss
  auto    use a recording when there is one, otherwise call live and record

Recordings live in ``LLM_REPLAY_DIR`` (default ``.cache/llm_replay``), one
JSON file per distinct request. The key is a SHA-256 of the request
(model, messages, tools, temperature, ...), ignoring only transport
settings such as ``timeout``. A request made several times in one run
(a retry, a repeated stochastic run) is stored once per occurrence and
replayed in the same order, so a replayed run sees exactly the
responses the recorded run saw.

Exact replay reproduces recorded responses. It does not mean a live
rerun of the model would give the same output.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

MODES = ("off", "record", "replay", "auto")
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = _PROJECT_ROOT / ".cache" / "llm_replay"
# Not part of what the model sees, so not part of the key.
_IGNORED_KEYS = {"timeout", "extra_headers", "extra_query", "stream_options"}


class ReplayMiss(RuntimeError):
    """Replay mode found no recording for a request."""


def current_mode() -> str:
    mode = (os.getenv("LLM_REPLAY_MODE") or "off").strip().lower()
    if mode not in MODES:
        raise ValueError(f"LLM_REPLAY_MODE must be one of {MODES}, got {mode!r}")
    return mode


def _canonical(value: Any) -> Any:
    """JSON-safe, order-stable form of a request value."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def request_key(request: Dict[str, Any]) -> str:
    kept = {k: v for k, v in request.items() if k not in _IGNORED_KEYS}
    blob = json.dumps(_canonical(kept), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _git_revision() -> Optional[str]:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_PROJECT_ROOT, capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


def _dump_response(response: Any) -> Dict[str, Any]:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json")
    raise TypeError(f"Cannot record a response of type {type(response).__name__}")


def _load_response(data: Dict[str, Any]) -> Any:
    from groq.types.chat import ChatCompletion

    return ChatCompletion.model_validate(data)


class ReplayStore:
    """One directory of recordings, shared by every wrapper in a process."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self._lock = threading.Lock()
        self._served: Dict[str, int] = {}  # key -> responses already replayed this process
        self._revision = _git_revision()

    def _path(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def _read(self, key: str) -> Optional[Dict[str, Any]]:
        path = self._path(key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def next_response(self, key: str) -> Optional[Dict[str, Any]]:
        """The next unserved recorded response for this request, or None."""
        with self._lock:
            entry = self._read(key)
            if not entry:
                return None
            index = self._served.get(key, 0)
            if index >= len(entry["responses"]):
                return None
            self._served[key] = index + 1
            return entry["responses"][index]["response"]

    def append(self, key: str, request: Dict[str, Any], response: Any, latency_s: float) -> None:
        dumped = _dump_response(response)
        with self._lock:
            entry = self._read(key) or {"key": key, "request": _canonical(
                {k: v for k, v in request.items() if k not in _IGNORED_KEYS}), "responses": []}
            entry["responses"].append({
                "response": dumped,
                "model": dumped.get("model") or request.get("model"),
                "usage": dumped.get("usage"),
                "latency_s": round(latency_s, 3),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "git_revision": self._revision,
            })
            # A recorded response is now known to this process too.
            self._served[key] = len(entry["responses"])
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)


_STORES: Dict[str, ReplayStore] = {}
_STORES_LOCK = threading.Lock()


def get_store(directory: Optional[Path] = None) -> ReplayStore:
    resolved = Path(directory or os.getenv("LLM_REPLAY_DIR") or DEFAULT_DIR).resolve()
    with _STORES_LOCK:
        if str(resolved) not in _STORES:
            _STORES[str(resolved)] = ReplayStore(resolved)
        return _STORES[str(resolved)]


class ReplayCompletions:
    """Wraps anything with ``create(**request)`` (a Groq ``chat.completions``
    object, or ``RotatingCompletions``)."""

    def __init__(self, inner: Any, mode: str, store: ReplayStore):
        self._inner, self.mode, self.store = inner, mode, store

    def create(self, **request):
        if request.get("stream"):
            if self.mode == "replay":
                raise ReplayMiss("Streaming requests cannot be replayed")
            return self._inner.create(**request)
        key = request_key(request)
        if self.mode in ("replay", "auto"):
            recorded = self.store.next_response(key)
            if recorded is not None:
                return _load_response(recorded)
            if self.mode == "replay":
                raise ReplayMiss(f"No recorded response for request {key[:12]} "
                                 f"(model={request.get('model')!r}) in {self.store.directory}")
        if self._inner is None:
            raise ReplayMiss("No live client available (no API key) and no recording for this request")
        started = time.monotonic()
        response = self._inner.create(**request)
        self.store.append(key, request, response, time.monotonic() - started)
        return response

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _Chat:
    def __init__(self, completions):
        self.completions = completions


class ReplayClient:
    """Stands in for ``groq.Groq``: ``.chat.completions.create`` is
    recorded/replayed, everything else (``models.list``) passes through."""

    def __init__(self, inner: Any, mode: str, store: ReplayStore):
        self._inner = inner
        self.chat = _Chat(ReplayCompletions(inner.chat.completions if inner is not None else None, mode, store))

    def __getattr__(self, name):
        if self._inner is None:
            raise ReplayMiss(f"No live client in replay mode (attribute {name!r})")
        return getattr(self._inner, name)


def wrap_client(client: Any, mode: Optional[str] = None, store: Optional[ReplayStore] = None) -> Any:
    mode = mode or current_mode()
    if mode == "off":
        return client
    return ReplayClient(client, mode, store or get_store())


def wrap_completions(completions: Any, mode: Optional[str] = None, store: Optional[ReplayStore] = None) -> Any:
    mode = mode or current_mode()
    if mode == "off":
        return completions
    return ReplayCompletions(completions, mode, store or get_store())
