"""Step 1 output contract: Slide / DeckDocument schema + JSON loader.

Everything downstream (Similiarity_gen, and later the clustering /
retrieval / verification / reporting stages) depends only on this
module's public names — never on how the deck was produced (PDF today,
maybe pptx or a CMS export later).
"""
from .schema import DeckDocument, Slide
from .storage import load_deck_json

__all__ = ["DeckDocument", "Slide", "load_deck_json"]
