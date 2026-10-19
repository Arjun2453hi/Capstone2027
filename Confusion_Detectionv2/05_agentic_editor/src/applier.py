"""applier.py — applies accepted SlideEdits to a copy of the deck.

The source .pptx is never written to. Each new slide borrows its header
(the course banner + title placeholder and the rule under it) from the
nearest existing slide that has one, so it matches the deck's template
without that template being hardcoded here.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import List, Optional, Union

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from .checker import duplicate_anchor_ids
from .schema import AppliedEdit, SlideEdit

ADDED_SLIDE_NAME = "gap_editor_added"
BODY_SHAPE_NAME = "gap_editor_body"
BODY_FONT, BODY_FONT_PT = "Calibri", 22
# Body box position/size of a typical content slide in a 13.33in-wide
# deck, expressed as fractions so other slide sizes scale with it.
_BODY_LEFT, _BODY_TOP, _BODY_WIDTH, _BODY_HEIGHT = 0.036, 0.216, 0.92, 0.70
_RULE_MAX_HEIGHT = Inches(0.1)


def _header_shapes(slide):
    """(title placeholder, rule) of a slide that follows the deck's
    content template -- a banner line plus a title line, with a rule
    under them -- else None. A slide with a title placeholder but not
    this header (the real deck has some: a one-line title holding a
    whole sentence, no banner, no rule) is not a template to copy."""
    title = slide.shapes.title
    if title is None or not title.has_text_frame:
        return None
    if len([p for p in title.text_frame.paragraphs if p.text.strip()]) < 2:
        return None
    rule = next(
        (s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.FREEFORM and s.height <= _RULE_MAX_HEIGHT and s.top < title.top + title.height + Inches(1)),
        None,
    )
    return (title, rule) if rule is not None else None


def _nearest_template_slide(prs, position: int):
    slides = prs.slides
    for distance in range(len(slides)):
        for candidate in (position - distance, position + distance):
            if 0 <= candidate < len(slides) and _header_shapes(slides[candidate]) is not None:
                return slides[candidate]
    return None


def _set_title(title_shape, text: str) -> None:
    """The deck's title placeholder holds the course banner on its
    first line and the slide title on its last; only the last non-empty
    paragraph is rewritten, keeping its first run's formatting."""
    paragraphs = [p for p in title_shape.text_frame.paragraphs if p.text.strip()]
    target = paragraphs[-1]
    target.runs[0].text = text
    for run in target.runs[1:]:
        run._r.getparent().remove(run._r)


def _add_bullet(paragraph, text: str) -> None:
    run = paragraph.add_run()
    run.text = text
    run.font.name, run.font.size = BODY_FONT, Pt(BODY_FONT_PT)
    paragraph.space_after = Pt(10)
    pPr = paragraph._p.get_or_add_pPr()
    pPr.set("marL", str(Inches(0.35)))
    pPr.set("indent", str(-Inches(0.35)))
    bu_font = pPr.makeelement(qn("a:buFont"), {"typeface": "Arial"})
    bu_char = pPr.makeelement(qn("a:buChar"), {"char": "•"})
    pPr.append(bu_font)
    pPr.append(bu_char)


def _build_slide(prs, template_slide, edit: SlideEdit):
    """Appends the new slide at the end of the deck and returns it."""
    layout = template_slide.slide_layout if template_slide is not None else prs.slide_layouts[0]
    slide = prs.slides.add_slide(layout)
    for shape in list(slide.shapes):  # the layout's empty placeholders
        shape._element.getparent().remove(shape._element)

    header = _header_shapes(template_slide) if template_slide is not None else None
    if header is not None:
        for source in header:
            slide.shapes._spTree.append(copy.deepcopy(source._element))
        _set_title(slide.shapes.title, edit.title)
    else:
        box = slide.shapes.add_textbox(Inches(0.3), Inches(0.3), prs.slide_width - Inches(0.6), Inches(0.9))
        run = box.text_frame.paragraphs[0].add_run()
        run.text = edit.title
        run.font.size, run.font.bold = Pt(24), True

    width, height = prs.slide_width, prs.slide_height
    body = slide.shapes.add_textbox(int(width * _BODY_LEFT), int(height * _BODY_TOP), int(width * _BODY_WIDTH), int(height * _BODY_HEIGHT))
    body.name = BODY_SHAPE_NAME
    frame = body.text_frame
    frame.word_wrap = True
    for i, bullet in enumerate(edit.bullets):
        _add_bullet(frame.paragraphs[0] if i == 0 else frame.add_paragraph(), bullet)

    slide._element.find(qn("p:cSld")).set("name", ADDED_SLIDE_NAME)
    answered = "; ".join(edit.addresses_questions)
    slide.notes_slide.notes_text_frame.text = (
        f"Added by the gap editor for topic {edit.topic_id} ({edit.gap_type}). Student questions answered: {answered}"
    )
    return slide


def _move_last_slide_to(prs, position: int) -> None:
    slide_ids = prs.slides._sldIdLst
    element = slide_ids[-1]
    slide_ids.remove(element)
    slide_ids.insert(position, element)


def apply_edits(source_pptx: Union[str, Path], output_pptx: Union[str, Path], edits: List[SlideEdit], deck) -> List[AppliedEdit]:
    """Writes output_pptx = source_pptx + one new slide per edit (and per
    verbatim repeat of its anchor slide). `deck` is Stage 1's parse of
    the same deck: its slide_ids are what after_slide_id refers to, and
    its row order is the slide order in the file."""
    source_pptx, output_pptx = Path(source_pptx), Path(output_pptx)
    if source_pptx.resolve() == output_pptx.resolve():
        raise ValueError("output_pptx must differ from source_pptx: the source deck is never modified")

    prs = Presentation(str(source_pptx))
    if len(prs.slides) != len(deck.slides):
        raise ValueError(f"{source_pptx.name} has {len(prs.slides)} slides but the parsed deck has {len(deck.slides)}: slide ids would not line up")
    position_of = {s.slide_id: i for i, s in enumerate(deck.slides)}

    # (anchor position, plan order, edit index): one per copy of each anchor.
    insertions = []
    mirrored = {}
    for index, edit in enumerate(edits):
        mirrored[index] = duplicate_anchor_ids(deck, edit.after_slide_id)
        for anchor_id in [edit.after_slide_id] + mirrored[index]:
            insertions.append((position_of[anchor_id], index, index))
    insertions.sort()

    # Back to front, so positions not yet processed are still the
    # original ones.
    for anchor_position, _, index in reversed(insertions):
        _build_slide(prs, _nearest_template_slide(prs, anchor_position), edits[index])
        _move_last_slide_to(prs, anchor_position + 1)

    # Every insertion before this one in file order shifts it by one.
    positions = {index: [] for index in range(len(edits))}
    for rank, (anchor_position, _, index) in enumerate(insertions):
        positions[index].append(anchor_position + 1 + rank)

    output_pptx.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_pptx))
    return [
        AppliedEdit(edit=edit, new_slide_positions=positions[index], mirrored_after_slide_ids=mirrored[index])
        for index, edit in enumerate(edits)
    ]


def added_slide_positions(pptx_path: Union[str, Path]) -> List[int]:
    """0-indexed positions of the slides this module added to a deck."""
    prs = Presentation(str(pptx_path))
    return [i for i, s in enumerate(prs.slides) if s._element.find(qn("p:cSld")).get("name") == ADDED_SLIDE_NAME]
