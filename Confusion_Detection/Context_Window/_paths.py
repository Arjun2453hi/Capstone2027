"""Makes `gap_detection.parsing` and `Similiarity_gen` importable
regardless of caller's cwd.

Context_Window sits between them: it needs gap_detection's DeckDocument
schema directly (to walk slide_ids/raw_text) and Similiarity_gen's
QuestionSlideRetriever (to get the anchor slide for a topic's
representative query) — both are siblings under Confusion_Detection/,
not one package tree, so this is the one place that bridges all three.
"""
from __future__ import annotations

import sys
from pathlib import Path

_CONFUSION_DETECTION_DIR = Path(__file__).resolve().parent.parent
_DOCUMENT_PARSING_DIR = _CONFUSION_DETECTION_DIR / "DocumentParsing"

for _p in (_CONFUSION_DETECTION_DIR, _DOCUMENT_PARSING_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
