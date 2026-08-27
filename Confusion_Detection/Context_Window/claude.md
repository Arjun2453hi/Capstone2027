# CLAUDE.md — Context_Window

Context for any Claude session (chat or Claude Code) working inside
`Context_Window/`. Read this before touching any file here.

---

## 1. Where this fits in the larger project

Gap Detection system, 5 stages total:

1. **Document Structuring** — parse deck into slide-level title/bullets.
   **Partially done** — `DocumentParsing/gap_detection/parsing/` gives
   title/bullets/slide_id. **Module grouping was never built** —
   `module_id` is `null` on every slide. See Section 6 — this folder
   works around that gap for now, it doesn't fix it.
2. **Global Question Topology** — embed all questions, cluster into
   topic buckets, distill each into a representative query. **Done —
   `Global_Context/`.**
3. **Multi-Context Retrieval** — retrieval half (dense similarity,
   single best slide per query) already exists in `Similiarity_gen/`.
   **This folder builds the other half**: turning one retrieved slide
   into a multi-slide context window, so the LLM judge in Step 4 can
   see content spread across adjacent slides, not just one slide in
   isolation.
4. **Synthesized Verification** — an LLM looks at (topic + context
   window) and produces a judgment: gap type, which slide_id(s) are
   responsible, guidance on what's missing, and — when confident enough
   — a concrete suggested addition (a draft bullet/explanation). Not
   built. This folder's output is its direct input.
5. **Hierarchical Gap Reporting** — aggregates Step 4's per-topic
   verdicts into the actual deliverable: a report grouped by Module →
   Topic → Slide range with prioritized, actionable revision
   recommendations. Not built.

**The actual end goal of the whole project** (confirmed): not just a
diagnostic gap report, but *slide revision suggestions* — Step 4/5 are
being designed around producing something an instructor can act on
directly, not just a list of what's wrong.

## 2. Why this folder exists (the specific problem it solves)

`Similiarity_gen`'s retriever answers "which single slide is closest to
this question?" That's not enough for Step 4 to do its job well,
because:

- A concept can be **split across 2-3 adjacent slides** (intro slide +
  detail slide + example slide). Looking at only the best-matching one
  might look like a "Complete Omission" when it's actually explained
  one slide over — a **"Fragmented Context"** false positive.
- Conversely, a single retrieved slide with no surrounding context
  gives the LLM less to work with when drafting a concrete suggested
  addition (Step 4's `suggested_addition` field) — more context means
  a more grounded, specific suggestion instead of a generic one.

This folder's job, narrowly: given a topic's representative query, get
the anchor slide from `Similiarity_gen`'s retriever, then assemble a
window of that slide plus its neighbors, with the raw text stitched
together in slide order. It does **not** do any LLM judgment — that's
Step 4, strictly downstream of this.

## 3. Non-negotiable design requirements

Same DI posture as the previous two folders.

- **`ContextWindowStrategy`** (`window_strategy.py`) — abstract
  interface: `build_window(anchor_slide_id, deck) -> List[int]` (returns
  an ordered list of slide_ids to include, always containing the
  anchor).
  - **`FixedRadiusWindow`** (default, implement now) — anchor ± N
    adjacent slide_ids (N configurable, default 1), clamped to the
    deck's actual slide_id range (no wraparound, no out-of-bounds).
  - **`ModuleAwareWindow`** (interface only, do NOT implement yet) —
    would expand to the anchor's full module boundary once `module_id`
    is actually populated. Leave a stub or comment noting this is
    blocked on Step 1's module grouper, not on anything in this folder.
- **`ContextWindowBuilder`** (`builder.py`) — the orchestrator. Takes an
  injected `QuestionSlideRetriever` (or the underlying `SlideIndex`,
  whichever `Similiarity_gen`'s actual retriever interface is — check
  `Similiarity_gen/retriever.py` before assuming) and an injected
  `ContextWindowStrategy`. For each `TopicCluster` from
  `Global_Context`'s `TopologyResult`, it:
  1. Retrieves the anchor slide_id for `representative_query`.
  2. Expands it into a window via the injected strategy.
  3. Assembles `window_text` — the window's slides' `raw_text`, joined
     in slide_id order, each prefixed with its slide_number so the LLM
     judge in Step 4 can cite which slide it's talking about.
  4. **Enforces a token/char budget** (see below) — truncate or shrink
     the window rather than silently handing Step 4's LLM more text
     than its context window can hold.
- **Token budget matters here — don't skip it.** Whatever local model
  ends up doing Step 4's judgment almost certainly has a real context
  limit (likely small, given the "free and lightweight" constraint on
  this project). `ContextWindowBuilder` should accept a
  `max_context_chars` (or token count, if a tokenizer is cheaply
  available) and clip the window content deterministically —
  documented and tested, not a silent truncation that could cut
  mid-sentence without anyone noticing.
- **Output schema** (`schema.py`) — a `ContextBundle` per topic:
  ```python
  @dataclass
  class ContextBundle:
      topic_id: int
      representative_query: str
      anchor_slide_id: int
      window_slide_ids: List[int]      # ordered, includes anchor
      window_text: str                 # assembled, budget-enforced
      source_questions: List[str]      # carried through from TopicCluster
      cluster_size: int                # carried through from TopicCluster
  ```
  This is Step 4's actual input — don't drop `source_questions` or
  `cluster_size`, Step 5 needs them for severity scoring and "backed by
  N questions."

## 4. Testing philosophy — numeric, not just pass/fail

Same standard as the previous two folders.

- **Window boundary tests** (deterministic, no model needed):
  - Anchor in the middle of a deck: window = expected `[anchor-N ..
    anchor+N]`.
  - Anchor at slide_id 0: window must not include negative slide_ids.
  - Anchor at the last slide_id: window must not run past the deck's
    max slide_id.
  - A blank/empty slide inside the window range: decide and document
    whether it's included as empty text or skipped — write the test
    either way, don't leave this undefined.
- **Budget enforcement test**: construct a case where the assembled
  window text exceeds `max_context_chars`, assert the output is
  actually clipped to the budget, and print the before/after character
  counts.
- **Integration test against real output**: run `ContextWindowBuilder`
  on `Global_Context`'s real topic clusters (from `u2_questions.txt`)
  and `Similiarity_gen`'s real slide index (`se-u2-slides.json`).
  Report, numerically: average / min / max window size in slides and
  in characters, how many windows needed truncation, and print 3-4
  example `ContextBundle`s in full so a human can sanity-check the
  assembled text actually reads coherently in slide order.
- If a real assertion fails, fix the code or flag the ambiguity back —
  don't loosen the assertion to make it pass.

## 5. File structure

```
Context_Window/
  __init__.py
  window_strategy.py     # ContextWindowStrategy ABC + FixedRadiusWindow (+ ModuleAwareWindow stub)
  schema.py                 # ContextBundle
  builder.py                  # ContextWindowBuilder orchestrator
tests/
  test_window_strategy.py       # boundary tests (Section 4, bullet 1)
  test_builder.py                  # budget enforcement + integration (Section 4, bullets 2-3)
```

Nothing in this folder exists yet — this is a from-scratch build, not a
review of drafted files (unlike `Similiarity_gen`/`Global_Context`).

## 6. Known open design question

`module_id` is `null` on every slide because Step 1's module grouper
was never built. `FixedRadiusWindow` is a real, usable strategy on its
own — don't block this folder on the module grouper — but flag clearly
in `builder.py`'s docstring that `ModuleAwareWindow` is the intended
eventual default once module grouping exists, and that fixed-radius
windows may occasionally include an irrelevant neighbor slide or miss
a relevant one outside the radius, especially near module boundaries.

## 7. Prompt to run (paste this in)

```
Build Context_Window per CLAUDE.md in this directory.

Specifically:
1. First inspect Similiarity_gen/retriever.py to confirm the exact
   interface QuestionSlideRetriever exposes (method name, return type)
   — don't assume, since ContextWindowBuilder depends on it directly.
2. Write window_strategy.py: ContextWindowStrategy ABC, FixedRadiusWindow
   (default radius=1, clamped to deck bounds, no wraparound), and a
   ModuleAwareWindow stub per Section 3/6 (interface only, raise
   NotImplementedError with a clear message referencing the module
   grouping gap).
3. Write schema.py: ContextBundle per Section 3.
4. Write builder.py: ContextWindowBuilder per Section 3, including the
   max_context_chars budget enforcement.
5. Write the full test suite per Section 4, including the deck-boundary
   edge cases and the budget enforcement test with printed before/after
   character counts.
6. Run ContextWindowBuilder against the real Global_Context topic
   output and Similiarity_gen slide index (se-u2-slides.json /
   u2_questions.txt) and report: average/min/max window size (slides
   and chars), how many windows were truncated, and print 3-4 full
   example ContextBundles.
7. Flag anything that looks wrong in the example bundles (e.g. windows
   that don't read coherently, or an anchor slide that seems like a bad
   retrieval match) before we move to Step 4.
```