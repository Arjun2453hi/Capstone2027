"""Generates tests/fixtures/sample_deck.pdf — a synthetic deck that
deliberately reproduces the banner-bigger-than-title bug, a page-number
footer, an image-only (blank) slide, a legitimately-repeated bullet
(below the boilerplate threshold), and a long acknowledgements
paragraph that must NOT be mistaken for boilerplate.

Needs `reportlab` (test-fixture-generation only, not a runtime
dependency of parser.py itself).

Regenerate with: python -m tests.fixtures.make_fixture (from 01_deck_parsing/)
"""
from __future__ import annotations

from pathlib import Path

BANNER_TEXT = "CLOUD COMPUTING"
FOOTER_TEMPLATE = "Dr. Prafullata Kiran Auradkar / Department of CSE - Page {page} of {total}"

BANNER_FONT_SIZE = 24  # deliberately bigger than every real title below
TITLE_FONT_SIZE = 18
BULLET_FONT_SIZE = 12
FOOTER_FONT_SIZE = 9

ACK_PARAGRAPH = (
    "Acknowledgements: this material draws on the course's prescribed "
    "textbooks, prior year lecture notes prepared by the course staff, "
    "and publicly available reference material from equipment and "
    "software vendors, adapted here for instructional use only and not "
    "intended for redistribution outside this course."
)

REPEATED_BULLET = "This technique reduces virtualization overhead significantly."

# One entry per page. Page 0 has the long ack paragraph (must not be
# flagged as boilerplate: long-form prose, not a short repeated line).
# Page 4 is fully blank content (banner + footer only) -- the
# image-only-slide edge case. REPEATED_BULLET appears on pages 3 and 7
# only (2 of 8 pages) -- below the boilerplate threshold
# (max(3, 0.3*8)=3), so it must survive as real content.
PAGES = [
    # Title deliberately distinct from BANNER_TEXT ("CLOUD COMPUTING")
    # -- an identical-modulo-case title would itself normalize to the
    # same repeated-boilerplate key as the banner and get correctly
    # stripped, which would be a fixture-design bug, not a parser one.
    {"title": "Course Introduction", "bullets": [ACK_PARAGRAPH]},
    {"title": "Hypervisors", "bullets": [
        "A hypervisor abstracts physical hardware for virtual machines.",
        "Type 1 hypervisors run directly on hardware; Type 2 run atop a host OS.",
    ]},
    {"title": "Virtualization Techniques", "bullets": [
        "Full virtualization traps and emulates privileged instructions.",
        "Paravirtualization requires a modified guest OS for better performance.",
    ]},
    {"title": "Memory Virtualization", "bullets": [
        "Shadow page tables track guest-to-host physical address mappings.",
        REPEATED_BULLET,
    ]},
    {"title": None, "bullets": []},
    {"title": "Popek-Goldberg Theorem", "bullets": [
        "Sensitive instructions must be a subset of privileged instructions for efficient virtualization.",
    ]},
    {"title": "VM Migration", "bullets": [
        "Live migration moves a running VM with minimal downtime.",
        "Cold migration requires stopping the VM before moving it.",
    ]},
    {"title": "Containers", "bullets": [
        "Containers share the host kernel instead of virtualizing hardware.",
        REPEATED_BULLET,
    ]},
]


def build_fixture(out_path: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    out_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out_path), pagesize=letter)
    width, height = letter
    total = len(PAGES)

    for i, page in enumerate(PAGES):
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
            # Wrap long bullets (the ack paragraph) across multiple
            # drawString lines so no single line is absurdly wide --
            # pdfplumber's line-grouping (by vertical position) treats
            # each wrapped line as its own line either way, same as it
            # would for real multi-line slide content.
            words = bullet.split()
            lines, current = [], "-"
            for word in words:
                candidate = f"{current} {word}"
                if len(candidate) > 85:
                    lines.append(current)
                    current = word
                else:
                    current = candidate
            lines.append(current)

            for line in lines:
                c.drawString(70, y, line)
                y -= 18
            y -= 7  # extra gap between bullets

        c.setFont("Helvetica", FOOTER_FONT_SIZE)
        c.drawString(50, 40, FOOTER_TEMPLATE.format(page=i + 1, total=total))

        c.showPage()

    c.save()


if __name__ == "__main__":
    build_fixture(Path(__file__).resolve().parent / "sample_deck.pdf")
    print(f"Wrote {Path(__file__).resolve().parent / 'sample_deck.pdf'}")
