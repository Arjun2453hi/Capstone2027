"""Generates tests/fixtures/sample_deck.pdf — a synthetic deck realistic
enough to catch the boilerplate bug (DocumentParsing/claude.md Section 2).

The original fixture only tested clean, isolated slide content with no
repeated banner — that's exactly what let the bug ship unnoticed. This
one puts a banner in a BIGGER font than every real title (the specific
condition that makes the banner win the pre-fix "largest font" title
check) and a page-number footer on every page.

Needs `reportlab` (test-fixture-generation only — not a runtime
dependency of DocParsing_1.py itself): `pip install reportlab`.

Regenerate with: python -m DocumentParsing.tests.fixtures.make_fixture
"""
from __future__ import annotations

from pathlib import Path

BANNER_TEXT = "Software Engineering Unit 2 - Course UE23CS341A"
FOOTER_TEMPLATE = "Page {page} of {total}"

BANNER_FONT_SIZE = 24  # deliberately bigger than every real title below
TITLE_FONT_SIZE = 18
BULLET_FONT_SIZE = 12
FOOTER_FONT_SIZE = 10

# One entry per page. Page 3 is title-only (no bullets) and page 4 is
# fully blank content (banner + footer only) — the same edge cases the
# original fixture covered, still exercised here. Page 5 repeats one
# bullet from page 0 verbatim: 2 out of 8 pages is below the boilerplate
# threshold (max(3, 0.3*8)=3), so it must survive as real content —
# this is what proves the fix doesn't over-strip legitimate repetition.
PAGES = [
    {
        "title": "Introduction to Testing",
        "bullets": [
            "Testing verifies software behaves as expected.",
            "Mocking replaces real dependencies with test doubles.",
        ],
    },
    {
        "title": "Stubs and Spies",
        "bullets": [
            "A stub returns canned answers.",
            "A spy records how it was called.",
        ],
    },
    {
        "title": "Test-Driven Development",
        "bullets": [
            "Red: write a failing test first.",
            "Green: make it pass.",
            "Refactor: clean up.",
        ],
    },
    {
        "title": "Questions?",
        "bullets": [],
    },
    {
        "title": None,
        "bullets": [],
    },
    {
        "title": "Mocking in Practice",
        "bullets": [
            "Mocking replaces real dependencies with test doubles.",
            "Useful for isolating the unit under test.",
        ],
    },
    {
        "title": "RACI Matrix",
        "bullets": [
            "Responsible does the work.",
            "Accountable owns the outcome.",
        ],
    },
    {
        "title": "COCOMO Estimation",
        "bullets": [
            "Effort equals a times KLOC to the power b.",
            "Three project types: organic, semi-detached, embedded.",
        ],
    },
]


def build_fixture(out_path: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    out_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out_path), pagesize=letter)
    width, height = letter
    total = len(PAGES)

    for i, page in enumerate(PAGES):
        # Banner: always present, biggest font on the page, at the top —
        # the exact condition that made the pre-fix parser misclassify
        # it as the title.
        c.setFont("Helvetica-Bold", BANNER_FONT_SIZE)
        c.drawString(50, height - 50, BANNER_TEXT)

        y = height - 100
        if page["title"]:
            c.setFont("Helvetica-Bold", TITLE_FONT_SIZE)
            c.drawString(50, y, page["title"])
            y -= 40
        else:
            y -= 10

        c.setFont("Helvetica", BULLET_FONT_SIZE)
        for bullet in page["bullets"]:
            c.drawString(70, y, f"- {bullet}")
            y -= 25

        # Footer: always present, bottom of page, digit varies per page.
        c.setFont("Helvetica", FOOTER_FONT_SIZE)
        c.drawString(50, 40, FOOTER_TEMPLATE.format(page=i + 1, total=total))

        c.showPage()

    c.save()


if __name__ == "__main__":
    build_fixture(Path(__file__).resolve().parent / "sample_deck.pdf")
    print(f"Wrote {Path(__file__).resolve().parent / 'sample_deck.pdf'}")
