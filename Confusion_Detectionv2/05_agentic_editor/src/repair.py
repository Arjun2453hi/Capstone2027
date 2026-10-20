"""repair.py — plans clean rebuilds of slides whose layout is broken.

Only for slides a person has confirmed as broken (layout_check.py only
proposes candidates). One LLM call per slide restructures that slide's
OWN text into a title and bullets; the checker then rejects a rebuild
that adds content or drops too much of the original. The pictures are
not the LLM's business: the applier carries them over unchanged.
"""
from __future__ import annotations

import json
from typing import List, Optional

from pptx.oxml.ns import qn

from .checker import MAX_REPAIR_BULLETS, MAX_TITLE_CHARS, check_edit
from .planner import EditGenerator
from .schema import REPAIR_SLIDE, REPAIR_TOPIC_ID, SlideEdit
from .slide_parts import SlideParts

SYSTEM_PROMPT = """You restore a lecture slide whose layout broke: text boxes
were drawn on top of each other, so its text reached you in pieces and
some words are scrambled together.

Rebuild it as a title and 2 to {max_bullets} bullets, using ONLY what is on
the slide:
- Copy the slide's sentences word for word. You may shorten one only by
  deleting words, never by rephrasing it in your own words.
- Keep every point the slide makes. Merge repeated text (the same
  sentence often appears more than once).
- One bullet per list item: a list of definitions becomes one bullet
  each, never one long bullet. Each bullet at most 40 words.
- Where a word is visibly scrambled (letters of two words interleaved),
  write the intended word only if the rest of the slide makes it clear;
  otherwise drop that fragment.
- Do not add explanations, examples or facts that are not on the slide.
- Leave out URLs, page numbers and labels that belong to a diagram.
- The title is a short heading of at most {max_title} characters, made of
  the slide's own words. If the slide's top line is a whole sentence,
  keep that sentence as the first bullet and make the heading from its
  key words.

Respond with strict JSON only:
{{"title": "<str>", "bullets": ["<str>", ...]}}"""


def label_text(element) -> str:
    """The text of a copied shape element, read from its a:t nodes
    (lxml's itertext() on python-pptx elements repeats runs)."""
    return " ".join(" ".join(t.text or "" for t in element.iter(qn("a:t"))).split())


def build_user_prompt(parts: SlideParts, pdf_text: Optional[str] = None) -> str:
    lines = [f"Title on the slide: {parts.title or '(none)'}", "", "Text boxes on the slide, top to bottom:"]
    lines += [f"[{i}] {block}" for i, block in enumerate(parts.text_blocks, start=1)]
    labels = [label_text(g.element) for g in parts.graphics if g.is_label]
    if labels:
        lines += ["", "Diagram labels (they stay on the diagram; do not turn them into bullets): " + "; ".join(labels)]
    if pdf_text:
        lines += ["", "The same slide's text as extracted from the PDF, for reference:", pdf_text]
    return "\n".join(lines)


def parse_repair(raw: Optional[str], slide_id: int, source_text: str) -> Optional[SlideEdit]:
    if not raw:
        return None
    try:
        item = json.loads(raw.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(item, dict) or not isinstance(item.get("bullets"), list):
        return None
    return SlideEdit(
        topic_id=REPAIR_TOPIC_ID,
        after_slide_id=slide_id,
        title=str(item.get("title") or "").strip(),
        bullets=[str(b).strip() for b in item["bullets"] if str(b).strip()],
        addresses_questions=[],
        operation=REPAIR_SLIDE,
        source_text=source_text,
    )


class RepairPlanner:
    def __init__(self, generator: EditGenerator):
        self.generator = generator

    def plan_repair(self, parts: SlideParts, slide_id: int, pdf_text: Optional[str] = None) -> Optional[SlideEdit]:
        """None when the model's output is unusable (the caller reports it)."""
        source_text = "\n".join(filter(None, [parts.text, pdf_text]))
        system = SYSTEM_PROMPT.format(max_bullets=MAX_REPAIR_BULLETS, max_title=MAX_TITLE_CHARS - 10)
        user = build_user_prompt(parts, pdf_text)
        edit = parse_repair(self.generator.generate(system, user), slide_id, source_text)
        if edit is None:
            return None
        # One retry with the checker's findings, the way a person would
        # send it back: most first-try rejections are a too-long title or
        # a paraphrase, which the model fixes once told.
        problems = check_edit(edit, None, None).problems
        if problems:
            retry = user + "\n\nYour previous rebuild was rejected:\n" + "\n".join(f"- {p}" for p in problems)
            retry += "\n\nYour previous rebuild:\n" + json.dumps({"title": edit.title, "bullets": edit.bullets}, ensure_ascii=False)
            second = parse_repair(self.generator.generate(system, retry), slide_id, source_text)
            if second is not None:
                return second
        return edit


def plan_repairs(planner: RepairPlanner, pptx_path, slide_ids: List[int], deck=None) -> List[SlideEdit]:
    """Plans a rebuild for each confirmed broken slide (0-indexed ids)."""
    from pptx import Presentation

    from .slide_parts import deck_banner, split_slide

    prs = Presentation(str(pptx_path))
    banner = deck_banner(prs)
    edits = []
    for slide_id in slide_ids:
        parts = split_slide(prs.slides[slide_id], prs.slide_width, prs.slide_height, banner)
        pdf_text = None
        if deck is not None and deck.get(slide_id) is not None:
            pdf_text = deck.get(slide_id).raw_text.strip() or None
        edit = planner.plan_repair(parts, slide_id, pdf_text)
        if edit is None:
            print(f"[warn] no usable repair for slide {slide_id + 1}")
            continue
        edits.append(edit)
    return edits
