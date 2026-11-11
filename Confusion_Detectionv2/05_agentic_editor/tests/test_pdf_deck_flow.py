"""End to end on a PDF-only deck, through the real server code:
generate (scripted model) -> checks + fact-check -> approve -> export ->
download, and the exported PDF has the new page in the right place."""
import hashlib
import json
import time

import pypdfium2
from fastapi.testclient import TestClient

from common import deck_workspace
from ..src.web_api import create_server

QUESTION = "Why is integration testing done incrementally rather than all at once?"
PAGES = [
    ("Software Engineering", "Integration testing"),
    ("Software Engineering", "Big bang integration combines every module at once and tests the whole system."),
    ("Software Engineering", "Top-down integration starts from the main control module and adds modules below it."),
    ("Software Engineering", "Bottom-up integration starts from the lowest modules and uses drivers to call them."),
]
NEW = {"operation": "insert_slide_after", "after_slide_id": 1, "title": "Why integrate incrementally",
       "bullets": ["Adding one module at a time narrows a new failure to the module just added.",
                   "Big bang integration makes faults hard to localise because everything changes at once."],
       "addresses_questions": [QUESTION]}


def _build_deck(root):
    from reportlab.pdfgen import canvas

    pdf = root / "unit.pdf"
    c = canvas.Canvas(str(pdf), pagesize=(720, 540))
    for banner, text in PAGES:
        c.setFont("Helvetica", 12)
        c.drawString(40, 510, banner)
        c.setFont("Helvetica", 18)
        c.drawString(40, 450, text[:70])
        c.showPage()
    c.save()
    ws = deck_workspace.register([pdf], deck_id="flow-unit", title="Flow Unit")
    parser = __import__("importlib").import_module("01_deck_parsing.src")
    parser.save_deck_json(parser.extract_any(ws.parse_source), ws.parsed_deck)
    ws.stages.mkdir(exist_ok=True)
    ws.topics.write_text(json.dumps([{"topic_id": 0, "start_slide_id": 0, "end_slide_id": 3, "slide_ids": [0, 1, 2, 3],
                                      "boundary_confidence": 0.0}]))
    text = "\n\n".join(t for _, t in PAGES)
    ws.gap_input.write_text(json.dumps({
        "generated_at": "test", "source_deck": str(ws.parsed_deck), "total_topics": 1, "total_questions": 1,
        "topics": [{"topic_id": 0, "slide_ids": [0, 1, 2, 3], "slide_range": "Slides 1-4", "boundary_confidence": 0.0,
                    "window_text": text, "cluster_size": 1,
                    "matched_questions": [{"question": QUESTION, "score": 0.8, "method": "semantic", "best_slide_id": 1}]}],
        "unmatched_questions": []}))
    ws.reports_dir.mkdir()
    (ws.reports_dir / "topic_00_report.json").write_text(json.dumps({
        "topic_id": 0, "assigned_slide_ids": [0, 1, 2, 3], "discovered_slide_ids": [], "gap_type": "shallow_coverage",
        "confidence": 0.8, "report_text": "The deck names integration strategies but never says why incremental integration helps.",
        "question_verdicts": [{"question": QUESTION, "verdict": "shallow_coverage"}]}))
    ws.reports_index.write_text(json.dumps([{"topic_id": 0, "filename": "topic_00_report.json", "gap_type": "shallow_coverage",
                                             "confidence": 0.8, "backed_by_questions": 1, "outcome": "completed", "severity": 1.0}]))
    ws.questions.write_text(QUESTION + "\n")
    deck_workspace.set_questions(ws, ws.questions, source="real")
    return ws


class Scripted:
    model = "test-fixture"
    max_tokens = 3000

    def generate(self, system, user):
        if "You fact-check" in system:
            return '{"problems": []}'
        assert "This deck is a PDF" in system  # the planner was told it may only add pages
        return json.dumps({"edits": [NEW]})


def test_pdf_deck_generate_approve_export_download(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKS_DIR", str(tmp_path / "decks"))
    ws = _build_deck(tmp_path)
    source_hash = hashlib.sha256(ws.pdf.read_bytes()).hexdigest()
    with TestClient(create_server(default_deck="flow-unit", state_root=tmp_path / "state", generator_factory=Scripted)) as client:
        workspace = client.get("/api/workspace").json()
        assert workspace["format"] == "pdf" and workspace["title"] == "Flow Unit"
        assert workspace["topics"][0]["eligible"]
        client.headers["x-workspace-token"] = workspace["token"]

        run = client.post("/api/runs", json={"kind": "gap", "target_id": 0}).json()
        deadline = time.monotonic() + 30
        while (status := client.get(f"/api/runs/{run['id']}").json())["status"] in ("queued", "running"):
            assert time.monotonic() < deadline, "run did not finish"
            time.sleep(.05)
        assert status["status"] == "completed", status
        proposal = next(p for p in client.get("/api/proposals").json() if p["title"] == NEW["title"])
        assert proposal["accepted"], proposal["problems"]

        preview = client.get(f"/api/proposals/{proposal['id']}/image")
        assert preview.status_code == 200 and preview.content[:4] == b"\x89PNG"

        assert client.post(f"/api/proposals/{proposal['id']}/decision", json={"decision": "approved"}).status_code == 200
        export = client.post("/api/apply", json={"proposal_ids": [proposal["id"]]}).json()
        pdf_bytes = client.get(export["download_url"]).content
        changes = client.get(export["changes_url"]).json()

    out = tmp_path / "edited.pdf"
    out.write_bytes(pdf_bytes)
    with pypdfium2.PdfDocument(str(out)) as doc:
        texts = [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
    assert len(texts) == 5
    assert "Why integrate incrementally" in texts[2]          # right after slide 2 (after_slide_id 1)
    assert "Software Engineering" in texts[2]                 # the deck's own banner on the new page
    assert "Top-down" in texts[3]                             # the original slide 3 moved down by one
    assert changes["edits"][0]["new_slide_numbers"] == [3]
    assert hashlib.sha256(ws.pdf.read_bytes()).hexdigest() == source_hash  # source untouched
