# Gap Detection — Similarity_gen & Global_Context: Build Summary

Covers everything built and run across two work sessions on the Gap
Detection pipeline. Gap Detection has 5 stages total:

| # | Stage | Status |
|---|---|---|
| 1 | Document Structuring (parse deck → slide-level title/bullets/notes, group into modules) | **Done** — `DocumentParsing/gap_detection/parsing/` |
| 2 | Global Question Topology (embed all questions, cluster into topic buckets, distill each to one query) | **Done** — `Global_Context/` |
| 3 | Multi-Context Retrieval (hybrid BM25+dense search per topic, multi-slide context windows) | Partially available — dense single-slide retrieval exists in `Similiarity_gen/` and is reused, not duplicated. BM25 fusion + multi-slide windows not built. |
| 4 | Synthesized Verification (LLM judges completeness per topic, classifies gap type) | Not built |
| 5 | Hierarchical Gap Reporting (severity-scored report, Module → Topic → Slide range) | Not built |

This document covers Stage 1's dependency contract, and the two
folders that got fully built: **Similarity_gen** (the semantic-similarity
engine underlying Steps 2/3) and **Global_Context** (Step 2 itself).

---

## 1. `DocumentParsing/gap_detection/parsing/` — Step 1's output contract

Referenced by both `Similiarity_gen/claude.md` and `Global_Context/claude.md`
as an existing dependency, but not actually present in the repo — built
from scratch to match the JSON shape `DocParsing_1.py` already produces:

- `schema.py` — `Slide` / `DeckDocument` dataclasses. `slide_id` is the
  permanent, 0-indexed identity used everywhere downstream (never
  re-derived from list position). `Slide.is_empty` flags slides with no
  title and no bullets, so downstream code has one place to decide
  whether a slide is worth embedding.
- `storage.py` — `load_deck_json(path) -> DeckDocument` / `save_deck_json`.

---

## 2. `Similiarity_gen/` — semantic-similarity engine (Steps 2/3's base)

**Job:** given a question and a parsed deck, say which slide(s) are most
relevant, with a numeric score. Scoped to embedding + similarity only —
no clustering, BM25, or LLM judgment.

### What was built

| File | Purpose |
|---|---|
| `embedding_models.py` | `EmbeddingModel` ABC + `TfidfEmbeddingModel` (sklearn, no download) + `SentenceTransformerEmbeddingModel` (`all-MiniLM-L6-v2`). Every concrete embedding-library import isolated to this one file. |
| `cache.py` | `EmbeddingCache` — disk cache keyed by `(deck source, model name, content hash)`; editing the deck or swapping models auto-invalidates it. |
| `slide_index.py` | `SlideIndex` — embeds every non-empty slide once per deck; always re-fits the model (cheap) even on a cache hit, so a cached index still works for query-time embedding. |
| `retriever.py` | `QuestionSlideRetriever` + `RetrievalResult` — reads the embedding model **off the `SlideIndex`**, never a separate reference, so a query can't accidentally be embedded in a different vector space than the slides. Supports single and batch retrieval, cosine similarity, configurable `top_k`. |
| `run_retrieval.py` | Generic CLI — given a directory, auto-detects which file is "the deck" (JSON matching the `{"slides":[...]}` shape) and which is "the questions" (`.txt` one-per-line, or a JSON list of strings) by content, not filename. |
| `tests/` | Ground-truth fixture (8 questions across 10 synthetic slides, including a title-only slide and a fully blank slide as edge cases) + numeric test suite. |

### Test results (real models, no mocking) — 12/12 pass

| Model | Top-1 accuracy | Mean top-1 score | Median | Hard-negative score |
|---|---|---|---|---|
| TF-IDF | 100% (8/8) | 0.3854 | 0.3595 | 0.0000 |
| sentence-transformers (all-MiniLM-L6-v2) | 100% (8/8) | 0.6626 | 0.6892 | 0.0852 |

Both models clearly separate the hard-negative question ("What's the
best pizza topping combination?") from real matches — confirms neither
model just scores everything similarly high.

### Real-data run — `se-u2-slides.json` (298 slides) × `u2_questions.txt` (800 questions), top_k=3

| Model | Runtime | Mean top-1 | Median | Min | Max | Questions scoring < 0.15 |
|---|---|---|---|---|---|---|
| TF-IDF | 0.2s | 0.2754 | 0.2517 | 0.0744 | 0.8214 | 93 / 800 |
| sentence-transformers | 3.3s | 0.5893 | 0.5961 | 0.1817 | 0.9013 | 0 / 800 |

Full per-question top-3 results for both models: **`Similiarity_gen/retrieval_report.json`**
(the 93 TF-IDF low-confidence questions are the first candidate list for
Step 4's gap detection — sentence-transformers' higher baseline means
the same fixed threshold doesn't carry over between models, so any
low-confidence cutoff needs to be tuned per model).

---

## 3. `Global_Context/` — Step 2: Global Question Topology

**Job:** collapse 800 raw questions into far fewer topic buckets, each
with one clean representative query — so Step 4 doesn't waste LLM calls
verifying the same concept dozens of times, and Step 5 can report "this
gap is backed by N questions" instead of N separate entries for the same
thing.

### What was built

| File | Purpose |
|---|---|
| `schema.py` | `TopicCluster` (`topic_id`, `representative_query`, `source_questions`, `question_indices`, `is_noise`) + `TopologyResult`. Validates `source_questions`/`question_indices` stay in lockstep, and that every `is_noise` topic has exactly 1 source question. |
| `clustering.py` | `Clusterer` ABC + `HDBSCANClusterer` (default — uses sklearn's built-in `HDBSCAN`, no extra dependency; `-1` = noise, never forced into a bucket) + `KMeansClusterer` (comparison baseline — silhouette-selected k, no noise concept). |
| `distillation.py` | `Distiller` ABC + `CentroidClosestDistiller` — picks the real question nearest the cluster centroid (free, deterministic, guarantees the representative query is something a student actually asked). |
| `topology_builder.py` | `QuestionTopologyBuilder` — the only file importing all three interfaces (`EmbeddingModel` reused from `Similiarity_gen`, `Clusterer`, `Distiller`) together. L2-normalizes embeddings once so clustering and distillation agree on "close." Every `-1` becomes its own singleton `TopicCluster(is_noise=True)` — **never dropped**. |
| `run_topology.py` | Generic CLI, same auto-detection posture as `Similiarity_gen/run_retrieval.py`; `--sweep-min-cluster-size` flag for comparing HDBSCAN thresholds. |
| `tests/` | Ground-truth fixture (4 real topics × 3 phrasings + 2 deliberately unrelated singleton outliers) + the ARI-scored suite. |

### Ground-truth test results — 12/12 pass

Adjusted Rand Index against the 14-question fixture, all four combinations:

| Clusterer | TF-IDF | sentence-transformers |
|---|---|---|
| HDBSCAN | 0.8389 | 0.3368 |
| KMeans | 0.6901 | **0.8967** |

HDBSCAN correctly isolated both outlier questions as singletons in
every run. KMeans has no noise concept, so isolation there is
incidental (checked but not asserted).

### Real-data run — `u2_questions.txt` (800 questions)

**KMeans "won" the tiny fixture, but doesn't hold up on real data.**
Silhouette-selected k=13 on the 800 real questions produces 13 clusters
averaging 62 questions each that mix unrelated concepts (e.g. WBS
questions folded into an "architecture definition" cluster) — see
`Global_Context/topology_report_kmeans_comparison.json`.

**HDBSCAN + sentence-transformers**, `min_cluster_size=3`
(`Global_Context/topology_report.json`):

| Metric | Value |
|---|---|
| Real clusters | 36 |
| Noise singletons | 409 |
| Cluster size (min / median / mean / max) | 3 / 5.0 / 10.86 / 51 |

`min_cluster_size` sweep (Section 6's open design question):

| `min_cluster_size` | Real clusters | Noise | Min / Median / Mean / Max size |
|---|---|---|---|
| 2 | 168 | 296 | 2 / 2.0 / 3.0 / 13 — over-split |
| **3** | **36** | **409** | **3 / 5.0 / 10.86 / 51 — coherent, kept as default** |
| 5 | 12 | 466 | 5 / 24.0 / 27.83 / 62 |
| 8 | 7 | 444 | 11 / 35 / 50.86 / 162 — too coarse, merges distinct topics |

Example real clusters at `min_cluster_size=3` (representative query + sample):

- **Technical debt** (51 questions) — "What is technical debt and how is it analogous to financial debt?"
- **COCOMO** (44 questions) — "How does COCOMO estimation feed into the project schedule?"
- **Microservices architecture** (40 questions) — "What is microservices architecture and how does it contrast with monolithic architecture?"
- **Project triangle / risk management** (33 questions) — "How does risk management support the project management triangle?"
- **Therac-25 / fail-safe design** (30 questions) — "What would a proper 'Fail-Safe' architecture have looked like for the Therac-25?"
- **Cohesion types** (28 questions) — "Explain the different types of cohesion in software modules."

**Recommendation (pending sign-off):** keep `HDBSCANClusterer(min_cluster_size=3)`
+ `SentenceTransformerEmbeddingModel` as the project default, despite its
lower score on the tiny ground-truth fixture — the 14-question fixture
isn't representative of 800 messy real questions, and the real-data
cluster coherence is what actually matters for Step 5's reporting.

---

## 4. Git history

| Branch | Contains |
|---|---|
| `semantic-and-document-parsing` | `gap_detection.parsing` + full `Similiarity_gen` build + `.gitignore` updates (pytest/mypy/ruff caches, venvs, embedding cache, node_modules, editor folders); a separate commit removing the unused `Confusion_Detection/frontend/` scaffold and the superseded `Data/se-u2-slides.pdf` + `Data/u2_questions.txt` (moved into `Similiarity_gen/`). Pushed to `origin`. |
| `global-context` (branched from `semantic-and-document-parsing`) | Full `Global_Context` build described above. Pushed to `origin`. |

---

## 5. Open items / next steps

1. **Sign off on the `Global_Context` default** — `HDBSCANClusterer(min_cluster_size=3)` + sentence-transformers, per the recommendation above (or pick a different sweep value).
2. **Step 3** — hybrid BM25 + dense retrieval per topic bucket, with multi-slide context windows (dense single-slide half already reusable from `Similiarity_gen`).
3. **Step 4** — LLM completeness judge per topic cluster (Complete Omission / Shallow Coverage / Fragmented Context). Explicitly replaces the old extractive-QA + missingness-score approach — not to be resurrected.
4. **Step 5** — severity-scored report grouped Module → Topic → Slide range, using each `TopicCluster`'s `source_questions`/`question_indices` for "backed by N questions" lines.
5. Per-model low-confidence thresholds for `Similarity_gen` (TF-IDF's 0.15 cutoff doesn't transfer to sentence-transformers' higher score baseline).
