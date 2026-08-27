# Gap Detection — Full Progress Summary

Everything built, tested, and run so far, from the start of this
project. Supersedes `SEMANTIC_AND_GLOBAL_CONTEXT_SUMMARY.md` (kept for
history) — this file additionally covers `Context_Window` and the
`DocumentParsing` boilerplate-bug fix.

Gap Detection finds what a slide deck fails to answer, given a list of
real student questions, in 5 stages:

| # | Stage | Status |
|---|---|---|
| 1 | **Document Structuring** — parse deck into slide-level title/bullets, group into modules | **Mostly done** — `DocumentParsing/gap_detection/parsing/`. Module grouping (`module_id`) was never built; still `null` on every slide. |
| 2 | **Global Question Topology** — embed all questions, cluster into topic buckets, distill each to one query | **Done** — `Global_Context/` |
| 3 | **Multi-Context Retrieval** — dense similarity (single best slide) + multi-slide context windows | **Done** — dense half in `Similiarity_gen/`, window-assembly half in `Context_Window/` |
| 4 | **Synthesized Verification** — LLM judges completeness per topic, classifies gap type, drafts a suggested addition | **Not built** |
| 5 | **Hierarchical Gap Reporting** — severity-scored report, Module → Topic → Slide range, actionable revision suggestions | **Not built** |

**The actual end goal** (confirmed while building Step 3): not just a diagnostic
report, but concrete *slide revision suggestions* an instructor can act on directly.

---

## 1. `DocumentParsing/` — Step 1: Document Structuring

### 1a. Initial build: the Step 1 output contract

`gap_detection/parsing/` was referenced by every later folder's `claude.md` as an
existing dependency but wasn't actually in the repo — built from scratch to match
the JSON shape `DocParsing_1.py` already produced:

- `schema.py` — `Slide` / `DeckDocument` dataclasses. `slide_id` is the permanent,
  0-indexed identity used everywhere downstream (never re-derived from list
  position). `Slide.is_empty` flags slides with no title and no bullets.
- `storage.py` — `load_deck_json(path) -> DeckDocument` / `save_deck_json`.

### 1b. Bug found and fixed: boilerplate banner corrupting the parse

**Symptom:** two anchor slides retrieved by `Similiarity_gen` (270, 138) looked
wrong on manual inspection during `Context_Window` testing.

**Root cause:** `DocParsing_1.py`'s title heuristic is "largest font at the top of
the page." Real slide decks carry a repeated course/unit banner and a page-number
footer on every page; when the banner renders bigger than the real heading (common
with university templates), it wins the title slot and the real title gets
demoted into the bullets.

**Fix — two-pass parsing:**
1. Pass 1 collects every page's lines, no classification yet.
2. A line is flagged boilerplate if it recurs (digit-normalized, so page numbers
   count as one repeated key) on ≥`max(3, 30%×num_pages)` **distinct pages**.
3. Pass 2 strips boilerplate lines, *then* runs title/bullet detection on what's left.

**New test suite** (`tests/`, first one this folder ever had) — 7/7 pass:
- Fixture (`tests/fixtures/make_fixture.py`, needs `reportlab`) with a banner in a
  bigger font than every real title, a page-number footer, and one bullet
  legitimately repeated on only 2/8 pages (below the boilerplate threshold, so it
  must survive).
- Verified against the *old* one-pass logic first that this fixture actually
  reproduces the bug (every title became the banner) before confirming the fix
  resolves it.

**Real-deck impact (`se-u2-slides.pdf`, 298 slides):**

| Metric | Value |
|---|---|
| Slides whose title was literally the banner (now `null`) | 11 / 298 |
| Slides with boilerplate contaminating bullets (now clean) | 142 / 298 |
| Total noise characters removed deck-wide | 15,251 (133,068 → 117,817) |
| Non-empty slide count | 298 → 287 |

Slides 270 and 138 specifically: their *titles* were already correct before the
fix — the fix cleaned garbled boilerplate out of their *bullets*, not a wrong
title. (Correction to an earlier report: I'd misread slide 138 as content-free due
to confusing `slide_number` with `slide_id` in a printed window — it always had
real content one slide over.)

**Known follow-up, out of this fix's scope:** some slides carry a *non-uniform*
character-duplication rendering glitch (e.g. `"DDIeeSppFaaCrrRttmm Eeexnnettc"`)
that varies per page, so it doesn't exact-string-match as "repeated" and isn't
caught by this fix. Different root cause from the banner bug; not yet addressed.

---

## 2. `Similiarity_gen/` — semantic-similarity engine (Steps 2/3's base)

**Job:** given a question and a parsed deck, say which slide(s) are most relevant,
with a numeric score. Scoped to embedding + similarity only.

### What was built
- `embedding_models.py` — `EmbeddingModel` ABC + `TfidfEmbeddingModel` (sklearn,
  no download) + `SentenceTransformerEmbeddingModel` (`all-MiniLM-L6-v2`). Every
  concrete embedding-library import isolated to this one file.
- `cache.py` — `EmbeddingCache`, disk cache keyed by `(deck source, model name,
  content hash)`; editing the deck or swapping models auto-invalidates it.
- `slide_index.py` — `SlideIndex`, embeds every non-empty slide once per deck.
- `retriever.py` — `QuestionSlideRetriever` + `RetrievalResult`; reads the
  embedding model **off the `SlideIndex`**, never a separate reference, so a query
  can't accidentally be embedded in a different vector space than the slides.
- `run_retrieval.py` — generic CLI, auto-detects the deck JSON and questions file
  in a directory by content, not filename.
- `tests/` — ground-truth fixture (8 questions across 10 synthetic slides,
  including a title-only and a fully blank slide as edge cases) + numeric suite.

### Test results — 12/12 pass, both real models (no mocking)

| Model | Top-1 accuracy | Mean top-1 score | Median | Hard-negative score |
|---|---|---|---|---|
| TF-IDF | 100% (8/8) | 0.3854 | 0.3595 | 0.0000 |
| sentence-transformers | 100% (8/8) | 0.6626 | 0.6892 | 0.0852 |

### Real-data run — 298 slides × 800 questions, top_k=3

| Model | Before boilerplate fix (mean / low-conf) | After fix (mean / low-conf) |
|---|---|---|
| TF-IDF | 0.2754 / 93 of 800 | **0.3042** / 90 of 800 |
| sentence-transformers | 0.5893 / 0 of 800 | **0.5923** / 0 of 800 |

Modest, not dramatic, improvement — as expected, since the banner was near-uniform
noise across the whole deck. Full per-question results: `retrieval_report.json`.

---

## 3. `Global_Context/` — Step 2: Global Question Topology

**Job:** collapse 800 raw questions into far fewer topic buckets, each with one
clean representative query, so Step 4 doesn't waste LLM calls re-verifying the
same concept dozens of times.

### What was built
- `schema.py` — `TopicCluster` / `TopologyResult`. Validates `source_questions` /
  `question_indices` stay in lockstep, and every `is_noise` topic has exactly 1
  source question.
- `clustering.py` — `Clusterer` ABC + `HDBSCANClusterer` (default; sklearn's
  built-in `HDBSCAN`, `-1` = noise, never forced into a bucket) + `KMeansClusterer`
  (comparison baseline; silhouette-selected k, no noise concept).
- `distillation.py` — `Distiller` ABC + `CentroidClosestDistiller` — picks the
  real question nearest the cluster centroid.
- `topology_builder.py` — `QuestionTopologyBuilder`, the only file importing all
  three interfaces together; L2-normalizes embeddings once; every `-1` becomes its
  own singleton `TopicCluster(is_noise=True)` — **never dropped**.
- `run_topology.py` — generic CLI with a `--sweep-min-cluster-size` option.
- `tests/` — ground-truth fixture (4 real topics × 3 phrasings + 2 unrelated
  singleton outliers) + the ARI-scored suite.

### Ground-truth ARI — 12/12 pass

| Clusterer | TF-IDF | sentence-transformers |
|---|---|---|
| HDBSCAN | 0.8389 | 0.3368 |
| KMeans | 0.6901 | **0.8967** |

HDBSCAN correctly isolated both outliers as singletons in every run; KMeans has no
noise concept, so isolation there is incidental.

### Real-data run — 800 questions

**KMeans "won" the 14-question fixture, but doesn't hold up on real data.**
Silhouette-selected k=13 produces 13 clusters averaging 62 questions each that mix
unrelated concepts (e.g. WBS questions folded into an "architecture definition"
cluster) — `topology_report_kmeans_comparison.json`.

**HDBSCAN + sentence-transformers, `min_cluster_size=3`** (`topology_report.json`):
36 real clusters, 409 noise singletons, cluster sizes min 3 / median 5.0 / mean
10.86 / max 51 — e.g. a 51-question technical-debt cluster, a 44-question COCOMO
cluster, a 40-question microservices cluster.

`min_cluster_size` sweep: 2 → 168 clusters (over-split) · **3 → 36 (kept as
default)** · 5 → 12 · 8 → 7 (too coarse, merges distinct topics).

**Recommendation (still pending explicit sign-off):** keep
`HDBSCANClusterer(min_cluster_size=3)` + sentence-transformers as the default —
the tiny fixture isn't representative of 800 messy real questions, and real-data
cluster coherence is what matters for Step 5.

---

## 4. `Context_Window/` — Step 3, second half: multi-slide context windows

**Job:** `Similiarity_gen`'s retriever answers "which single slide is closest to
this question?" — not enough for Step 4, since a concept can span 2-3 adjacent
slides. This folder turns one retrieved anchor slide into a window of stitched,
budget-enforced text.

### What was built
- `window_strategy.py` — `ContextWindowStrategy` ABC + `FixedRadiusWindow`
  (default, radius=1, clamped to deck bounds, no wraparound) + a `ModuleAwareWindow`
  stub raising `NotImplementedError` (blocked on Step 1's still-missing module
  grouper, not on anything in this folder).
- `schema.py` — `ContextBundle` (topic_id, representative_query, anchor_slide_id,
  window_slide_ids, window_text, source_questions, cluster_size); validates the
  anchor is always inside its own window.
- `builder.py` — `ContextWindowBuilder`: retrieves the anchor via
  `QuestionSlideRetriever`, expands via the injected strategy, assembles
  `window_text` under a `max_context_chars` budget. Design decisions:
  - A blank slide inside the window keeps its `slide_id` positionally (real
    physical neighborhood, needed for Step 5's slide-range reporting) but
    contributes no text.
  - Budget-fill prioritizes the anchor's own segment over distant neighbors, so a
    tight budget drops far slides before ever dropping the one actually retrieved
    as relevant.
  - Only the single-segment-too-big degenerate case ever hard-truncates mid-slide
    — and only then with an explicit marker.
- `run_context_windows.py` — generic CLI producing `context_windows_report.json`.
- `tests/` — boundary tests (anchor at deck edges, blank-slide handling,
  `ModuleAwareWindow`'s error) + budget-enforcement tests (deterministic, fake
  retriever) + a real integration test against the actual deck/questions/topology.

### Test results — 13/13 pass

### Real-data run — 445 context bundles (36 real topics + 409 noise singletons)

| Metric | Before boilerplate fix | After fix |
|---|---|---|
| Window size (chars): min / mean / max | 380 / 1543.0 / 3927 | 62 / 1429.61 / 3561 |
| Windows truncated at `max_context_chars=4000` | 0 / 445 | 0 / 445 |

Anchor slides for the example topics (270, 30, 138, 36) were **unchanged** by the
DocumentParsing fix — the fix cleaned their content, it didn't change which slide
got retrieved. E.g. topic 9 (technical debt, 51 questions) shrank from 457 → 182
chars once boilerplate was stripped from its window.

**Findings flagged from the real output** (per `claude.md`'s "flag anything that
looks wrong" instruction):
1. ~~Every slide carries repeating garbled boilerplate~~ — **fixed** by the
   DocumentParsing two-pass parse (Section 1b above).
2. **Topic 9 ("technical debt") window may be missing the real content** — the
   anchor (slide 270) and its radius-1 neighbors are mostly just the slide title;
   the actual explanatory content ("A better analogy: Pollution") sits on slide
   272, one slide outside the window. A genuine "Fragmented Context" risk from
   `FixedRadiusWindow`'s fixed radius, not a parsing bug — still open.
3. ~~Topic 6 anchor looked content-free~~ — **not a real issue**, was a
   misreading on my part (slide_number vs. slide_id confusion when reading the
   printed report).

---

## 5. Git history

| Branch | Branched from | Contains |
|---|---|---|
| `semantic-and-document-parsing` | (main) | `gap_detection.parsing` + full `Similiarity_gen` build + `.gitignore` updates; separate commit removing the unused `Confusion_Detection/frontend/` scaffold and superseded `Data/se-u2-slides.pdf` + `Data/u2_questions.txt` (moved into `Similiarity_gen/`) |
| `global-context` | `semantic-and-document-parsing` | Full `Global_Context` build |
| `context-window` | `global-context` | Full `Context_Window` build; the `DocumentParsing` boilerplate fix + its new test suite; regenerated `se-u2-slides.json` and `retrieval_report.json` from the corrected parse; `SEMANTIC_AND_GLOBAL_CONTEXT_SUMMARY.md` |

All three pushed to `origin`.

---

## 6. Open items / next steps

1. **Sign off on the `Global_Context` default** — `HDBSCANClusterer(min_cluster_size=3)`
   + sentence-transformers, per the recommendation in Section 3.
2. **Decide on Context_Window finding #2** — whether `FixedRadiusWindow`'s radius=1
   needs to be widened (or `ModuleAwareWindow` prioritized once module grouping
   exists) to avoid missing content just outside the window.
3. **Optional follow-up:** the non-uniform character-duplication glitch noted in
   Section 1b — different root cause from the banner bug, not yet addressed.
4. **Step 4** — LLM completeness judge per topic cluster (Complete Omission /
   Shallow Coverage / Fragmented Context), plus a concrete `suggested_addition`
   draft. Explicitly replaces the old extractive-QA + missingness-score approach —
   not to be resurrected. `Context_Window`'s `ContextBundle` is its direct input.
5. **Step 5** — severity-scored report grouped Module → Topic → Slide range, using
   each bundle's `source_questions` / `cluster_size` for "backed by N questions" lines.
6. Per-model low-confidence thresholds for `Similarity_gen` (TF-IDF's 0.15 cutoff
   doesn't transfer to sentence-transformers' higher score baseline).
7. Step 1's module grouper was never built (`module_id` is `null` on every slide) —
   blocks `ModuleAwareWindow` and any Module-level grouping in Step 5's report.
