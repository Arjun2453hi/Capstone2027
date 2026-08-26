# CLAUDE.md — Similarity_gen

This file is context for any Claude session (chat or Claude Code) working
inside the `Similarity_gen/` folder. Read this before touching any file
here.

---

## 1. Where this fits in the larger project

We're rebuilding a "Gap Detection" system that finds what a slide deck
fails to answer, given a list of student questions. The target
architecture has 5 stages:

1. **Document Structuring** — parse the deck into slide-level
   title/bullets/notes, group slides into modules. **(Done — see
   `gap_detection/parsing/`.)**
2. **Global Question Topology** — embed all questions, cluster into
   topic buckets, distill each into a representative query.
3. **Multi-Context Retrieval** — hybrid (BM25 + dense) search per topic
   bucket, with multi-slide context windows.
4. **Synthesized Verification** — an LLM judges completeness per topic
   cluster and classifies the gap type (Complete Omission / Shallow
   Coverage / Fragmented Context).
5. **Hierarchical Gap Reporting** — severity-scored report grouped by
   Module → Topic → Slide range, with revision recommendations.

**`Similarity_gen` is the semantic-similarity engine that Steps 2 and 3
are built on.** Its job, narrowly: given a question and the parsed
deck, say which slide(s) are most relevant to it, with a numeric score.
It does **not** do clustering, hybrid BM25 fusion, multi-slide context
windows, or LLM judgment — those are separate stages that will consume
this folder's output. Keep this folder scoped to embedding + similarity
only; don't let Step 2/3/4 logic creep in here.

## 2. Inputs this folder consumes (already built, do not modify)

- `gap_detection/parsing/schema.py` — `Slide` and `DeckDocument`
  dataclasses. **`slide_id` is the permanent, 0-indexed identity** used
  everywhere downstream — never re-derive slide identity from position
  in a list.
- `gap_detection/parsing/storage.py` — `load_deck_json(path) ->
  DeckDocument`.

Deck JSON shape (already validated with tests, see
`tests/test_pdf_parser.py` and `tests/test_storage.py`):

```json
{
  "source_pdf": "...",
  "extracted_at": "...",
  "num_pages": 7,
  "slides": [
    {
      "slide_id": 0,
      "slide_number": 1,
      "title": "Unit 2: Mocking Dependencies",
      "bullets": ["...", "..."],
      "notes": null,
      "raw_text": "Unit 2: Mocking Dependencies\n...",
      "char_count": 160,
      "module_id": null
    }
  ]
}
```

A synthetic fixture deck already exists at
`tests/fixtures/sample_deck.pdf` (7 slides, includes a title-only slide
and a fully blank slide as edge cases — see
`tests/fixtures/make_fixture.py`). Extend it with more slides if the
ground-truth test set below needs more variety; don't replace it, since
Step 1's tests depend on its current exact content.

## 3. Non-negotiable design requirements

**This must be dependency-injectable.** The whole point: swapping the
similarity strategy later (different embedding model, or eventually
BM25/hybrid) should mean writing one new class, not touching callers.

- `EmbeddingModel` — an abstract interface (`fit(corpus)` optional
  no-op hook, `embed(texts) -> np.ndarray` abstract). Every concrete
  model is a subclass. Nothing outside `embedding_models.py` may import
  a concrete embedding library directly (no bare `sentence_transformers`
  imports in `slide_index.py` or `retriever.py`).
- Provide **at least two working implementations**:
  - `TfidfEmbeddingModel` — sklearn only, no download, deterministic.
    Cheap baseline, always available.
  - `SentenceTransformerEmbeddingModel` — real semantic embeddings
    (`all-MiniLM-L6-v2` — small, free, local after first download).
    **Internet is available now — actually exercise this model in
    tests, don't mock it.**
- `SlideIndex` — builds once per deck: embeds every **non-empty** slide
  (skip slides with no title and no bullets — embedding blank text is
  noise that can spuriously rank high). Must cache computed embeddings
  to disk, keyed by `(deck source, model name, content hash)` — so
  editing the deck or swapping models auto-invalidates the cache rather
  than silently serving stale vectors.
- `QuestionSlideRetriever` — takes a `SlideIndex`, **not a separate
  model reference**. It must read the embedding model off the index
  it's given, so a query can never accidentally be embedded with a
  different model (or a differently-fitted TF-IDF vectorizer) than the
  slides were. Support both single-question and batch retrieval.
  Returns ranked `(slide_id, slide_number, score)` results, `top_k`
  configurable.
- Every file: docstrings that explain **why** a design choice was made,
  not just what the code does. Someone (or a future Claude session)
  should be able to swap the strategy without reading the whole module.

## 4. Testing philosophy — numeric, not just pass/fail

Internet is available for these tests. Use a real model, not a fake.
The point of testing this folder is to know **how good the similarity
signal actually is**, not just that the code runs.

Requirements for the test suite:

- **Ground-truth fixture**: a small set of questions where you (the
  human) already know which slide should rank #1. Store this as an
  explicit mapping, e.g.:
  ```python
  GROUND_TRUTH = [
      {"question": "What is mocking used for?", "expected_slide_id": 0},
      {"question": "What's the difference between a stub and a spy?", "expected_slide_id": 2},
  ]
  ```
- **Report real numbers.** Every test that checks similarity should
  print or log the actual score (not just assert a boolean), e.g.:
  `print(f"Q: {q!r} -> slide {top_slide_id} (score={score:.4f}), expected {expected}")`
  A future reader should be able to see the score distribution, not
  just "PASSED".
- **Run the same test logic against both embedding models**
  (parametrize over `TfidfEmbeddingModel` and
  `SentenceTransformerEmbeddingModel`) — this is what actually proves
  the DI works, not just a claim in a docstring.
- **Include at least one hard-negative question** — one that shouldn't
  match any slide well — and assert its best score is noticeably lower
  than the ground-truth matches' scores. This catches a model that
  scores everything similarly high (a real failure mode with some
  embeddings on short texts).
- **Aggregate metric**: compute and print top-1 accuracy against the
  ground-truth set, plus mean/median top-1 score. Treat this as a
  regression baseline — if a later change drops accuracy, that should
  be visible in the numbers, not just "tests still pass."
- If a real assertion fails, fix the code or flag the ambiguity back —
  don't loosen the assertion or delete the test to make it pass. The
  entire system exists to catch failures; a test suite that's been
  edited to always pass defeats the purpose.

## 5. Expected file structure

```
Similarity_gen/
  __init__.py
  embedding_models.py    # EmbeddingModel ABC + TfidfEmbeddingModel + SentenceTransformerEmbeddingModel
  cache.py                # disk cache for slide embeddings (keyed by deck+model+content hash)
  slide_index.py            # SlideIndex — embeds and holds all non-empty slides for a deck
  retriever.py                # QuestionSlideRetriever + RetrievalResult dataclass
tests/
  fixtures/
    ground_truth_questions.py   # question -> expected_slide_id mapping
  test_embedding_models.py
  test_slide_index.py
  test_retriever.py               # the numeric ground-truth accuracy tests described above
```

Draft versions of `embedding_models.py`, `cache.py`, and `slide_index.py`
already exist (built without internet access, so only tested against
`TfidfEmbeddingModel` so far) — they need `retriever.py` added, the real
`SentenceTransformerEmbeddingModel` path actually exercised now that
internet is available, and the numeric ground-truth test suite written
per Section 4.

---

## 6. Prompt to run (paste this in)

```
Build out the Similarity_gen folder per CLAUDE.md in this directory.

Specifically:
1. Review the existing draft files (embedding_models.py, cache.py,
   slide_index.py) against the requirements in CLAUDE.md Section 3 —
   fix anything that violates the DI rules (e.g. any concrete embedding
   library imported outside embedding_models.py).
2. Write retriever.py: QuestionSlideRetriever + RetrievalResult, per
   Section 3's spec (it must read the model off the SlideIndex, not
   take a separate model argument).
3. Write tests/fixtures/ground_truth_questions.py with at least 6
   question -> expected_slide_id pairs against tests/fixtures/sample_deck.pdf
   (extend that fixture deck with more slides first if 7 slides isn't
   enough variety for a meaningful ground-truth set).
4. Write the test suite per Section 4 — run it against BOTH
   TfidfEmbeddingModel and SentenceTransformerEmbeddingModel (install
   sentence-transformers, actually download all-MiniLM-L6-v2, don't
   mock it), and make every test print the real similarity scores and
   the aggregate top-1 accuracy / mean score, not just pass/fail.
5. Run the full suite and show me the actual numeric output — top-1
   accuracy per model, and the full score table — before we move on to
   Step 2 (question clustering).
```