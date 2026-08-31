"""slide_schema.py — the shared Slide / DeckDocument contract.

Owned by `common/` (not by 01_deck_parsing) so that every stage depends
on this one shared definition rather than importing another stage's
internals — the structural issue the old build had (`Global_Context`
importing directly from `Similiarity_gen`) doesn't get reintroduced here.

Will define (once 01_deck_parsing is actually built):
- `Slide`: slide_id (0-indexed, permanent identity, assigned once at
  parse time and never re-derived), slide_number (1-indexed,
  human-readable), title, bullets, raw_text, char_count, and an
  `is_empty()` method that correctly distinguishes a genuinely
  image-only/blank slide from a parsing failure.
- `DeckDocument`: the parsed deck as a whole — source path, page count,
  and the ordered list of Slides.

Scaffolding only — no real code yet, per the project root CLAUDE.md's
build order (01_deck_parsing gets real code first; this file gets
filled in alongside it, since that stage is what actually produces
Slide/DeckDocument instances).
"""
