# CLAUDE.md — Gap_Verification

Context for any Claude session (chat or Claude Code) working inside
`Gap_Verification/`. Read this before touching any file here.

---

## 1. Where this fits — current real pipeline status

| # | Stage | Status |
|---|---|---|
| 1 | Document Structuring | Parsing done + boilerplate bug fixed (see Section 3). Module grouping NOT built — `module_id` is `null` on every slide. |
| 2 | Global Question Topology | **Done** — `Global_Context/`. Default: `HDBSCANClusterer(min_cluster_size=3)` + sentence-transformers → 36 real clusters + 409 noise singletons = 445 topics. (Sign-off on this default still technically pending, but treat it as current truth for this build.) |
| 3 | Multi-Context Retrieval | **Done** — dense half in `Similiarity_gen/`, window-assembly half in `Context_Window/`. Known open risk: see Section 3. |
| 4 | Synthesized Verification | **This folder. Not built yet — first real build.** |
| 5 | Hierarchical Gap Reporting | Not built. Consumes this folder's output directly. |

**Confirmed end goal of the whole project:** not a diagnostic list —
concrete, actionable *slide revision suggestions*. This folder is where
that judgment actually gets made.

## 2. What this folder consumes: `ContextBundle`

From `Context_Window/schema.py`, one per topic (445 total on the real
dataset):
```python
ContextBundle(
    topic_id: int,
    representative_query: str,
    anchor_slide_id: int,
    window_slide_ids: List[int],
    window_text: str,             # budget-enforced, slide-ordered, slide-number-prefixed
    source_questions: List[str],
    cluster_size: int,
    is_noise: bool,                # True for 409 of 445 — singleton, backed by 1 question
)
```

## 3. Known data-quality caveats to design around — read before writing the prompt

These aren't hypothetical — they were found and documented while
building the folders this one depends on:

- **`FixedRadiusWindow` can genuinely miss the real content.** Example
  found in real output: the "technical debt" topic (51 backing
  questions) retrieved slide 270 as its anchor, but the actual
  explanatory content ("A better analogy: Pollution") sits on slide
  272 — one slide outside the radius-1 window. This means a
  `window_text` that looks like "just a title, no real explanation" is
  **ambiguous between two very different truths**: the deck genuinely
  never explains it (`complete_omission`), or the explanation exists
  just outside what this bundle happened to include (a windowing
  limitation, not a deck gap). **Design implication: when
  `window_text` looks unusually thin relative to a topic backed by many
  questions, the classifier/generator should lean toward lower
  `confidence` rather than confidently calling `complete_omission`.**
  Don't silently treat window content as ground truth about the whole
  deck — it's a sample of the deck, occasionally an incomplete one.
- **A separate, still-unfixed parsing glitch** can produce
  non-uniformly garbled text on some slides (character-duplication
  artifacts like `"DDIeeSppFaaCrrRttmm Eeexnnettc"`) — different root
  cause from the (already-fixed) boilerplate bug, not yet addressed
  upstream. `window_text` may occasionally contain this. **The
  classifier/generator must not crash on it**, and ideally should
  reflect it in lower confidence rather than confidently classifying
  off garbled input as if it were clean.
- **409 of 445 topics are singletons** (`is_noise=True`, `cluster_size=1`).
  These are real signals (never dropped, per `Global_Context`'s rule)
  but carry far less weight than a 51-question cluster. Don't treat all
  445 as equally worth the same verification effort by default — see
  Section 6.

## 4. Non-negotiable design requirements

Same DI posture as every prior module — two independent interfaces,
composed by an orchestrator that owns neither concrete implementation.

- **`GapClassifier`** (`classifier.py`) —
  `classify(query: str, context_text: str) -> ClassificationResult`
  returning `gap_type ∈ {complete_omission, shallow_coverage,
  fragmented_context, covered}` **and a genuine confidence score from
  the model itself** (e.g. a zero-shot NLI classifier's label
  probability), not an invented heuristic layered on top.
- **`GapContentGenerator`** (`generator.py`) —
  `generate(query, context_text, gap_type, source_questions) ->
  GeneratedContent` returning `guidance: str` (always) and
  `suggested_addition: Optional[str]` (nullable — `null` when the
  generator isn't confident enough to draft real content, or when its
  raw output is degenerate: empty, a refusal, or an echo of the
  prompt). Only called when `gap_type != "covered"`.
- **Why two models, not one** (already decided, carry this forward):
  classification and open-ended generation are different skills, and
  the earlier version of this project already saw a single small
  generative model (FLAN-T5) produce unreliable "mushy" output when
  asked to also hold a structured classification together. Splitting
  lets the classifier's real label-probability become `confidence`
  directly, and lets the generator focus on one narrower job,
  conditioned on an already-decided `gap_type`.
- **`GapVerifier`** (`verifier.py`) — the orchestrator, depends on both
  interfaces only, produces the final `GapVerdict` schema:
  ```python
  @dataclass
  class GapVerdict:
      topic_id: int
      gap_type: str
      slide_ids: List[int]          # = bundle.window_slide_ids, carried through
      guidance: str
      suggested_addition: Optional[str]
      confidence: float             # or a bucketed label if that reads better downstream — pick one, be consistent
      backed_by_questions: int      # = bundle.cluster_size
      is_noise: bool                # carried through — Step 5 needs this for weighting
  ```
- **Carry `cluster_size` and `is_noise` straight through unchanged** —
  Step 5's severity scoring and "backed by N questions" line need them,
  and this folder must not be the place traceability finally breaks.

## 5. Testing philosophy — numeric, using the REAL bundles already generated

Same standard as every prior module. Since `context_windows_report.json`
already exists with 445 real bundles, use it directly rather than only
synthetic fixtures:

- Hand-label a small ground-truth set **pulled from real bundles**,
  including deliberately: one clearly `covered` topic, one clear
  `complete_omission`, one clear `shallow_coverage`, and **the
  "technical debt" topic specifically** as the hard
  `fragmented_context`-vs-`complete_omission` ambiguity case described
  in Section 3 — this is the single most informative test case
  available, since its correct behavior (probably lower confidence,
  arguably `fragmented_context` rather than `complete_omission`) is
  already known from manual inspection.
- Report classifier accuracy + full confusion matrix against that set.
- Spot-check `guidance`/`suggested_addition` by eye on a sample —
  budget for manual review, this half isn't fully automatable.
- Explicitly test graceful handling of garbled `window_text` (construct
  or find a real example) — assert no crash, and check whether
  confidence drops appropriately.
- Run against all 445 real bundles and report: gap_type distribution,
  mean confidence for clusters vs. singletons, how many
  `suggested_addition`s came back non-null vs. `null`.

## 6. Open design question to settle before/while building

**Should the 409 singleton topics get a lighter-weight or skipped
verification pass relative to the 36 real clusters?** They're backed by
only 1 question each and will carry low severity weight in Step 5
regardless of verdict. Options: verify all 445 the same way (simplest,
highest cost); skip singletons entirely and only report them if a
human wants the raw list; or run singletons through the classifier only
(skip the generator's content-drafting step, since drafting content for
a single unconfirmed question is lower-value). No default has been
picked — decide and document the choice in `verifier.py`'s docstring,
and expose it as a mode on `run_verification.py` (mirroring
`Global_Context`'s `--sweep-min-cluster-size` pattern), e.g.
`--singleton-mode {full,classify-only,skip}`.

## 7. File structure

```
Gap_Verification/
  __init__.py
  classifier.py               # GapClassifier ABC + a zero-shot NLI implementation
  generator.py                   # GapContentGenerator ABC + a small generator implementation
  schema.py                         # ClassificationResult, GeneratedContent, GapVerdict
  verifier.py                          # GapVerifier orchestrator
  run_verification.py                     # generic CLI, auto-detects context_windows_report.json
tests/
  fixtures/
    ground_truth_verdicts.py    # hand-labeled real bundles, incl. the technical-debt case
  test_classifier.py
  test_generator.py
  test_verifier.py
```

Nothing in this folder exists yet — from-scratch build.

## 8. Prompt to run (paste this in)

```
Build Gap_Verification per CLAUDE.md in this directory.

Specifically:
1. Load context_windows_report.json from Context_Window and pick 5-6
   real ContextBundles for a hand-labeled ground-truth set per Section
   5 — including the "technical debt" topic specifically as the hard
   fragmented-context-vs-complete-omission case.
2. Write schema.py: ClassificationResult, GeneratedContent, GapVerdict
   per Section 4.
3. Write classifier.py: GapClassifier ABC + a zero-shot NLI
   implementation (e.g. facebook/bart-large-mnli or a lighter distilled
   variant) with the 4 candidate gap_type labels, returning the model's
   own label probability as confidence.
4. Write generator.py: GapContentGenerator ABC + a small generator
   (e.g. FLAN-T5) that produces guidance + suggested_addition
   conditioned on gap_type, with degenerate-output detection so
   suggested_addition is null rather than forced.
5. Write verifier.py: GapVerifier orchestrator per Section 4, carrying
   cluster_size/is_noise through unchanged. Pick and document a default
   for the Section 6 singleton-handling question, expose it as
   --singleton-mode on run_verification.py.
6. Write the test suite per Section 5, including the garbled-text
   graceful-handling test.
7. Run against all 445 real bundles and report: classifier accuracy +
   confusion matrix on the ground-truth set, gap_type distribution
   across all 445, mean confidence for clusters vs singletons, and the
   actual classifier/generator output for the technical-debt topic
   specifically — I want to see whether it correctly reflects the
   windowing ambiguity rather than confidently calling it a clean
   complete_omission.
```