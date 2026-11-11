"""deck_workspace.py -- one folder per deck: its source files, its
questions and every stage's output.

Nothing downstream names a specific deck. Every entry point (the
pipeline, the editor CLI, the web server, the Streamlit demo, the
evaluation) takes a deck id and gets all of its paths from here.

Layout (DECKS_DIR, default <project>/decks):

    decks/<deck_id>/
      deck.json                 manifest: title, sources, questions, provenance
      source.pdf | source.pptx  copied in by `add` (or referenced in place)
      questions.txt             real student questions, one per line
      questions.synthetic.txt   LLM-written questions (kept apart, flagged)
      stages/                   parsed_deck.json, topics.json, ..., reports/
      editor/                   edit plans, exports, the web workspace state

A deck with a .pptx can be edited in place (new slides, added bullets,
layout repairs). A PDF-only deck gets new slides inserted as pages of a
copy of the PDF.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUESTION_SOURCES = ("real", "synthetic")


def decks_root() -> Path:
    return Path(os.getenv("DECKS_DIR") or PROJECT_ROOT / "decks").resolve()


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "deck"


def _rel(path: Path) -> str:
    """Store paths relative to the project when possible, so a cloned
    repo works from any location."""
    path = Path(path).resolve()
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _abs(value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else PROJECT_ROOT / p


@dataclass
class DeckWorkspace:
    deck_id: str
    root: Path
    manifest: dict = field(default_factory=dict)

    # -- sources ---------------------------------------------------------
    @property
    def title(self) -> str:
        return self.manifest.get("title") or self.deck_id

    @property
    def pptx(self) -> Optional[Path]:
        return _abs(self.manifest.get("pptx"))

    @property
    def pdf(self) -> Optional[Path]:
        return _abs(self.manifest.get("pdf"))

    @property
    def editable_format(self) -> str:
        """'pptx' when a .pptx exists (full editing), else 'pdf'."""
        return "pptx" if self.pptx else "pdf"

    @property
    def edit_source(self) -> Path:
        return self.pptx or self.pdf

    @property
    def parse_source(self) -> Path:
        """What stage 1 reads. Fixed per deck in the manifest, so stage
        outputs never silently switch parser (topic boundaries depend on it)."""
        return _abs(self.manifest.get("parse_source")) or self.pdf or self.pptx

    @property
    def capabilities(self) -> dict:
        pptx = self.editable_format == "pptx"
        return {"insert_slide": True, "append_bullets": pptx, "layout_repair": pptx and self.pdf is not None,
                "source_previews": True}

    # -- questions -------------------------------------------------------
    @property
    def question_source(self) -> str:
        return self.manifest.get("question_source", "real")

    @property
    def questions(self) -> Path:
        return _abs(self.manifest.get("questions")) or self.root / "questions.txt"

    @property
    def synthetic_questions(self) -> Path:
        return self.root / "questions.synthetic.txt"

    @property
    def synthetic_meta(self) -> Path:
        return self.root / "questions.synthetic.meta.json"

    def has_questions(self) -> bool:
        return self.questions.exists() and any(l.strip() for l in self.questions.read_text(encoding="utf-8-sig").splitlines())

    # -- stage outputs ---------------------------------------------------
    @property
    def stages(self) -> Path:
        return self.root / "stages"

    parsed_deck = property(lambda self: self.stages / "parsed_deck.json")
    topics = property(lambda self: self.stages / "topics.json")
    segmentation_plot = property(lambda self: self.stages / "segmentation.png")
    question_mapping = property(lambda self: self.stages / "question_mapping.json")
    gap_input = property(lambda self: self.stages / "gap_input.json")
    reports_dir = property(lambda self: self.stages / "reports")
    reports_index = property(lambda self: self.stages / "reports" / "index.json")

    @property
    def editor_dir(self) -> Path:
        return self.root / "editor"

    @property
    def editor_state(self) -> Path:
        return self.editor_dir / "workspace"

    def status(self) -> dict:
        reports = sorted(self.reports_dir.glob("topic_*_report.json")) if self.reports_dir.exists() else []
        return {
            "parsed": self.parsed_deck.exists(),
            "segmented": self.topics.exists(),
            "questions": self.has_questions(),
            "mapped": self.gap_input.exists(),
            "reports": len(reports),
        }

    @property
    def editor_deck_path(self) -> Path:
        return self.stages / "editor_deck.json"

    def editor_deck(self):
        """The slide text the editor writes and fact-checks against.

        When the deck has a .pptx, its text is read from the .pptx even if
        the pipeline parsed the PDF: a PDF read line by line interleaves
        multi-column slides ("Principle #1 Principle #3 / Every build ...
        Automate ..."), and suggestions written from that text got the
        deck's own numbering wrong. Slide ids must line up one to one;
        if the counts differ, the pipeline's parse is used."""
        import importlib

        storage = importlib.import_module("01_deck_parsing.src.storage")
        pipeline_deck = storage.load_deck_json(self.parsed_deck)
        if not self.pptx or not self.pptx.exists() or self.parse_source == self.pptx:
            return pipeline_deck
        path = self.editor_deck_path
        if not path.exists() or path.stat().st_mtime < self.pptx.stat().st_mtime:
            deck = importlib.import_module("01_deck_parsing.src.pptx_parser").extract_deck_from_pptx(self.pptx)
            deck.source_pdf = _rel(self.pptx)  # relative, like every stage output
            storage.save_deck_json(deck, path)
        deck = storage.load_deck_json(path)
        return deck if len(deck.slides) == len(pipeline_deck.slides) else pipeline_deck

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "deck.json").write_text(json.dumps(self.manifest, indent=1, ensure_ascii=False), encoding="utf-8")

    def summary(self) -> dict:
        return {"id": self.deck_id, "title": self.title, "format": self.editable_format,
                "question_source": self.question_source, "capabilities": self.capabilities, "status": self.status()}


def load(deck_id: str) -> DeckWorkspace:
    root = decks_root() / deck_id
    manifest_path = root / "deck.json"
    if not manifest_path.exists():
        known = ", ".join(d.deck_id for d in list_decks()) or "none"
        raise FileNotFoundError(f"No deck '{deck_id}' in {decks_root()} (known: {known}). "
                                "Add one with: python run_pipeline.py add <file.pdf|file.pptx>")
    return DeckWorkspace(deck_id, root, json.loads(manifest_path.read_text(encoding="utf-8")))


def list_decks() -> List[DeckWorkspace]:
    root = decks_root()
    if not root.exists():
        return []
    return [load(p.parent.name) for p in sorted(root.glob("*/deck.json"))]


def resolve(deck_id: Optional[str] = None) -> DeckWorkspace:
    """The deck to use: the given id, else $DECK_ID, else the only deck
    there is. Never a built-in default."""
    deck_id = deck_id or os.getenv("DECK_ID")
    if deck_id:
        return load(deck_id)
    decks = list_decks()
    if len(decks) == 1:
        return decks[0]
    if not decks:
        raise FileNotFoundError(f"No decks in {decks_root()}. Add one with: python run_pipeline.py add <file>")
    raise ValueError("Several decks exist; choose one with --deck or DECK_ID: " + ", ".join(d.deck_id for d in decks))


def register(sources: List[Path], *, deck_id: Optional[str] = None, title: Optional[str] = None,
             questions: Optional[Path] = None, copy: bool = True, subject: Optional[str] = None) -> DeckWorkspace:
    """Create a deck workspace from a .pdf and/or a .pptx of the same deck."""
    sources = [Path(s).resolve() for s in sources]
    for s in sources:
        if not s.exists():
            raise FileNotFoundError(s)
        if s.suffix.lower() not in (".pdf", ".pptx"):
            raise ValueError(f"{s.name}: only .pdf and .pptx are supported (convert .ppt to .pptx first)")
    pdfs = [s for s in sources if s.suffix.lower() == ".pdf"]
    pptxs = [s for s in sources if s.suffix.lower() == ".pptx"]
    if len(pdfs) > 1 or len(pptxs) > 1 or not sources:
        raise ValueError("Give one .pdf and/or one .pptx of the same deck")
    deck_id = slugify(deck_id or sources[0].stem)
    root = decks_root() / deck_id
    if (root / "deck.json").exists():
        raise FileExistsError(f"Deck '{deck_id}' already exists at {root}")
    root.mkdir(parents=True, exist_ok=True)

    def place(src: Path, name: str) -> Path:
        if not copy:
            return src
        dst = root / name
        shutil.copy2(src, dst)
        return dst

    manifest = {
        "deck_id": deck_id,
        "title": title or sources[0].stem,
        "subject": subject,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "original_files": [s.name for s in sources],
        "pdf": _rel(place(pdfs[0], "source.pdf")) if pdfs else None,
        "pptx": _rel(place(pptxs[0], "source.pptx")) if pptxs else None,
        "question_source": "real",
    }
    # Stage 1 reads the PDF when there is one (the committed reports of the
    # original deck were made from its PDF); otherwise the .pptx.
    manifest["parse_source"] = manifest["pdf"] or manifest["pptx"]
    ws = DeckWorkspace(deck_id, root, manifest)
    if questions:
        set_questions(ws, Path(questions), source="real")
    ws.save()
    return ws


def set_questions(ws: DeckWorkspace, path: Path, source: str = "real") -> None:
    """Point the deck at a question file. Real questions are copied to
    questions.txt; synthetic ones live in questions.synthetic.txt."""
    if source not in QUESTION_SOURCES:
        raise ValueError(f"source must be one of {QUESTION_SOURCES}")
    target = ws.root / ("questions.txt" if source == "real" else "questions.synthetic.txt")
    if Path(path).resolve() != target.resolve():
        shutil.copy2(path, target)
    ws.manifest["questions"] = _rel(target)
    ws.manifest["question_source"] = source
    ws.save()
