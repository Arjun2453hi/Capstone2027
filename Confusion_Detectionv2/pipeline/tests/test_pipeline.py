"""Pipeline runner and synthetic questions, without network."""
import importlib
import json
import os

import pytest

from common import deck_workspace
from pipeline import runner
from pipeline.synthetic_questions import generate, write

schema = importlib.import_module("01_deck_parsing.src.schema")
Topic = importlib.import_module("02_topic_segmentation.src.schema").Topic


class FakeClient:
    """Answers every prompt with fixed questions; off-topic prompts get off-topic ones."""

    def __init__(self):
        self.prompts = []
        self.chat = type("C", (), {})()
        self.chat.completions = self

    def create(self, **request):
        prompt = request["messages"][-1]["content"]
        self.prompts.append(prompt)
        if "completely different subject" in prompt:
            items = [{"question": "How do I bake bread", "kind": "off_topic"}]
        else:
            n = len(self.prompts)
            items = [{"question": f"What is idea {n}?", "kind": "answered"},
                     {"question": f"Why does idea {n} matter?", "kind": "deeper"},
                     {"question": "What is idea 1?", "kind": "answered"},  # duplicate across topics
                     {"question": "bad", "kind": "nonsense"}]
        msg = type("M", (), {"content": json.dumps({"questions": items})})()
        return type("R", (), {"choices": [type("Ch", (), {"message": msg})()]})()


def _deck(texts):
    return schema.Deck(source_pdf="x", num_pages=len(texts), slides=[
        schema.Slide(slide_id=i, slide_number=i + 1, title=f"T{i}", title_font_size=None, raw_text=t)
        for i, t in enumerate(texts)])


def test_synthetic_questions_are_grounded_deduplicated_and_flagged(tmp_path):
    long = "Requirements engineering elicits and validates what stakeholders need. " * 5
    deck = _deck([long, long, "x", long])
    topics = [Topic(0, 0, 1, [0, 1], 0.0), Topic(1, 2, 2, [2], 0.4), Topic(2, 3, 3, [3], 0.5)]
    client = FakeClient()
    rows = generate(deck, topics, "Software Engineering", client, "m", progress=lambda m: None)
    assert len(client.prompts) == 3                        # topic 1 has too little text: no call
    assert "Requirements engineering" in client.prompts[0]  # grounded in the topic's own slides
    qs = [r["question"] for r in rows]
    assert len(qs) == len(set(q.lower() for q in qs))       # duplicates dropped
    assert all(r["kind"] in ("answered", "deeper", "missing", "off_topic") for r in rows)
    assert all(q.endswith("?") for q in qs)
    assert {r["topic_id"] for r in rows if r["kind"] != "off_topic"} == {0, 2}
    write(rows, tmp_path / "q.txt", tmp_path / "meta.json", "m", "SE")
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert "not a verified label" in meta["warning"] and meta["counts"]["off_topic"] == 1
    assert (tmp_path / "q.txt").read_text(encoding="utf-8").count("\n") == len(rows)


def _workspace(tmp_path, monkeypatch):
    from reportlab.pdfgen import canvas

    monkeypatch.setenv("DECKS_DIR", str(tmp_path / "decks"))
    pdf = tmp_path / "d.pdf"
    c = canvas.Canvas(str(pdf))
    c.drawString(40, 700, "slide")
    c.showPage()
    c.save()
    return deck_workspace.register([pdf], deck_id="d")


def test_pending_topics_resume_only_what_is_left(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch)
    ws.stages.mkdir(parents=True)
    ws.gap_input.write_text(json.dumps({"topics": [
        {"topic_id": 0, "matched_questions": [{"question": "a"}]},
        {"topic_id": 1, "matched_questions": [{"question": "b"}]},
        {"topic_id": 2, "matched_questions": []}],
        "unmatched_questions": [{"question": "c"}]}))
    assert runner.pending_topics(ws) is None  # no reports yet: everything
    ws.reports_dir.mkdir()
    (ws.reports_dir / "topic_00_report.json").write_text("{}")
    (ws.reports_dir / "topic_01_report.json").write_text("{}")
    ws.reports_index.write_text(json.dumps([{"topic_id": 0, "outcome": "completed"},
                                            {"topic_id": 1, "outcome": "rate_limit_failed"}]))
    assert runner.pending_topics(ws) == [1, -1]  # topic 2 has no questions; topic 1 failed; unmatched missing
    later = ws.reports_index.stat().st_mtime + 10
    os.utime(ws.gap_input, (later, later))       # the mapping changed after the reports
    assert runner.pending_topics(ws) is None


def test_run_refuses_a_deck_without_questions_unless_asked(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch)
    with pytest.raises(runner.PipelineError, match="no questions"):
        runner.run(ws, start="questions", stop="questions", progress=lambda m: None)


def test_editor_reads_pptx_text_but_falls_back_when_slides_do_not_line_up(tmp_path, monkeypatch):
    from pptx import Presentation

    monkeypatch.setenv("DECKS_DIR", str(tmp_path / "decks"))
    pdf_ws = _workspace(tmp_path, monkeypatch)  # a 1-page PDF deck
    parser = importlib.import_module("01_deck_parsing.src")
    parser.save_deck_json(parser.extract_any(pdf_ws.pdf), pdf_ws.parsed_deck)
    assert pdf_ws.editor_deck().slides[0].raw_text == parser.extract_any(pdf_ws.pdf).slides[0].raw_text  # no pptx

    prs = Presentation()
    for title in ("One", "Two"):
        prs.slides.add_slide(prs.slide_layouts[1]).shapes.title.text = title
    pptx = tmp_path / "d.pptx"
    prs.save(pptx)
    pdf_ws.manifest["pptx"] = str(pptx)
    pdf_ws.save()
    ws = deck_workspace.load("d")
    assert len(ws.editor_deck().slides) == 1  # 2 pptx slides vs 1 parsed page: keep the pipeline's parse
