import importlib
import json

import pytest

context_module = importlib.import_module("05_agentic_editor.src.context")


@pytest.mark.parametrize("topic_id", [20, 13])
def test_real_topic_context_and_save(topic_id, tmp_path):
    context = context_module.load_topic_context(topic_id)
    root = context_module.PROJECT_ROOT
    report = json.loads((root / f"04_gap_reporting_agent/output/topic_{topic_id:02d}_report.json").read_text(encoding="utf-8"))
    deck = json.loads((root / "01_deck_parsing/parsed_deck.json").read_text(encoding="utf-8"))
    slides = {slide["slide_id"]: slide for slide in deck["slides"]}
    assert context["report_text"] == report["report_text"]
    assert context["report_text"].strip()
    assert [s["slide_id"] for s in context["slide_content"]] == context["topic"]["slide_ids"]
    for slide in context["slide_content"] + context["reference_slide_content"]:
        assert slide == slides[slide["slide_id"]]
    editable = set(context["topic"]["slide_ids"])
    assert {s["slide_id"] for s in context["reference_slide_content"]} == set(report["discovered_slide_ids"]) - editable
    assert "questions" not in context
    output = tmp_path / "context.json"
    context_module.save_topic_context(context, output)
    assert json.loads(output.read_text(encoding="utf-8")) == context


def test_unknown_topic():
    with pytest.raises(ValueError, match="Unknown topic_id"):
        context_module.load_topic_context(999)


def test_editor_llm_uses_shared_client(monkeypatch):
    from common import llm_client

    calls = []
    sentinel = object()

    def fake_client(**kwargs):
        calls.append(kwargs)
        return sentinel

    monkeypatch.setattr(llm_client, "get_validated_chat_groq", fake_client)
    module = importlib.import_module("05_agentic_editor.src.llm")
    assert module.build_editor_llm(model_name="test-model", api_key="test-key") is sentinel
    assert calls == [{"model_name": "test-model", "api_key": "test-key", "temperature": 0.0}]
