"""Makes `gap_detection.parsing` (for DeckDocument) importable
regardless of caller's cwd. Gap_Reporting's own logic never imports
Gap_Verification directly (it duck-types on GapVerdict's shape, same
one-way-dependency posture as every prior folder) — only
run_reporting.py's CLI does, to construct real verdict objects from
verification_report.json.
"""
from __future__ import annotations

import sys
from pathlib import Path

_CONFUSION_DETECTION_DIR = Path(__file__).resolve().parent.parent
_DOCUMENT_PARSING_DIR = _CONFUSION_DETECTION_DIR / "DocumentParsing"

for _p in (_CONFUSION_DETECTION_DIR, _DOCUMENT_PARSING_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
