"""
extract_deck.py — Step 1: parse a slide-deck PDF into structured JSON.

Usage:
    python extract_deck.py

    Or from the command line with an explicit path:
    python extract_deck.py path/to/deck.pdf [path/to/output.json]

Output JSON shape (one entry per slide):
    {
      "source_pdf": "...",
      "extracted_at": "...",
      "num_pages": N,
      "slides": [
        {
          "slide_id": 0,          # 0-indexed, permanent identity used downstream
          "slide_number": 1,      # 1-indexed, human-readable
          "title": "...",         # None if no title detected
          "bullets": ["...", "..."],
          "notes": null,          # always null for PDF source
          "raw_text": "...",      # title + bullets, reconstructed
          "char_count": 123,
          "module_id": null       # filled in by a later grouping step
        },
        ...
      ]
    }

How title/bullet splitting works:
    Plain text extraction throws away font size, so a title and a bullet
    look identical. This script instead reads word-level position + font
    size data and treats the largest-font line(s) at the top of each page
    as the title; everything below that is a bullet (with leading marker
    characters like "•" or "-" stripped).

Boilerplate stripping (two-pass) — see DocumentParsing/claude.md:
    Real slide exports almost always carry a repeated course/unit banner
    and a page-number footer on every page. When that banner renders in
    a bigger font than the slide's actual heading (common with
    university deck templates), the "largest font at the top" rule above
    would pick the banner as the title instead of the real heading, and
    the real title falls through and gets misclassified as a bullet.
    extract_deck() runs two passes to avoid this: pass 1 collects every
    page's lines with no classification yet; a line that recurs (after
    digit-normalizing, so page numbers don't dodge detection) on enough
    distinct pages is flagged as boilerplate and stripped from every
    page's lines; pass 2 runs title/bullet detection on what's left.
    Order matters — stripping has to happen before classification, or
    the banner has already won the title slot by the time anyone tries
    to remove it.
"""
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional

import pdfplumber


# ─────────────────────────────────────────────
# INPUT / OUTPUT PATHS  ← change these to your files
# ─────────────────────────────────────────────
PDF_PATH    = r"se-u2-slides.pdf"
OUTPUT_PATH = r"gap_detection/data/decks/se-u2-slides.json"


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
LINE_TOLERANCE = 3.0          # px: words within this vertical distance = "same line"
TITLE_SIZE_TOLERANCE = 0.5    # px: font-size slack when matching the page's max size
BULLET_MARKER_RE = re.compile(
    r"^(?:[\u2022\u25CF\u2023\u2043\-\*]|\(cid:\d+\))\s*"
)

# Boilerplate detection (see module docstring). Both a fraction *and* an
# absolute floor are required: fraction alone breaks on small decks (2
# pages out of 5 looks like 40%, but that's not enough evidence a line
# is structural repetition rather than coincidence); an absolute floor
# alone breaks on huge decks (3 occurrences out of 500 pages is noise).
BOILERPLATE_MIN_FRACTION = 0.30
BOILERPLATE_MIN_ABSOLUTE = 3
_DIGIT_RE = re.compile(r"\d+")


# ─────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────
@dataclass
class Slide:
    slide_id: int
    slide_number: int
    title: Optional[str]
    bullets: List[str] = field(default_factory=list)
    notes: Optional[str] = None
    raw_text: str = ""
    char_count: int = 0
    module_id: Optional[int] = None

    def __post_init__(self):
        if not self.raw_text:
            parts = ([self.title] if self.title else []) + list(self.bullets)
            self.raw_text = "\n".join(parts)
        if not self.char_count:
            self.char_count = len(self.raw_text)


@dataclass
class DeckDocument:
    source_pdf: str
    num_pages: int
    slides: List[Slide]
    extracted_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "source_pdf": self.source_pdf,
            "extracted_at": self.extracted_at,
            "num_pages": self.num_pages,
            "slides": [asdict(s) for s in self.slides],
        }


# ─────────────────────────────────────────────
# PARSING LOGIC
# ─────────────────────────────────────────────
def _group_words_into_lines(words):
    """Group word dicts (with 'top', 'size', 'text', 'x0') into lines,
    top-to-bottom. Each line gets 'text' and 'size' (median word size)."""
    if not words:
        return []

    words_sorted = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines, current = [], [words_sorted[0]]

    for w in words_sorted[1:]:
        if abs(w["top"] - current[-1]["top"]) <= LINE_TOLERANCE:
            current.append(w)
        else:
            lines.append(current)
            current = [w]
    lines.append(current)

    result = []
    for line_words in lines:
        line_words.sort(key=lambda w: w["x0"])
        sizes = sorted(w["size"] for w in line_words)
        median_size = sizes[len(sizes) // 2]
        text = " ".join(w["text"] for w in line_words)
        result.append({"text": text, "size": median_size, "top": line_words[0]["top"]})
    return result


def _normalize_for_boilerplate(text: str) -> str:
    """Collapse digit runs so a page-number footer normalizes to the
    same key on every page (e.g. "Page 3 of 45" and "Page 4 of 45" both
    become "page # of #") — without this, exact-string matching would
    never see a footer as "repeated" at all, since the number changes
    on every single page."""
    return _DIGIT_RE.sub("#", text.strip().lower())


def _detect_boilerplate_line_keys(all_page_lines, num_pages) -> set:
    """A line is boilerplate if its normalized form appears on at least
    max(BOILERPLATE_MIN_ABSOLUTE, BOILERPLATE_MIN_FRACTION * num_pages)
    DISTINCT pages.

    Counting distinct pages (not raw occurrences) matters: a line
    repeated twice on one page but nowhere else must not be flagged —
    that's topical repetition on a single slide, not the deck-wide
    structural repetition (a banner/footer) this is meant to catch.
    """
    page_counts = {}
    for page_lines in all_page_lines:
        seen_this_page = {_normalize_for_boilerplate(l["text"]) for l in page_lines}
        for key in seen_this_page:
            page_counts[key] = page_counts.get(key, 0) + 1

    threshold = max(BOILERPLATE_MIN_ABSOLUTE, int(BOILERPLATE_MIN_FRACTION * num_pages))
    return {key for key, count in page_counts.items() if count >= threshold}


def _split_title_and_bullets(lines):
    """Title = topmost contiguous run of lines at the page's max font size.
    Everything after that is bullet text (marker characters stripped)."""
    if not lines:
        return None, []

    max_size = max(l["size"] for l in lines)

    title_lines, i = [], 0
    while i < len(lines) and lines[i]["size"] >= max_size - TITLE_SIZE_TOLERANCE:
        title_lines.append(lines[i]["text"])
        i += 1

    title = " ".join(title_lines).strip() or None

    bullets = []
    for line in lines[i:]:
        text = BULLET_MARKER_RE.sub("", line["text"]).strip()
        text = BULLET_MARKER_RE.sub("", text).strip()  # catch leftover (cid:N) tokens
        if text:
            bullets.append(text)

    return title, bullets


def extract_deck(pdf_path: str) -> DeckDocument:
    """Two-pass parse (see module docstring for why one pass isn't
    enough): pass 1 collects every page's lines with no classification;
    boilerplate is detected once across the whole deck; pass 2 strips
    it and *then* runs title/bullet detection. Reversing this order
    would let a boilerplate banner already win the "largest font"
    title check before anyone tried to remove it."""
    with pdfplumber.open(pdf_path) as pdf:
        num_pages = len(pdf.pages)

        # Pass 1: no classification yet, just gather lines per page.
        all_page_lines = [
            _group_words_into_lines(page.extract_words(extra_attrs=["size"]))
            for page in pdf.pages
        ]

        boilerplate_keys = _detect_boilerplate_line_keys(all_page_lines, num_pages)

        # Pass 2: strip boilerplate, then classify what's left.
        slides = []
        for slide_id, page_lines in enumerate(all_page_lines):
            clean_lines = [
                line for line in page_lines
                if _normalize_for_boilerplate(line["text"]) not in boilerplate_keys
            ]
            title, bullets = _split_title_and_bullets(clean_lines)
            slides.append(Slide(
                slide_id=slide_id,
                slide_number=slide_id + 1,
                title=title,
                bullets=bullets,
            ))
    return DeckDocument(source_pdf=pdf_path, num_pages=num_pages, slides=slides)


def save_deck_json(deck: DeckDocument, out_path: str) -> None:
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(deck.to_dict(), f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else PDF_PATH
    out_path = sys.argv[2] if len(sys.argv) > 2 else OUTPUT_PATH

    assert os.path.exists(pdf_path), f"PDF not found: {pdf_path}"

    print(f"Parsing {pdf_path} ...")
    deck = extract_deck(pdf_path)

    n_titled = sum(1 for s in deck.slides if s.title)
    n_empty = sum(1 for s in deck.slides if not s.title and not s.bullets)
    print(f"  {deck.num_pages} pages parsed")
    print(f"  {n_titled} slides with a detected title")
    print(f"  {n_empty} slides with no extractable text")

    save_deck_json(deck, out_path)
    print(f"Saved structured JSON to: {out_path}")


if __name__ == "__main__":
    main()