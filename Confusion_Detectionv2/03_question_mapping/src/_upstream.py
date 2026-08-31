"""_upstream.py — bridges to 01_deck_parsing's Deck/Slide schema and
02_topic_segmentation's Topic schema.

Both folder names start with a digit, so `from 01_deck_parsing.src...`
is a syntax error in source code even though the import *system* has
no such restriction -- `importlib.import_module` takes a plain string
and resolves it fine. This is the one file that does that string-based
import and re-exports the names cleanly.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_deck_schema = importlib.import_module("01_deck_parsing.src.schema")
_deck_storage = importlib.import_module("01_deck_parsing.src.storage")
_topic_schema = importlib.import_module("02_topic_segmentation.src.schema")
_topic_storage = importlib.import_module("02_topic_segmentation.src.storage")

Deck = _deck_schema.Deck
Slide = _deck_schema.Slide
load_deck_json = _deck_storage.load_deck_json
Topic = _topic_schema.Topic
load_topics_json = _topic_storage.load_topics_json
