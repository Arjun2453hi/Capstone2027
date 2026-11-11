"""Multi-deck server and PDF-only decks."""
import json

import pypdfium2
import pytest
from fastapi.testclient import TestClient

from common import deck_workspace
from ..src.pdf_applier import PDF_ONLY_PROBLEM, apply_edits_pdf, find_banner, pdf_room_check
from ..src.schema import APPEND_BULLETS, SlideEdit
from ..src.web_api import create_app, create_server

PDF_DECK = next((d for d in deck_workspace.list_decks() if d.editable_format == "pdf" and d.parsed_deck.exists()), None)
PPTX_DECK = next((d for d in deck_workspace.list_decks() if d.editable_format == "pptx" and d.reports_dir.exists()), None)
needs_decks = pytest.mark.skipif(PDF_DECK is None or PPTX_DECK is None, reason="needs a processed PDF deck and a .pptx deck")


def _edit(after, title="Added", operation="insert_slide_after"):
    return SlideEdit(topic_id=0, after_slide_id=after, title=title, bullets=["First point.", "Second point."],
                     addresses_questions=["q?"], operation=operation)


def _pdf(tmp_path, pages=4):
    from reportlab.pdfgen import canvas

    path = tmp_path / "deck.pdf"
    c = canvas.Canvas(str(path), pagesize=(720, 540))
    for i in range(pages):
        c.drawString(40, 500, "Course Banner")
        c.drawString(40, 450, f"Original page {i}")
        c.showPage()
    c.save()
    return path


def _page_texts(path):
    with pypdfium2.PdfDocument(str(path)) as doc:
        return [doc[i].get_textpage().get_text_range() for i in range(len(doc))]


def test_pdf_applier_inserts_pages_after_their_anchors_in_a_copy(tmp_path):
    src = _pdf(tmp_path)
    before = src.read_bytes()
    out = tmp_path / "out.pdf"
    applied = apply_edits_pdf(src, out, [_edit(2, "Second added"), _edit(0, "First added"), _edit(2, "Third added")],
                              banner="Course Banner")
    assert src.read_bytes() == before  # the source is never written to
    texts = _page_texts(out)
    assert len(texts) == 7
    order = [next((t for t in ("Original page", "First added", "Second added", "Third added") if t in x), "?") for x in texts]
    assert order == ["Original page", "First added", "Original page", "Original page", "Second added", "Third added", "Original page"]
    # Reported positions point at the pages actually added.
    for a in applied:
        assert a.edit.title in texts[a.new_slide_positions[0]]
    assert "Course Banner" in texts[1] and "Added slide" in texts[1]
    assert find_banner(src) == "Course Banner"


def test_pdf_decks_refuse_bullet_additions(tmp_path):
    assert pdf_room_check(_edit(0, operation=APPEND_BULLETS)) == PDF_ONLY_PROBLEM
    assert pdf_room_check(_edit(0)) is None
    with pytest.raises(ValueError):
        apply_edits_pdf(_pdf(tmp_path), tmp_path / "o.pdf", [_edit(0, operation=APPEND_BULLETS)])
    with pytest.raises(ValueError):
        apply_edits_pdf(_pdf(tmp_path), tmp_path / "o.pdf", [_edit(99)])


@needs_decks
def test_dispatcher_switches_decks_by_cookie_and_query(tmp_path):
    with TestClient(create_server(default_deck=PPTX_DECK.deck_id, state_root=tmp_path)) as client:
        first = client.get("/api/workspace").json()
        assert first["deck_id"] == PPTX_DECK.deck_id and first["format"] == "pptx"
        assert {d["id"] for d in first["decks"]} >= {PPTX_DECK.deck_id, PDF_DECK.deck_id}
        client.cookies.set("deck", PDF_DECK.deck_id)
        second = client.get("/api/workspace").json()
        assert second["deck_id"] == PDF_DECK.deck_id and second["format"] == "pdf"
        assert second["title"] == PDF_DECK.title and second["capabilities"]["append_bullets"] is False
        assert client.get(f"/api/workspace?deck={PPTX_DECK.deck_id}").json()["deck_id"] == PPTX_DECK.deck_id
        unknown = client.get("/api/workspace?deck=no-such-deck")
        assert unknown.status_code == 409 and unknown.json()["decks"]


@needs_decks
def test_pdf_deck_previews_and_refuses_pptx_only_features(tmp_path):
    with TestClient(create_app(state_dir=tmp_path, deck_id=PDF_DECK.deck_id)) as client:
        ws = client.get("/api/workspace").json()
        client.headers["x-workspace-token"] = ws["token"]
        image = client.get("/api/slides/0/image")
        assert image.status_code == 200 and image.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert client.get("/api/layout-issues").json() == []
        assert client.post("/api/runs", json={"kind": "repair", "target_id": 0}).status_code in (422, 503)


def test_resolve_never_guesses_between_decks(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKS_DIR", str(tmp_path))
    monkeypatch.delenv("DECK_ID", raising=False)
    with pytest.raises(FileNotFoundError):
        deck_workspace.resolve()
    a = deck_workspace.register([_pdf(tmp_path)], deck_id="Deck A")
    assert a.deck_id == "deck-a" and deck_workspace.resolve().deck_id == "deck-a"
    deck_workspace.register([_pdf(tmp_path)], deck_id="deck-b")
    with pytest.raises(ValueError):
        deck_workspace.resolve()
    monkeypatch.setenv("DECK_ID", "deck-b")
    assert deck_workspace.resolve().deck_id == "deck-b"
    assert a.capabilities == {"insert_slide": True, "append_bullets": False, "layout_repair": False, "source_previews": True}
    with pytest.raises(FileExistsError):
        deck_workspace.register([_pdf(tmp_path)], deck_id="deck-a")
    manifest = json.loads((a.root / "deck.json").read_text())
    assert manifest["pdf"] and manifest["parse_source"] == manifest["pdf"]
