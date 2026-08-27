"""Context_Window — Step 3's second half: turns one retrieved slide
into a multi-slide context window for Step 4's LLM judge.

Scoped narrowly (see claude.md Section 2): retrieval itself is
Similiarity_gen's job; this folder only expands an anchor slide into a
window and assembles budget-enforced text. No LLM judgment here.
"""
from .builder import ContextWindowBuilder
from .schema import ContextBundle
from .window_strategy import ContextWindowStrategy, FixedRadiusWindow, ModuleAwareWindow

__all__ = [
    "ContextWindowBuilder",
    "ContextBundle",
    "ContextWindowStrategy",
    "FixedRadiusWindow",
    "ModuleAwareWindow",
]
