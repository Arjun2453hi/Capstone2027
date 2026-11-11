"""Record/replay of LLM calls: no network, a fake inner client."""
import pytest
from groq.types.chat import ChatCompletion

from common import llm_replay
from common.llm_replay import ReplayMiss, ReplayStore, request_key, wrap_client, wrap_completions


def _completion(text, model="m"):
    return ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    })


class FakeCompletions:
    def __init__(self, texts):
        self.texts, self.calls = list(texts), 0

    def create(self, **request):
        self.calls += 1
        return _completion(self.texts.pop(0), request.get("model", "m"))


class FakeClient:
    def __init__(self, texts):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(texts)
        self.models = "live-models"


REQ = {"model": "m", "temperature": 0.0, "messages": [{"role": "user", "content": "hi"}]}


def test_off_mode_returns_the_client_untouched(tmp_path):
    client = FakeClient(["a"])
    assert wrap_client(client, mode="off") is client
    completions = FakeCompletions(["a"])
    assert wrap_completions(completions, mode="off") is completions


def test_record_then_replay_needs_no_live_calls(tmp_path):
    live = FakeClient(["first", "second"])
    recorder = wrap_client(live, mode="record", store=ReplayStore(tmp_path))
    assert recorder.chat.completions.create(**REQ).choices[0].message.content == "first"
    assert recorder.chat.completions.create(**REQ).choices[0].message.content == "second"
    assert live.chat.completions.calls == 2

    # A new process: same directory, no live client at all.
    player = wrap_client(None, mode="replay", store=ReplayStore(tmp_path))
    first = player.chat.completions.create(**REQ)
    second = player.chat.completions.create(**REQ)
    # Repeated identical requests replay in the order they were recorded.
    assert [first.choices[0].message.content, second.choices[0].message.content] == ["first", "second"]
    assert first.usage.total_tokens == 5
    with pytest.raises(ReplayMiss):  # a third occurrence was never recorded
        player.chat.completions.create(**REQ)


def test_replay_miss_is_loud(tmp_path):
    player = wrap_client(None, mode="replay", store=ReplayStore(tmp_path))
    with pytest.raises(ReplayMiss):
        player.chat.completions.create(**REQ)


def test_auto_mode_calls_live_only_for_new_requests(tmp_path):
    wrap_client(FakeClient(["cached"]), mode="record", store=ReplayStore(tmp_path)).chat.completions.create(**REQ)
    live = FakeClient(["fresh"])
    auto = wrap_client(live, mode="auto", store=ReplayStore(tmp_path))
    assert auto.chat.completions.create(**REQ).choices[0].message.content == "cached"
    other = dict(REQ, messages=[{"role": "user", "content": "different"}])
    assert auto.chat.completions.create(**other).choices[0].message.content == "fresh"
    assert live.chat.completions.calls == 1


def test_key_ignores_timeout_but_not_content():
    assert request_key(REQ) == request_key(dict(REQ, timeout=30))
    assert request_key(REQ) != request_key(dict(REQ, temperature=0.7))
    assert request_key(REQ) != request_key(dict(REQ, model="other"))
    # Key order in the request does not matter.
    assert request_key({"b": 1, "a": 2}) == request_key({"a": 2, "b": 1})


def test_completions_adapter_wrapping_records_metadata(tmp_path):
    store = ReplayStore(tmp_path)
    wrapped = wrap_completions(FakeCompletions(["x"]), mode="record", store=store)
    wrapped.create(**REQ)
    entry = store._read(request_key(REQ))
    assert entry["request"]["model"] == "m"
    meta = entry["responses"][0]
    assert meta["usage"]["total_tokens"] == 5 and "recorded_at" in meta and "latency_s" in meta


def test_non_chat_attributes_pass_through(tmp_path):
    client = wrap_client(FakeClient(["a"]), mode="record", store=ReplayStore(tmp_path))
    assert client.models == "live-models"


def test_invalid_mode_rejected(monkeypatch):
    monkeypatch.setenv("LLM_REPLAY_MODE", "sometimes")
    with pytest.raises(ValueError):
        llm_replay.current_mode()


def test_chatgroq_replays_through_langchain_without_a_key(tmp_path):
    """Stage 4's path: ChatGroq(client=<wrapped completions>)."""
    from langchain_groq import ChatGroq

    store = ReplayStore(tmp_path)
    live = FakeCompletions(["recorded answer"])
    ChatGroq(model="m", api_key="k", temperature=0.0, client=wrap_completions(live, mode="record", store=store)).invoke("hi")
    player = ChatGroq(model="m", api_key="replay-mode", temperature=0.0,
                      client=wrap_completions(None, mode="replay", store=ReplayStore(tmp_path)))
    assert player.invoke("hi").content == "recorded answer"
    assert live.calls == 1
