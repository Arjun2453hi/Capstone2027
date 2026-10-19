"""_upstream.py — bridges to the earlier stages' schemas and loaders.

Same digit-prefixed-folder workaround every other stage's _upstream.py
uses: the names can't be imported with a normal `from 01_...` statement,
so this is the one file that resolves them by string and re-exports
them.
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
_export = importlib.import_module("03_question_mapping.src.export")
_mapping_schema = importlib.import_module("03_question_mapping.src.schema")
_report_schema = importlib.import_module("04_gap_reporting_agent.src.schema")

Deck = _deck_schema.Deck
Slide = _deck_schema.Slide
load_deck_json = _deck_storage.load_deck_json
TopicDossier = _export.TopicDossier
load_gap_verification_input_json = _export.load_gap_verification_input_json
QuestionMatch = _mapping_schema.QuestionMatch
GapReport = _report_schema.GapReport
