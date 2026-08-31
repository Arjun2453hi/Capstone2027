"""_upstream.py — bridges to 01_deck_parsing's Deck/Slide schema and
its near-duplicate detector.

01_deck_parsing's folder name starts with a digit, so `from
01_deck_parsing.src.schema import Deck` is a syntax error in source
code (Python identifiers can't start with a digit) even though the
import *system* has no such restriction -- `importlib.import_module`
takes a plain string and resolves it fine. This is the one file that
does that string-based import and re-exports the names cleanly, so
nothing else in this stage needs to know about the workaround.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_schema = importlib.import_module("01_deck_parsing.src.schema")
_storage = importlib.import_module("01_deck_parsing.src.storage")
_boilerplate = importlib.import_module("01_deck_parsing.src.boilerplate")

Deck = _schema.Deck
Slide = _schema.Slide
load_deck_json = _storage.load_deck_json
find_near_duplicate_groups = _boilerplate.find_near_duplicate_groups
