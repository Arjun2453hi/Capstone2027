# CLAUDE.md — Global_Context

Context for any Claude session (chat or Claude Code) working inside
`Global_Context/`. Read this before touching any file here.

---

## 1. Where this fits in the larger project

Gap Detection system, 5 stages total:

1. **Document Structuring** — parse deck into slide-level title/bullets,
   group into modules. **Done — `DocumentParsing/gap_detection/parsing/`.**
2. **Global Question Topology** — embed all questions, cluster into
   topic buckets, distill each into one representative query.
   **This folder. In progress.**
3. **Multi-Context Retrieval** — hybrid (BM25 + dense) search per topic
   bucket, multi-slide context windows. Retrieval half (dense,
   single-slide) already exists in `Similiarity_gen/` and is reused
   here, not duplicated.
4. **Synthesized Verification** — LLM judges completeness per topic
   cluster, classifies gap type (Complete Omission / Shallow Coverage /
   Fragmented Context). Not built.
5. **Hierarchical Gap Reporting** — severity-scored report grouped by
   Module → Topic → Slide range. Not built.

**Why this stage exists:** feeding all 800 raw questions individually
into Step 4 is wrong for two reasons — (a) massive redundancy, since
real questions repeat constantly in different phrasings, and Step 5
needs "backed by N questions" per gap, not N separate gap entries for
the same thing; (b) it wastes LLM calls verifying the same concept
dozens of times. This folder collapses 800 questions into a much
smaller number of topic buckets, each with one clean query to actually
retrieve and verify against.

**What replaced (do not resurrect):** the original script's extractive
QA + missingness-score approach (`p_null × R × (1-p_ans)`) is dead —
not part of any later stage either. Step 4's LLM judge replaces it
entirely with a direct completeness/gap-type judgment.

## 2. Inputs this folder consumes

- Plain list of question strings (from `u2_questions.txt` or similar —
  one question per line). This folder does not touch the deck JSON at
  all; slide-side logic lives entirely in `Similiarity_gen/`.
- `EmbeddingModel` interface from `Similiarity_gen/embedding_models.py`
  — **reused, not duplicated**. `TfidfEmbeddingModel` and
  `SentenceTransformerEmbeddingModel` both already exist there and work
  unchanged for embedding questions (same interface, different input).

## 3. Non-negotiable design requirements

Same dependency-injection posture as `Similiarity_gen/`: three
independent, swappable interfaces.

- **`Clusterer`** (`clustering.py`) — `fit_predict(embeddings) ->
  labels`. `-1` means "no cluster assigned."
  - `HDBSCANClusterer` (default) — doesn't require guessing the number
    of topics up front, and explicitly supports "this question doesn't
    belong anywhere" via `-1` instead of forcing every question into
    the nearest bucket regardless of fit.
  - `KMeansClusterer` (comparison baseline) — silhouette-selected k,
    forces every point into a cluster, no noise concept.
- **Critical rule: `-1` / noise labels must never be dropped.** An
  unclustered question can still be a real, individually meaningful
  signal for gap detection. `topology_builder.py` turns every `-1`
  point into its own singleton `TopicCluster` (`is_noise=True`) rather
  than discarding it. Don't "clean up" noise points by filtering them
  out — that's a traceability bug, not a simplification.
- **`Distiller`** (`distillation.py`) — `distill(questions, embeddings)
  -> str`. `CentroidClosestDistiller` (default, implemented) picks the
  actual question nearest the cluster centroid — free, deterministic,
  and guarantees the representative query is something a real student
  actually asked. An LLM-paraphrase distiller can be added later behind
  the same interface; don't build it now unless asked.
- **`TopicCluster` / `TopologyResult`** (`schema.py`) — every topic
  keeps `source_questions` (full text) and `question_indices` (back to
  the original input list). Never lossy-collapse this — Step 5's
  severity scoring and "backed by N questions" line both depend on it.
- **`QuestionTopologyBuilder`** (`topology_builder.py`) — the only file
  that imports all three interfaces together. Depends on
  `EmbeddingModel`, `Clusterer`, `Distiller` — never a concrete class
  directly. Swapping any one of the three should require touching only
  that one file plus the constructor call site, nothing here.

## 4. Testing philosophy — numeric, not just pass/fail

Same standard as `Similiarity_gen/`: real numbers, real models,
internet available, no mocking a model that can actually run.

- **Ground-truth fixture already drafted**:
  `tests/fixtures/ground_truth_topics.py` — 14 questions: 4 true topics
  of 3 phrasings each, plus 2 deliberately unrelated singleton
  outliers. Extend it if more variety is needed, but keep the singleton
  outliers — they're the test that noise handling actually works.
- **Score clustering quality with Adjusted Rand Index**
  (`sklearn.metrics.adjusted_rand_score`) against the ground-truth
  labels — a real number showing how well predicted clusters match the
  true topic groupings, not just "did it run." Print the ARI score.
- **Run the same test against both `HDBSCANClusterer` and
  `KMeansClusterer`**, and against both `TfidfEmbeddingModel` and
  `SentenceTransformerEmbeddingModel` — report ARI for each
  combination. This is the actual evidence of which combination to use
  as the project default, not a guess.
- **Explicitly assert the two outlier questions never get merged into
  one of the 4 real clusters**, and print whether each landed as a
  singleton (`is_noise=True`) as expected.
- **Assert every `representative_query` is a real member of its own
  `source_questions`** (guaranteed by centroid-closest, but verify it —
  this is what makes the distillation trustworthy, not just fast).
- **Print the full cluster assignment table** (question -> predicted
  topic_id) for manual inspection, not just the aggregate score.
- Then **run against the real data** (`u2_questions.txt`, 800
  questions, no ground truth available there) and report: number of
  real clusters found, number of singleton/noise topics, cluster size
  distribution (min/median/max), and a handful of example clusters with
  their representative query + 2-3 source questions, so a human can
  sanity-check the topics actually make sense.
- If a real assertion fails, fix the code or flag the ambiguity back —
  don't loosen the assertion to make it pass.

## 5. File structure (current state)

```
Global_Context/
  __init__.py
  clustering.py         # Clusterer ABC + HDBSCANClusterer + KMeansClusterer  [drafted]
  distillation.py         # Distiller ABC + CentroidClosestDistiller           [drafted]
  schema.py                 # TopicCluster + TopologyResult                    [drafted]
  topology_builder.py         # QuestionTopologyBuilder orchestrator           [drafted]
tests/
  fixtures/
    ground_truth_topics.py      # 14 questions, 4 topics + 2 outliers          [drafted]
  test_clustering.py               # NOT YET WRITTEN
  test_distillation.py               # NOT YET WRITTEN
  test_topology_builder.py             # NOT YET WRITTEN — the ARI-scored numeric suite
```

Everything marked `[drafted]` was written without internet access in
the planning sandbox and has NOT been run against a real embedding
model yet — treat it as a strong starting point, not finished code.
Nothing marked "NOT YET WRITTEN" exists at all.

## 6. Known open design question

`min_cluster_size` in `HDBSCANClusterer` (currently defaults to 3) is
an untuned guess. It should be tuned against the real 800-question set
once the test suite is running — too low produces spurious tiny
clusters, too high pushes real small topics into singletons. Report
the cluster-size distribution at a couple of different values before
picking a final default.

---

## 7. Prompt to run (paste this in)

```
Build out Global_Context per CLAUDE.md in this directory.

Specifically:
1. Review the existing drafted files (clustering.py, distillation.py,
   schema.py, topology_builder.py, tests/fixtures/ground_truth_topics.py)
   against Section 3 — fix anything that violates the DI rules or the
   "never drop noise points" rule.
2. Write test_clustering.py, test_distillation.py, and
   test_topology_builder.py per Section 4. test_topology_builder.py is
   the important one: it must compute and print Adjusted Rand Index
   against the ground-truth labels, for all four combinations of
   {HDBSCANClusterer, KMeansClusterer} x {TfidfEmbeddingModel,
   SentenceTransformerEmbeddingModel} (install sentence-transformers,
   actually run it, don't mock it).
3. Run the suite and show me the ARI numbers for all four combinations,
   plus the full cluster assignment table.
4. Then run QuestionTopologyBuilder against the real u2_questions.txt
   (800 questions) using whichever combination had the best ARI, and
   report: number of real clusters, number of singleton topics, cluster
   size distribution, and 5-6 example clusters with their
   representative query + a few source questions each — so I can
   sanity-check the topics before we move to Step 3's context windows.
5. If min_cluster_size=3 produces obviously bad results on the real
   data (too many tiny clusters or too many singletons), try 2-3 other
   values and report the cluster-size distribution for each so we can
   pick a default together.
```

