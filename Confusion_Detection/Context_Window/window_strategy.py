"""ContextWindowStrategy — how an anchor slide expands into a window.

Same DI posture as the previous two folders: ContextWindowBuilder
depends on this interface, never a concrete strategy directly, so
swapping FixedRadiusWindow for ModuleAwareWindow later is a
constructor-argument change, not a rewrite.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from . import _paths  # noqa: F401  (side effect: puts DocumentParsing on sys.path)

from gap_detection.parsing.schema import DeckDocument


class ContextWindowStrategy(ABC):
    @abstractmethod
    def build_window(self, anchor_slide_id: int, deck: DeckDocument) -> List[int]:
        """Return an ordered (ascending) list of slide_ids to include,
        always containing `anchor_slide_id`. Implementations must clamp
        to the deck's actual slide_id range — no negative ids, nothing
        past the deck's max slide_id, no wraparound."""
        raise NotImplementedError


class FixedRadiusWindow(ContextWindowStrategy):
    """Default strategy: anchor +/- `radius` adjacent slide_ids.

    Deliberately positional, not content-aware — it doesn't know or care
    whether a neighbor slide is blank or on-topic. That's a real
    limitation (see ModuleAwareWindow below and claude.md Section 6),
    but it's a correct, usable baseline that needs nothing from Step 1
    beyond slide_id/slide ordering, which already exists.
    """

    def __init__(self, radius: int = 1):
        if radius < 0:
            raise ValueError(f"radius must be >= 0, got {radius}")
        self._radius = radius

    @property
    def radius(self) -> int:
        return self._radius

    def build_window(self, anchor_slide_id: int, deck: DeckDocument) -> List[int]:
        if not deck.slides:
            return [anchor_slide_id]  # degenerate deck; nothing to clamp against

        min_id = min(s.slide_id for s in deck.slides)
        max_id = max(s.slide_id for s in deck.slides)
        lo = max(min_id, anchor_slide_id - self._radius)
        hi = min(max_id, anchor_slide_id + self._radius)
        return list(range(lo, hi + 1))


class ModuleAwareWindow(ContextWindowStrategy):
    """Intended eventual default, once Step 1's module grouper exists.

    Would expand the window to the anchor slide's full module boundary
    (e.g. every slide sharing its `module_id`) instead of a fixed
    radius — a concept doesn't always span exactly +/-1 slide, and a
    fixed radius can include an irrelevant neighbor or miss a relevant
    slide just outside it, especially near a module boundary.

    NOT implemented: `module_id` is `null` on every slide today (Step
    1's grouper was never built — see Context_Window/claude.md Section
    6). This is blocked on that gap, not on anything in this folder.
    """

    def build_window(self, anchor_slide_id: int, deck: DeckDocument) -> List[int]:
        raise NotImplementedError(
            "ModuleAwareWindow requires every slide's module_id to be "
            "populated by Step 1's module grouper, which hasn't been "
            "built yet (module_id is null on every slide in the current "
            "deck JSON). Use FixedRadiusWindow until that exists."
        )
