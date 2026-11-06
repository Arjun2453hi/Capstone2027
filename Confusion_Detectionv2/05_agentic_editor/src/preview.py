"""preview.py — a picture of a .pptx slide, drawn from its shapes.

For reviewing a changed slide in the demo without PowerPoint. Draws what
decides how a slide reads: the master and layout graphics (this deck's
PES logo lives in the master), pictures at their real size, text in its
real font, size, colour and weight, wrapped by measured width and
respecting paragraph margins and manual line breaks, bullets in their
own character and colour, and lines and shapes in their own colour.
It is close to PowerPoint, not identical: open the deck for the exact
look.
"""
from __future__ import annotations

import io
from typing import Callable, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
from pptx.enum.dml import MSO_COLOR_TYPE
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.oxml.ns import qn

Rect = Tuple[float, float, float, float]
_FONT_DIRS = (
    "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts",  # Calibri ships with Office on macOS
    "/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "C:/Windows/Fonts",
    "/usr/share/fonts/truetype/dejavu",
)
_FONT_FILES = {
    False: ("Calibri.ttf", "calibri.ttf", "Arial.ttf", "arial.ttf", "DejaVuSans.ttf"),
    True: ("Calibrib.ttf", "calibrib.ttf", "Arial Bold.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"),
}
_TEXT = (29, 33, 36)
_OUTLINE = (184, 194, 204)
_EMU_PER_PT = 12700
_font_cache: dict = {}


def _font(px: int, bold: bool = False):
    key = (max(px, 6), bold)
    if key in _font_cache:
        return _font_cache[key]
    font = None
    for directory in _FONT_DIRS:
        for name in _FONT_FILES[bold]:
            try:
                font = ImageFont.truetype(f"{directory}/{name}", key[0])
                break
            except OSError:
                continue
        if font is not None:
            break
    if font is None:
        try:
            font = ImageFont.load_default(size=key[0])
        except TypeError:  # Pillow < 10.1
            font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def _rgb(colour_format) -> Optional[Tuple[int, int, int]]:
    try:
        if colour_format is not None and colour_format.type == MSO_COLOR_TYPE.RGB:
            value = colour_format.rgb
            return (value[0], value[1], value[2])
    except (AttributeError, TypeError, ValueError):
        pass
    return None


def _srgb(node) -> Optional[Tuple[int, int, int]]:
    """Colour of an <a:buClr>, <a:solidFill>, ... node holding <a:srgbClr>."""
    if node is None:
        return None
    srgb = node.find(qn("a:srgbClr"))
    if srgb is None or not srgb.get("val"):
        return None
    v = srgb.get("val")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


def _group_transform(group, outer: Callable[[Rect], Rect]) -> Callable[[Rect], Rect]:
    xfrm = group._element.grpSpPr.find(qn("a:xfrm"))

    def pair(tag, keys):
        node = xfrm.find(qn(tag))
        return tuple(int(node.get(k)) for k in keys) if node is not None else None

    off, ext = pair("a:off", ("x", "y")), pair("a:ext", ("cx", "cy"))
    ch_off, ch_ext = pair("a:chOff", ("x", "y")) or off, pair("a:chExt", ("cx", "cy")) or ext
    sx, sy = ext[0] / max(ch_ext[0], 1), ext[1] / max(ch_ext[1], 1)
    return lambda r: outer((
        off[0] + (r[0] - ch_off[0]) * sx,
        off[1] + (r[1] - ch_off[1]) * sy,
        off[0] + (r[2] - ch_off[0]) * sx,
        off[1] + (r[3] - ch_off[1]) * sy,
    ))


def _wrap(text: str, font, width_px: float):
    lines = []
    for piece in text.split("\v"):  # "\v": a manual line break inside the paragraph
        line = ""
        for word in piece.split():
            candidate = f"{line} {word}".strip()
            if line and font.getlength(candidate) > width_px:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return lines or [""]


def _draw_path(draw, geometry, b, fill, line_colour) -> None:
    """A freeform along its own outline (an arrow, a bracket), not a
    filled box: each path's points are scaled from its own coordinate
    space into the shape's box; curves are drawn through their end
    points."""
    path_list = geometry.find(qn("a:pathLst"))
    if path_list is None:
        return
    for path in path_list.findall(qn("a:path")):
        w, h = int(path.get("w", 0)) or 1, int(path.get("h", 0)) or 1
        sx, sy = (b[2] - b[0]) / w, (b[3] - b[1]) / h
        points, closed = [], False
        for command in path:
            tag = command.tag.split("}")[1]
            if tag == "close":
                closed = True
                continue
            pts = command.findall(qn("a:pt"))
            if pts:
                pt = pts[-1]
                points.append((b[0] + int(pt.get("x")) * sx, b[1] + int(pt.get("y")) * sy))
        if len(points) < 2:
            continue
        if closed and fill and path.get("fill") != "none":
            draw.polygon(points, fill=fill, outline=line_colour)
        else:
            draw.line(points + ([points[0]] if closed else []), fill=line_colour or fill or _OUTLINE, width=2)


def render_slide(slide, slide_width: int, slide_height: int, width_px: int = 1000) -> Image.Image:
    scale = width_px / slide_width
    image = Image.new("RGB", (width_px, int(slide_height * scale)), "white")
    draw = ImageDraw.Draw(image)
    title_id = slide.shapes.title.shape_id if slide.shapes.title is not None else None

    def px(r: Rect):
        return [int(v * scale) for v in r]

    def draw_text(shape, b, is_title: bool) -> None:
        body_pr = shape.text_frame._txBody.find(qn("a:bodyPr"))

        def inset(key, default):
            return int(body_pr.get(key, default)) * scale if body_pr is not None else default * scale

        left_in, top_in, right_in = inset("lIns", 91440), inset("tIns", 45720), inset("rIns", 91440)
        y = b[1] + top_in
        for index, paragraph in enumerate(shape.text_frame.paragraphs):
            p_pr = paragraph._p.find(qn("a:pPr"))

            def attr(key):
                return int(p_pr.get(key, 0)) * scale if p_pr is not None else 0

            mar_l, mar_r, indent = attr("marL"), attr("marR"), attr("indent")
            run = next((r for r in paragraph.runs if r.text.strip()), paragraph.runs[0] if paragraph.runs else None)
            if run is not None and run.font.size:
                size = run.font.size.pt
            else:
                size = (20 if index == 0 else 28) if is_title else 18  # this deck's banner / title / body defaults
            bold = bool(run.font.bold) if run is not None and run.font.bold is not None else is_title
            colour = (_rgb(run.font.color) if run is not None else None) or _TEXT
            font = _font(int(size * _EMU_PER_PT * scale), bold)
            line_height = size * 1.2 * _EMU_PER_PT * scale
            before = p_pr.find(qn("a:spcBef")) if p_pr is not None else None
            if before is not None and before.find(qn("a:spcPts")) is not None:
                y += int(before.find(qn("a:spcPts")).get("val")) / 100 * _EMU_PER_PT * scale
            if not paragraph.text.strip():
                y += line_height
                continue
            bullet = p_pr.find(qn("a:buChar")) if p_pr is not None else None
            if bullet is not None:
                bullet_colour = _srgb(p_pr.find(qn("a:buClr"))) or colour
                bx = b[0] + left_in + mar_l + indent
                bullet_font = p_pr.find(qn("a:buFont"))
                wingdings = bullet_font is not None and (bullet_font.get("typeface") or "").startswith("Wingdings")
                if wingdings or bullet.get("char") in ("▪", "■", "§"):  # square bullets are drawn: the glyph is often missing
                    side = size * 0.32 * _EMU_PER_PT * scale
                    cy = y + line_height * 0.5
                    draw.rectangle([bx, cy - side / 2, bx + side, cy + side / 2], fill=bullet_colour)
                else:
                    draw.text((bx, y), bullet.get("char"), fill=bullet_colour, font=font)
            x = b[0] + left_in + mar_l
            width = (b[2] - b[0]) - left_in - right_in - mar_l - mar_r
            for line in _wrap(paragraph.text, font, max(width, 20)):
                draw.text((x, y), line, fill=colour, font=font)
                y += line_height

    def visit(shapes, to_slide: Callable[[Rect], Rect], from_template: bool) -> None:
        for shape in shapes:
            if from_template and shape.is_placeholder:
                continue  # the slide's own placeholders carry the text
            left, top = shape.left or 0, shape.top or 0
            rect = to_slide((left, top, left + (shape.width or 0), top + (shape.height or 0)))
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                visit(shape.shapes, _group_transform(shape, to_slide), from_template)
                continue
            b = px(rect)
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                if b[2] - b[0] < 1 or b[3] - b[1] < 1:
                    continue
                try:
                    picture = Image.open(io.BytesIO(shape.image.blob)).convert("RGBA").resize((b[2] - b[0], b[3] - b[1]))
                    image.paste(picture, (b[0], b[1]), picture)
                except Exception:  # an image format PIL cannot read (e.g. EMF)
                    draw.rectangle(b, outline=_OUTLINE)
                continue
            if shape.has_text_frame and shape.text_frame.text.strip():
                draw_text(shape, b, shape.shape_id == title_id and not from_template)
                continue
            sp_pr = shape._element.find(qn("p:spPr"))
            if sp_pr is None:
                continue
            fill = _srgb(sp_pr.find(qn("a:solidFill")))
            line = sp_pr.find(qn("a:ln"))
            line_colour = _srgb(line.find(qn("a:solidFill"))) if line is not None else None
            geometry = sp_pr.find(qn("a:custGeom"))
            if geometry is not None and b[3] - b[1] > 4 and b[2] - b[0] > 4:
                _draw_path(draw, geometry, b, fill, line_colour)
                continue
            if b[3] - b[1] <= 4 or b[2] - b[0] <= 4:  # a rule or a thin line
                draw.rectangle([b[0], b[1], max(b[2], b[0] + 1), max(b[3], b[1] + 2)], fill=fill or line_colour or _OUTLINE)
            elif fill:
                draw.rectangle(b, fill=fill, outline=line_colour)
            elif line_colour:
                draw.rectangle(b, outline=line_colour)

    layout = slide.slide_layout
    visit(layout.slide_master.shapes, lambda r: r, from_template=True)  # e.g. the PES logo
    visit(layout.shapes, lambda r: r, from_template=True)
    visit(slide.shapes, lambda r: r, from_template=False)
    return image
