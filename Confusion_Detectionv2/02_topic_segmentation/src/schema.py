"""schema.py — Topic, this stage's output contract.

Deliberately excludes assembled slide text -- Stage 3 pulls content
from Stage 1's JSON via `slide_ids` when needed. Keep this module
single-purpose: boundaries only (claude.md Section 5).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class Topic:
    topic_id: int
    start_slide_id: int
    end_slide_id: int
    slide_ids: List[int]
    boundary_confidence: float  # the combined score that triggered this cut; 0.0 for the deck's first topic (no preceding cut)
