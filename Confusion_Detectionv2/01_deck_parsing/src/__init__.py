"""01_deck_parsing: PDF -> structured Slide/Deck JSON. See CLAUDE.md."""
from .parser import extract_deck
from .schema import Deck, Slide
from .storage import load_deck_json, save_deck_json

__all__ = ["Slide", "Deck", "extract_deck", "save_deck_json", "load_deck_json"]
