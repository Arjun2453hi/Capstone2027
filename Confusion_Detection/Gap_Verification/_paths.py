"""Makes `Context_Window` (and transitively `Similiarity_gen`,
`gap_detection.parsing`) importable regardless of caller's cwd.

Gap_Verification only needs Context_Window's ContextBundle schema
directly; Confusion_Detection is the shared root all four folders sit
under, so inserting it once here is enough.
"""
from __future__ import annotations

import sys
from pathlib import Path

_CONFUSION_DETECTION_DIR = Path(__file__).resolve().parent.parent

if str(_CONFUSION_DETECTION_DIR) not in sys.path:
    sys.path.insert(0, str(_CONFUSION_DETECTION_DIR))
