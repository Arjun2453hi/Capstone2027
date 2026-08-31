"""parser.py — extract_deck(pdf_path) -> Deck.

Two-pass parsing (see boilerplate.py's module docstring and claude.md
Section 5): pass 1 collects every page's lines with no classification
yet; boilerplate is detected once across the whole deck; pass 2 strips
it and *then* runs title/bullet detection. Order matters -- stripping
after classification would already be too late, since a repeated
banner in a bigger font than the real heading would have already won
the "largest font at the top = title" check.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple, Union

import pdfplumber

from .boilerplate import detect_boilerplate_line_keys, normalize_for_repetition
from .schema import Deck, Slide

LINE_TOLERANCE = 3.0  # px: words within this vertical distance = "same line"
TITLE_SIZE_TOLERANCE = 0.5  # px: font-size slack when matching the page's max size
BULLET_MARKER_RE = re.compile(
    r"^(?:[•●‣⁃\-\*]|\(cid:\d+\))\s*"
)


def _group_words_into_lines(words: List[dict]) -> List[dict]:
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


def _split_title_and_bullets(lines: List[dict]) -> Tuple[Optional[str], Optional[float], List[str]]:
    """Title = topmost contiguous run of lines at the page's max font
    size (after boilerplate stripping). Returns (title, title_font_size,
    bullets) -- title_font_size is the size that run was detected at,
    None if there's no title at all."""
    if not lines:
        return None, None, []

    max_size = max(line["size"] for line in lines)

    title_lines, i = [], 0
    while i < len(lines) and lines[i]["size"] >= max_size - TITLE_SIZE_TOLERANCE:
        title_lines.append(lines[i]["text"])
        i += 1

    title = " ".join(title_lines).strip() or None
    title_font_size = max_size if title else None

    bullets = []
    for line in lines[i:]:
        text = BULLET_MARKER_RE.sub("", line["text"]).strip()
        text = BULLET_MARKER_RE.sub("", text).strip()  # catch leftover (cid:N) tokens
        if text:
            bullets.append(text)

    return title, title_font_size, bullets


def extract_deck(pdf_path: Union[str, Path]) -> Deck:
    pdf_path = str(pdf_path)
    with pdfplumber.open(pdf_path) as pdf:
        num_pages = len(pdf.pages)

        # Pass 1: no classification yet, just gather lines per page.
        all_page_lines = [
            _group_words_into_lines(page.extract_words(extra_attrs=["size"]))
            for page in pdf.pages
        ]

        boilerplate_keys = detect_boilerplate_line_keys(all_page_lines, num_pages)

        # Pass 2: strip boilerplate, then classify what's left.
        slides = []
        for slide_id, page_lines in enumerate(all_page_lines):
            clean_lines = [
                line for line in page_lines
                if normalize_for_repetition(line["text"]) not in boilerplate_keys
            ]
            title, title_font_size, bullets = _split_title_and_bullets(clean_lines)
            slides.append(
                Slide(
                    slide_id=slide_id,
                    slide_number=slide_id + 1,
                    title=title,
                    title_font_size=title_font_size,
                    bullets=bullets,
                )
            )

    return Deck(source_pdf=pdf_path, num_pages=num_pages, slides=slides)
