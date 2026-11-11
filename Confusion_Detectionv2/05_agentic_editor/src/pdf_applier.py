"""pdf_applier.py -- apply edits to a PDF-only deck.

A PDF has no editable slide objects, so only one operation is possible:
INSERT_SLIDE_AFTER. Each new slide is drawn as a page the same size as
the deck's pages (reportlab) and inserted after its anchor page in a
COPY of the PDF (pypdfium2). The source file is never changed.

The page follows the deck where it can be measured from the PDF itself:
the banner text repeated on every page (e.g. the course name) is drawn
at the top as on the source pages, and the title and body sizes are
scaled from the page size. Fonts and colours of the original are not
reproduced; the new page is plainly styled so a reader can tell it was
added (a small footer says so).

APPEND_BULLETS and REPAIR_SLIDE need the .pptx: they are refused here,
and the checker refuses them earlier for PDF decks (see pdf_room_check).
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import List, Optional, Union

from .schema import APPEND_BULLETS, INSERT_SLIDE_AFTER, AppliedEdit, SlideEdit

FOOTER = "Added slide: written to answer student questions"
PDF_ONLY_PROBLEM = ("this deck is a PDF: bullets cannot be added to an existing slide; "
                    "propose a new slide (insert_slide_after) instead")


def pdf_room_check(edit: SlideEdit) -> Optional[str]:
    """Same contract as applier.RoomChecker: a problem string or None."""
    if edit.operation != INSERT_SLIDE_AFTER:
        return PDF_ONLY_PROBLEM
    return None


def _page_pdf(title: str, bullets: List[str], width: float, height: float, banner: Optional[str]) -> bytes:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    margin = width * 0.06
    y = height - height * 0.07
    if banner:
        c.setFont("Helvetica", max(9, height * 0.022))
        c.setFillColor(HexColor("#555555"))
        c.drawString(margin, y, banner)
        y -= height * 0.05
    title_size = max(16, height * 0.06)
    c.setFillColor(HexColor("#111111"))
    c.setFont("Helvetica-Bold", title_size)
    for line in simpleSplit(title, "Helvetica-Bold", title_size, width - 2 * margin)[:2]:
        c.drawString(margin, y, line)
        y -= title_size * 1.2
    y -= height * 0.02
    c.setStrokeColor(HexColor("#999999"))
    c.line(margin, y + title_size * 0.4, width - margin, y + title_size * 0.4)

    bottom = height * 0.08
    body = max(10.0, height * 0.038)
    indent = body * 1.2
    while body > 9:  # shrink until every bullet fits, like the .pptx applier's font fitting
        lines = [simpleSplit(b, "Helvetica", body, width - 2 * margin - indent) for b in bullets]
        needed = sum(len(l) for l in lines) * body * 1.25 + len(bullets) * body * 0.5
        if y - needed >= bottom:
            break
        body -= 1
    c.setFont("Helvetica", body)
    for wrapped in lines:
        if not wrapped:
            continue
        c.drawString(margin, y - body, "•")
        for line in wrapped:
            y -= body * 1.25
            c.drawString(margin + indent, y, line)
        y -= body * 0.5
    c.setFont("Helvetica-Oblique", max(7, height * 0.016))
    c.setFillColor(HexColor("#777777"))
    c.drawString(margin, height * 0.03, FOOTER)
    c.showPage()
    c.save()
    return buf.getvalue()


def render_edit_page(source_pdf: Union[str, Path], edit: SlideEdit, banner: Optional[str] = None) -> bytes:
    """One new page as PDF bytes, sized like the anchor page."""
    import pypdfium2

    with pypdfium2.PdfDocument(str(source_pdf)) as pdf:
        anchor = min(max(edit.after_slide_id, 0), len(pdf) - 1)
        width, height = pdf[anchor].get_size()
    return _page_pdf(edit.title, list(edit.bullets), width, height, banner)


def apply_edits_pdf(source_pdf: Union[str, Path], output_pdf: Union[str, Path], edits: List[SlideEdit],
                    deck=None, banner: Optional[str] = None) -> List[AppliedEdit]:
    import pypdfium2

    for e in edits:
        if e.operation != INSERT_SLIDE_AFTER:
            raise ValueError(f"{e.operation}: {PDF_ONLY_PROBLEM}")
    out = pypdfium2.PdfDocument(str(source_pdf))
    n = len(out)
    for e in edits:
        if not 0 <= e.after_slide_id < n:
            raise ValueError(f"after_slide_id {e.after_slide_id} is outside the deck (0..{n - 1})")
    # Insert from the back so earlier anchors keep their page numbers; edits on
    # the same anchor keep their given order.
    order = sorted(range(len(edits)), key=lambda i: (edits[i].after_slide_id, i), reverse=True)
    for i in order:
        e = edits[i]
        page = pypdfium2.PdfDocument(render_edit_page(source_pdf, e, banner))
        out.import_pages(page, index=e.after_slide_id + 1)
        page.close()
    Path(output_pdf).parent.mkdir(parents=True, exist_ok=True)
    out.save(str(output_pdf))
    out.close()
    # Final positions: an edit lands after its anchor, shifted by every
    # insertion at an earlier anchor (or the same anchor, earlier in the list).
    applied = []
    for i, e in enumerate(edits):
        before = sum(1 for j, o in enumerate(edits) if o.after_slide_id < e.after_slide_id
                     or (o.after_slide_id == e.after_slide_id and j < i))
        applied.append(AppliedEdit(edit=e, new_slide_positions=[e.after_slide_id + 1 + before],
                                   mirrored_after_slide_ids=[]))
    return applied


def find_banner(pdf_path: Union[str, Path], min_fraction: float = 0.5) -> Optional[str]:
    """The first text line shared by most pages (a course banner), if any."""
    import pdfplumber

    counts, n = {}, 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages[:60]:
            n += 1
            lines = [l.strip() for l in (page.extract_text() or "").splitlines() if l.strip()]
            if lines:
                counts[lines[0]] = counts.get(lines[0], 0) + 1
    if not counts:
        return None
    line, count = max(counts.items(), key=lambda kv: kv[1])
    return line if n and count / n >= min_fraction and len(line) <= 60 else None
