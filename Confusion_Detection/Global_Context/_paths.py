"""Makes `Similiarity_gen` importable regardless of caller's cwd.

Global_Context reuses Similiarity_gen's EmbeddingModel interface (per
claude.md Section 2: "reused, not duplicated") — they're siblings under
Confusion_Detection/, not one package tree, so this is the one place
that bridges them.
"""
from __future__ import annotations

import sys
from pathlib import Path

_CONFUSION_DETECTION_DIR = Path(__file__).resolve().parent.parent

if str(_CONFUSION_DETECTION_DIR) not in sys.path:
    sys.path.insert(0, str(_CONFUSION_DETECTION_DIR))
