"""pptx_parser.py -- .pptx -> the same Slide/Deck contract as the PDF parser.

Needed to read back a deck the editor has changed (the editor writes
.pptx), and so a .pptx can be the only input instead of a PDF that has
to match it slide for slide.

Differences from the PDF parser, all deliberate:
* The title is the slide's title placeholder when it has one. That is
  the author's own declaration, so it beats the PDF parser's
  largest-font guess. Without a placeholder, the PDF rule applies: the
  topmost lines at the slide's largest font size.
* Text in tables and grouped shapes is included; pictures are not
  (an image-only slide parses as empty, exactly like the PDF parser).
* Lines repeated across many slides (banners, footers) are removed with
  the PDF parser's own boilerplate rule, before the title is chosen.
* Hidden slides are skipped by default: PowerPoint leaves them out of a
  PDF export and a slideshow, and the editor hides the originals of the
  slides it repairs. ``slide_index_map`` gives each parsed slide's
  position in the .pptx.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple, Union

from .boilerplate import detect_boilerplate_line_keys, normalize_for_repetition
from .parser import BULLET_MARKER_RE
from .schema import Deck, Slide

_SPACE_RE = re.compile(r"[ \t   ]+")


def _clean(text: str) -> str:
    text = _SPACE_RE.sub(" ", text.replace("\x0b", " ")).strip()
    return BULLET_MARKER_RE.sub("", text).strip() if BULLET_MARKER_RE.match(text) else text


def _paragraph_size(paragraph) -> Optional[float]:
    sizes = [r.font.size.pt for r in paragraph.runs if r.font.size is not None]
    if sizes:
        return max(sizes)
    if paragraph.font.size is not None:
        return paragraph.font.size.pt
    return None


def _iter_shapes(shapes):
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(shape.shapes)
        else:
            yield shape


def _is_title_placeholder(shape) -> bool:
    from pptx.enum.shapes import PP_PLACEHOLDER

    if not shape.is_placeholder:
        return False
    try:
        return shape.placeholder_format.type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE, PP_PLACEHOLDER.VERTICAL_TITLE)
    except ValueError:
        return False


def _slide_lines(slide) -> List[dict]:
    """Every text line on the slide in reading order (top, then left).
    A soft line break (Shift+Enter) starts a new line but stays in the
    same paragraph ("para"), so a banner joined to a title by one can
    still be recognised as a banner."""
    lines = []
    shapes = sorted(_iter_shapes(slide.shapes), key=lambda s: ((s.top or 0), (s.left or 0)))
    for order, shape in enumerate(shapes):
        is_title = _is_title_placeholder(shape)
        if shape.has_text_frame:
            for p_index, paragraph in enumerate(shape.text_frame.paragraphs):
                raw = paragraph.text or ""  # soft line breaks come back as a vertical tab; joining runs would drop them
                size = _paragraph_size(paragraph)
                for part in raw.split("\x0b"):
                    text = _clean(part)
                    if text:
                        lines.append({"text": text, "size": size, "is_title": is_title, "order": order,
                                      "para": (order, p_index)})
        elif getattr(shape, "has_table", False) and shape.has_table:
            for r_index, row in enumerate(shape.table.rows):
                cells = [_clean(c.text) for c in row.cells]
                text = " | ".join(c for c in cells if c)
                if text:
                    lines.append({"text": text, "size": None, "is_title": False, "order": order,
                                  "para": (order, f"row{r_index}")})
    return lines


def _strip_boilerplate(lines: List[dict], boilerplate) -> List[dict]:
    """Drops boilerplate lines, and a boilerplate prefix fused onto a real
    line ("CLOUD COMPUTING KVM" -> "KVM") when the two are in one run."""
    kept = []
    for ln in lines:
        key = normalize_for_repetition(ln["text"])
        if key in boilerplate:
            continue
        for b in boilerplate:
            if len(b) >= 4 and key.startswith(b + " "):
                words = len(b.split(" "))
                ln = dict(ln, text=" ".join(ln["text"].split()[words:]))
                break
        if ln["text"]:
            kept.append(ln)
    return kept


def _split_title(lines: List[dict]) -> Tuple[Optional[str], Optional[float], List[str]]:
    """The title is ONE paragraph: the first one in the title placeholder,
    or else the first paragraph at the slide's largest font size, or
    else the first line. Any further paragraphs are bullets -- converted
    decks often put a whole slide's text in one box or one placeholder."""
    if not lines:
        return None, None, []
    placeholder = [ln for ln in lines if ln["is_title"]]
    if placeholder:
        head = placeholder[0]
    else:
        sizes = [ln["size"] for ln in lines if ln["size"] is not None]
        if sizes:
            top = max(sizes)
            head = next(ln for ln in lines if ln["size"] is not None and abs(ln["size"] - top) <= 0.5)
        else:
            head = lines[0]
    title_lines = [ln for ln in lines if ln["para"] == head["para"]] if head["para"] is not None else [head]
    title = " ".join(ln["text"] for ln in title_lines).strip() or None
    bullets = [ln["text"] for ln in lines if all(ln is not t for t in title_lines)]
    return title, head["size"], bullets


def _is_hidden(slide) -> bool:
    return slide._element.get("show") == "0"


def slide_index_map(pptx_path: Union[str, Path], include_hidden: bool = False) -> List[int]:
    """Parsed slide_id -> 0-indexed position in the .pptx."""
    from pptx import Presentation

    prs = Presentation(str(pptx_path))
    return [i for i, s in enumerate(prs.slides) if include_hidden or not _is_hidden(s)]


def extract_deck_from_pptx(pptx_path: Union[str, Path], include_hidden: bool = False) -> Deck:
    from pptx import Presentation

    prs = Presentation(str(pptx_path))
    slides = [s for s in prs.slides if include_hidden or not _is_hidden(s)]
    all_lines = [_slide_lines(s) for s in slides]
    boilerplate = detect_boilerplate_line_keys(all_lines, len(slides))
    out = []
    for slide_id, lines in enumerate(all_lines):
        kept = _strip_boilerplate(lines, boilerplate)
        title, title_size, bullets = _split_title(kept)
        out.append(Slide(slide_id=slide_id, slide_number=slide_id + 1, title=title,
                         title_font_size=title_size, bullets=bullets))
    return Deck(source_pdf=str(pptx_path), num_pages=len(out), slides=out)


def extract_any(path: Union[str, Path], include_hidden: bool = False) -> Deck:
    """.pptx or .pdf, by extension."""
    path = Path(path)
    if path.suffix.lower() == ".pptx":
        return extract_deck_from_pptx(path, include_hidden=include_hidden)
    if path.suffix.lower() == ".pdf":
        from .parser import extract_deck

        return extract_deck(path)
    raise ValueError(f"Unsupported deck format {path.suffix!r}: use .pptx or .pdf (convert .ppt to .pptx first)")
