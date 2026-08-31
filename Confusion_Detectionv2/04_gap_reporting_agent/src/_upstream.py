"""_upstream.py — bridges to 01_deck_parsing, 02_topic_segmentation,
and 03_question_mapping's schemas + outputs.

Both digit-prefixed-folder-name problems every other stage's
_upstream.py already works around apply here too. Also re-exports
Stage 2's own boundary-scoring primitives (block_similarity,
depth_score) -- tools.py's search_expanding_context reuses these
directly as its "have we crossed into a different topic" switch signal
rather than reimplementing that logic (claude.md Section 6).
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
_similarity = importlib.import_module("02_topic_segmentation.src.similarity")
_segmenter = importlib.import_module("02_topic_segmentation.src.segmenter")
_export = importlib.import_module("03_question_mapping.src.export")
_mapping_schema = importlib.import_module("03_question_mapping.src.schema")

Deck = _deck_schema.Deck
Slide = _deck_schema.Slide
load_deck_json = _deck_storage.load_deck_json
cosine_similarity = _similarity.cosine_similarity
block_similarity = _similarity.block_similarity
depth_score = _similarity.depth_score
TopicSegmenter = _segmenter.TopicSegmenter
GapVerificationInput = _export.GapVerificationInput
TopicDossier = _export.TopicDossier
load_gap_verification_input_json = _export.load_gap_verification_input_json
QuestionMatch = _mapping_schema.QuestionMatch
UnmatchedQuestion = _mapping_schema.UnmatchedQuestion
