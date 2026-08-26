"""Makes `gap_detection.parsing` importable regardless of caller's cwd.

Similiarity_gen (Step 2/3's engine) and gap_detection/ (Step 1's package,
under ../DocumentParsing) are siblings under Confusion_Detection/, not
one package tree — this file is the one place that bridges them, so a
path change only ever needs an edit here.
"""
from __future__ import annotations

import sys
from pathlib import Path

_DOCUMENT_PARSING_DIR = Path(__file__).resolve().parent.parent / "DocumentParsing"

if str(_DOCUMENT_PARSING_DIR) not in sys.path:
    sys.path.insert(0, str(_DOCUMENT_PARSING_DIR))
