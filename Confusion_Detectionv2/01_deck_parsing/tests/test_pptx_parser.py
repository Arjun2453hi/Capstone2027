""".pptx parser: same contract as the PDF parser, built on small decks
generated here that reproduce what the real deck does."""
from __future__ import annotations

from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Inches, Pt

from ..src.pptx_parser import extract_any, extract_deck_from_pptx, slide_index_map

BANNER = "CLOUD COMPUTING"
REAL_PPTX = Path(__file__).resolve().parents[2] / "data" / "cc-unit2-slides.pptx"


def _title_slide(prs, title_paragraphs, body=None):
    slide = prs.slides.add_slide(prs.slide_layouts[1])  # Title and Content
    tf = slide.shapes.title.text_frame
    tf.text = title_paragraphs[0]
    for extra in title_paragraphs[1:]:
        tf.add_paragraph().text = extra
    if body:
        body_tf = slide.placeholders[1].text_frame
        body_tf.text = body[0]
        for b in body[1:]:
            body_tf.add_paragraph().text = b
    return slide


def _box_slide(prs, paragraphs, sizes):
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
    tf = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(9), Inches(6)).text_frame
    for i, (text, size) in enumerate(zip(paragraphs, sizes)):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = p.add_run()
        run.text, run.font.size = text, Pt(size)
    return slide


@pytest.fixture
def deck_path(tmp_path):
    prs = Presentation()
    names = ["Hypervisors", "Containers", "Migration", "Namespaces"]
    for name in names:  # the banner repeats on every slide -> boilerplate
        _title_slide(prs, [BANNER, name], [f"{name} first point", f"{name} second point"])
    _title_slide(prs, [f"{BANNER}\x0bSoft break title"], ["body"])           # 4: banner joined by soft break
    _title_slide(prs, [f"{BANNER} Fused title"], ["body"])                   # 5: banner fused into the same run
    _box_slide(prs, [BANNER, "Box title", "Box body one", "Box body two"], [18, 28, 28, 28])  # 6: no placeholder
    table_slide = _title_slide(prs, [BANNER, "Table slide"])                 # 7: a table
    rows = table_slide.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(6), Inches(2)).table
    rows.cell(0, 0).text, rows.cell(0, 1).text = "Type 1", "bare metal"
    rows.cell(1, 0).text, rows.cell(1, 1).text = "Type 2", "hosted"
    prs.slides.add_slide(prs.slide_layouts[6])                               # 8: empty (image-only)
    hidden = _title_slide(prs, [BANNER, "Hidden original"], ["old text"])    # 9: hidden
    hidden._element.set("show", "0")
    _title_slide(prs, [BANNER, "Last"], ["end"])                             # 10
    path = tmp_path / "deck.pptx"
    prs.save(path)
    return path


def test_banner_removed_and_title_is_one_paragraph(deck_path):
    deck = extract_deck_from_pptx(deck_path)
    assert deck.slides[0].title == "Hypervisors"
    assert deck.slides[0].bullets == ["Hypervisors first point", "Hypervisors second point"]
    assert all(BANNER not in (s.raw_text or "") for s in deck.slides)


def test_banner_joined_by_soft_break_or_fused_is_removed(deck_path):
    deck = extract_deck_from_pptx(deck_path)
    assert deck.slides[4].title == "Soft break title"
    assert deck.slides[5].title == "Fused title"


def test_slide_without_placeholder_takes_first_largest_paragraph(deck_path):
    s = extract_deck_from_pptx(deck_path).slides[6]
    assert s.title == "Box title" and s.title_font_size == 28
    assert s.bullets == ["Box body one", "Box body two"]


def test_tables_are_read_and_empty_slides_are_empty(deck_path):
    deck = extract_deck_from_pptx(deck_path)
    assert deck.slides[7].bullets == ["Type 1 | bare metal", "Type 2 | hosted"]
    assert deck.slides[8].is_empty()


def test_hidden_slides_skipped_by_default(deck_path):
    shown = extract_deck_from_pptx(deck_path)
    assert [s.title for s in shown.slides][-1] == "Last" and len(shown.slides) == 10
    assert all(s.title != "Hidden original" for s in shown.slides)
    assert slide_index_map(deck_path)[-1] == 10  # parsed slide 9 is pptx slide 10
    everything = extract_deck_from_pptx(deck_path, include_hidden=True)
    assert len(everything.slides) == 11 and everything.slides[9].title == "Hidden original"
    assert [s.slide_id for s in shown.slides] == list(range(10))


def test_extract_any_dispatches_by_extension(deck_path, tmp_path):
    assert len(extract_any(deck_path).slides) == 10
    with pytest.raises(ValueError):
        extract_any(tmp_path / "deck.ppt")


@pytest.mark.skipif(not REAL_PPTX.exists(), reason="real deck not present")
def test_real_deck_agrees_with_the_pdf_parse():
    """Numeric check against the committed PDF parse of the same deck."""
    import re

    from ..src.storage import load_deck_json

    pdf = load_deck_json(Path(__file__).resolve().parents[1] / "parsed_deck.json")
    pptx = extract_deck_from_pptx(REAL_PPTX)
    assert len(pptx.slides) == len(pdf.slides) == 288

    def tokens(s):
        return set(re.findall(r"[a-z0-9]+", (s or "").lower()))

    overlaps = []
    for a, b in zip(pdf.slides, pptx.slides):
        ta, tb = tokens(a.raw_text), tokens(b.raw_text)
        overlaps.append(1.0 if not ta and not tb else len(ta & tb) / len(ta | tb))
    mean = sum(overlaps) / len(overlaps)
    print(f"\nmean per-slide token overlap with the PDF parse: {mean:.3f}; min {min(overlaps):.3f}")
    assert mean > 0.95 and min(overlaps) > 0.5
    assert sum(s.is_empty() for s in pptx.slides) == sum(s.is_empty() for s in pdf.slides)
    assert not [s.slide_id for s in pptx.slides if s.title and s.title.upper().startswith("CLOUD COMPUTING")]
