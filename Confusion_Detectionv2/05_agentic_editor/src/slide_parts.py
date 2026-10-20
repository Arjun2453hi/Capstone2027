"""slide_parts.py — splits a .pptx slide into what a layout repair needs.

The repair planner rewrites the TEXT; the applier moves the GRAPHICS.
Both use this module, so they agree on which shape is which.

Shaped by the deck's six known broken slides (5, 98, 99, 100, 278, 282):
  * a group covering the whole slide can hold the real diagram (282:
    the Kubernetes architecture picture sits inside one), so a group
    that big is opened up and its pieces judged one by one;
  * a picture covering (nearly) the whole slide is a flattened
    screenshot -- on 99 it is a different slide entirely -- and is
    dropped;
  * a flattened animation can contain the same picture twice (282
    again); only the copy closest to the image's true proportions is
    kept, with the small shapes drawn over the dropped copy;
  * pictures were stretched out of proportion by the PDF conversion
    (278's diagram is shown at 0.95:1, the image is 1.39:1); every
    carried picture gets its true proportions back, keeping its centre
    and area;
  * short text sitting on a picture is that picture's label and moves
    with it (unless it is scrambled, then it is dropped); everything
    else is a paragraph and goes to the LLM.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.oxml.ns import qn
from pptx.util import Inches

PROSE_MIN_CHARS = 40  # shorter text next to a graphic is that graphic's label
RULE_MAX_HEIGHT = Inches(0.1)
OPEN_GROUP_SHARE = 0.6  # a group covering more of the slide than this is opened up
BACKDROP_SHARE = 0.9  # a picture covering more of the slide than this is a backdrop
STACKED_LABEL_SHARE = 0.3  # labels overlapping each other this much were printed over each other
_LABEL_MARGIN = Inches(0.3)
_GRAPHIC_TYPES = {
    MSO_SHAPE_TYPE.PICTURE,
    MSO_SHAPE_TYPE.GROUP,
    MSO_SHAPE_TYPE.FREEFORM,
    MSO_SHAPE_TYPE.AUTO_SHAPE,
    MSO_SHAPE_TYPE.TABLE,
    MSO_SHAPE_TYPE.LINE,
}

Rect = Tuple[int, int, int, int]  # left, top, right, bottom, in slide EMU


@dataclass
class Carried:
    element: object  # the shape's XML element, copied as-is by the applier
    rect: Rect  # where it sits on the slide (absolute, even if it came out of a group)
    is_label: bool = False


@dataclass
class SlideParts:
    title: str = ""
    text_blocks: List[str] = field(default_factory=list)  # reading order: top to bottom, then left to right
    graphics: List[Carried] = field(default_factory=list)  # pictures/diagrams and their labels, back to front

    @property
    def text(self) -> str:
        return "\n".join(([self.title] if self.title else []) + self.text_blocks)


def _area(r: Rect) -> float:
    return max(r[2] - r[0], 0) * max(r[3] - r[1], 0)


def _touches(r: Rect, box: Rect) -> bool:
    m = _LABEL_MARGIN
    return r[0] < box[2] + m and box[0] - m < r[2] and r[1] < box[3] + m and box[1] - m < r[3]


def _xfrm_values(xfrm, tag):
    node = xfrm.find(qn(tag))
    if node is None:
        return None
    return tuple(int(node.get(k)) for k in (("x", "y") if tag in ("a:off", "a:chOff") else ("cx", "cy")))


def _child_transform(group):
    """A function mapping a rect in the group's child coordinates to
    slide coordinates."""
    xfrm = group._element.grpSpPr.find(qn("a:xfrm"))
    off, ext = _xfrm_values(xfrm, "a:off"), _xfrm_values(xfrm, "a:ext")
    ch_off, ch_ext = _xfrm_values(xfrm, "a:chOff") or off, _xfrm_values(xfrm, "a:chExt") or ext
    sx = ext[0] / ch_ext[0] if ch_ext[0] else 1.0
    sy = ext[1] / ch_ext[1] if ch_ext[1] else 1.0

    def to_slide(r: Rect) -> Rect:
        return (
            int(off[0] + (r[0] - ch_off[0]) * sx),
            int(off[1] + (r[1] - ch_off[1]) * sy),
            int(off[0] + (r[2] - ch_off[0]) * sx),
            int(off[1] + (r[3] - ch_off[1]) * sy),
        )

    return to_slide


def deck_banner(prs) -> Optional[str]:
    """The course banner: the first line most title placeholders share
    ("CLOUD COMPUTING" in this deck), or None if no line is shared."""
    first_lines = Counter()
    for slide in prs.slides:
        title = slide.shapes.title
        if title is not None and title.has_text_frame:
            lines = [p.text.strip() for p in title.text_frame.paragraphs if p.text.strip()]
            if lines:
                first_lines[lines[0]] += 1
    line, count = first_lines.most_common(1)[0] if first_lines else (None, 0)
    return line if count >= max(3, len(prs.slides) // 4) else None


def title_text(slide, banner: Optional[str] = None) -> str:
    """The slide's own title: its title placeholder's text without the
    course banner line, else ''."""
    title = slide.shapes.title
    if title is None or not title.has_text_frame:
        return ""
    lines = [p.text.strip() for p in title.text_frame.paragraphs if p.text.strip()]
    return " ".join(line for line in lines if line != banner)


def _distortion(shape, rect: Rect) -> float:
    """How far a picture's shown proportions are from the image's own
    (0 = undistorted)."""
    w, h = shape.image.size
    shown = (rect[2] - rect[0]) / max(rect[3] - rect[1], 1)
    return abs(shown / (w / h) - 1) if h else 0.0


def _true_proportions(shape, rect: Rect) -> Rect:
    import math

    w, h = shape.image.size
    if not h or not w or shape._element.find(".//" + qn("a:srcRect")) is not None:
        return rect  # cropped: the shown proportions are intended
    aspect = w / h
    area = _area(rect)
    new_w = math.sqrt(area * aspect)
    new_h = new_w / aspect
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    return (int(cx - new_w / 2), int(cy - new_h / 2), int(cx + new_w / 2), int(cy + new_h / 2))


def _inside(inner: Rect, outer: Rect) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def _is_rule(shape) -> bool:
    return shape.shape_type == MSO_SHAPE_TYPE.FREEFORM and shape.height <= RULE_MAX_HEIGHT and shape.width > Inches(2)


def _has_text(shape) -> bool:
    return shape.has_text_frame and bool(shape.text_frame.text.strip())


def split_slide(slide, slide_width: int, slide_height: int, banner: Optional[str] = None) -> SlideParts:
    slide_area = slide_width * slide_height
    title_shape = slide.shapes.title
    parts = SlideParts(title=title_text(slide, banner))

    graphics: List[Carried] = []
    texts = []  # (rect, text, element)
    picture_seen = {}  # image hash -> (index in graphics, distortion)
    dropped_copies: List[Rect] = []

    def visit(shapes, to_slide):
        for shape in shapes:
            if title_shape is not None and to_slide is None and shape.shape_id == title_shape.shape_id:
                continue
            rect = (shape.left, shape.top, shape.left + shape.width, shape.top + shape.height)
            rect = to_slide(rect) if to_slide else rect
            share = _area(rect) / slide_area
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP and share > OPEN_GROUP_SHARE:
                visit(shape.shapes, _child_transform(shape) if to_slide is None else _compose(to_slide, shape))
                continue
            if _has_text(shape) and shape.shape_type != MSO_SHAPE_TYPE.GROUP:
                texts.append((rect, shape.text_frame.text.strip(), shape._element))
                continue
            if shape.shape_type not in _GRAPHIC_TYPES or _is_rule(shape):
                continue
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                if share > BACKDROP_SHARE:
                    continue
                digest = hashlib.sha1(shape.image.blob).hexdigest()
                distortion = _distortion(shape, rect)
                fixed = _true_proportions(shape, rect)
                if digest in picture_seen:  # the same picture again (a flattened animation)
                    index, kept_distortion = picture_seen[digest]
                    if distortion < kept_distortion:
                        dropped_copies.append(original_rects[index])
                        graphics[index] = Carried(shape._element, fixed)
                        original_rects[index] = rect
                        picture_seen[digest] = (index, distortion)
                    else:
                        dropped_copies.append(rect)
                    continue
                picture_seen[digest] = (len(graphics), distortion)
                original_rects[len(graphics)] = rect
                graphics.append(Carried(shape._element, fixed))
                continue
            graphics.append(Carried(shape._element, rect))

    def _compose(outer, group):
        inner = _child_transform(group)
        return lambda r: outer(inner(r))

    original_rects = {}  # index in graphics -> a picture's rect before its proportions were fixed
    visit(slide.shapes, None)
    pictures = {i for _, (i, _) in picture_seen.items()}

    # Short text whose centre sits on a graphic is that graphic's label;
    # scrambled text (two words printed over each other) is dropped.
    blocks = []
    for rect, text, element in texts:
        on_graphic = any(_contains_centre(g.rect if i not in original_rects else original_rects[i], rect) for i, g in enumerate(graphics))
        if len(text) < PROSE_MIN_CHARS and on_graphic:
            if not _scrambled(text):
                graphics.append(Carried(element, rect, is_label=True))
        else:
            blocks.append((rect[1], rect[0], text))
    parts.text_blocks = [text for _, _, text in sorted(blocks)]

    # Shapes drawn over a dropped duplicate went with it. Shapes drawn
    # over a picture whose proportions were fixed move with the picture:
    # labels keep their size, other shapes are reshaped like it.
    moved = {i: (original_rects[i], graphics[i].rect) for i in pictures if original_rects[i] != graphics[i].rect}
    kept = []
    for i, g in enumerate(graphics):
        if i in pictures:
            kept.append(g)
            continue
        if any(_contains_centre(d, g.rect) for d in dropped_copies):
            continue
        for old, new in moved.values():
            if _contains_centre(old, g.rect):
                g = Carried(g.element, _map_rect(g.rect, old, new, keep_size=g.is_label), g.is_label)
                break
        kept.append(g)
    # Labels printed on top of each other are the breakage itself (the
    # PDF conversion stacked animation steps): neither copy is kept.
    labels = [g for g in kept if g.is_label]
    stacked = {
        id(a)
        for a in labels
        for b in labels
        if a is not b and _overlap_share(a.rect, b.rect) > STACKED_LABEL_SHARE
    }
    parts.graphics = [g for g in kept if id(g) not in stacked]
    return parts


def _overlap_share(a: Rect, b: Rect) -> float:
    inter = _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))
    return inter / (min(_area(a), _area(b)) or 1)


def _contains_centre(outer: Rect, r: Rect) -> bool:
    cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def _scrambled(text: str) -> bool:
    """A run of 16+ letters with no space is two words printed on top
    of each other by the PDF conversion ("Consenovliirdoantmioe")."""
    import re

    return bool(re.search(r"[A-Za-z]{16,}", text))


def _map_rect(r: Rect, old: Rect, new: Rect, keep_size: bool) -> Rect:
    sx = (new[2] - new[0]) / max(old[2] - old[0], 1)
    sy = (new[3] - new[1]) / max(old[3] - old[1], 1)
    if keep_size:
        cx = new[0] + ((r[0] + r[2]) / 2 - old[0]) * sx
        cy = new[1] + ((r[1] + r[3]) / 2 - old[1]) * sy
        w, h = r[2] - r[0], r[3] - r[1]
        return (int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2))
    return (
        int(new[0] + (r[0] - old[0]) * sx),
        int(new[1] + (r[1] - old[1]) * sy),
        int(new[0] + (r[2] - old[0]) * sx),
        int(new[1] + (r[3] - old[1]) * sy),
    )
