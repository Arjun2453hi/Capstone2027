# CLAUDE.md — Gap_Reporting

Context for any Claude session (chat or Claude Code) working inside
`Gap_Reporting/`. Read this before touching any file here.

---

## 1. Where this fits — current real pipeline status

| # | Stage | Status |
|---|---|---|
| 1 | Document Structuring | Parsing done + boilerplate bug fixed. Module grouping NOT built — `module_id` is `null` on every slide. **This folder must degrade gracefully without it, not crash or block on it.** |
| 2 | Global Question Topology | Done — 36 real clusters + 409 noise singletons = 445 topics |
| 3 | Multi-Context Retrieval | Done — dense retrieval + multi-slide context windows |
| 4 | Synthesized Verification | Documented, not yet built (`Gap_Verification/`) — this folder's direct input |
| 5 | Hierarchical Gap Reporting | **This folder. Not built — final stage of the 5-step spec.** |

**This is the last stage of the originally-specified 5-step pipeline.**
A separate open question (unresolved as of this writing) is whether a
Step 6 gets added later — auto-generating a *revised deck* with
suggestions physically inserted, rather than a human applying them by
hand. **This folder's job is unaffected by that decision either way**:
it produces the report; if Step 6 is ever built, this folder's output
becomes *its* input. Don't build deck-editing logic here.

**Confirmed end goal:** actionable slide revision suggestions, not a
diagnostic list. Every design choice below is in service of "a human
can read this and know exactly what to fix and why."

## 2. What this folder consumes: `GapVerdict`

From `Gap_Verification/schema.py`, one per topic verified (up to 445 on
the real dataset, fewer if singleton-mode skips some — see that
folder's Section 6):
```python
GapVerdict(
    topic_id: int,
    gap_type: str,              # complete_omission | shallow_coverage | fragmented_context | covered
    slide_ids: List[int],
    guidance: str,
    suggested_addition: Optional[str],
    confidence: float,
    backed_by_questions: int,   # = cluster_size
    is_noise: bool,             # True for singleton topics
)
```
Also needs read access to the parsed deck (`DeckDocument` from
`DocumentParsing`) to resolve `slide_ids` into human-readable slide
numbers/ranges and (when available) `module_id`.

**`Gap_Verification` doesn't exist yet.** Build and test this folder
against a hand-authored fixture set of `GapVerdict`s that mimics the
real distribution (mostly `shallow_coverage`/`complete_omission` for
the 36 real clusters, a long tail of low-confidence singleton verdicts)
— then re-validate against real `GapVerdict` output once that folder
is built.

## 3. Non-negotiable design requirements

Same DI posture as every prior module.

- **`SeverityScorer`** (`severity.py`) — abstract:
  `score(verdict: GapVerdict) -> float`.
  - `DefaultSeverityScorer` (implement now) — explicit, documented
    formula, not an implicit ordering buried in sort logic:
    ```python
    GAP_TYPE_WEIGHT = {
        "complete_omission": 3.0,
        "fragmented_context": 2.0,
        "shallow_coverage": 1.0,
        "covered": 0.0,
    }
    severity = GAP_TYPE_WEIGHT[gap_type] * log1p(backed_by_questions) * confidence
    ```
    Rationale: gap_type sets the base severity tier; `log1p` on
    `backed_by_questions` means a 51-question cluster outranks a
    3-question one but doesn't drown out a smaller, more severe gap
    entirely; multiplying by `confidence` means the ambiguous
    windowing cases flagged in `Gap_Verification` (Section 3 there —
    e.g. "technical debt") naturally rank lower rather than
    overstating certainty. **This is a first version — treat it as
    tunable, not final, and say so in the docstring.**
  - `covered` verdicts always score `0.0` and are excluded from the
    report entirely (see Section 4) — they're not gaps.
- **`SlideRangeAggregator`** (`aggregation.py`) — abstract:
  `aggregate(verdicts: List[GapVerdict]) -> List[ReportEntry]`.
  - `SimpleAggregator` (implement now, v1) — one `ReportEntry` per
    non-covered verdict, no merging of overlapping/adjacent slide
    ranges across different topics. **Known limitation, document it**:
    if two different topics both implicate slide 12, they'll appear as
    two separate entries rather than one merged "slide 12" entry — an
    intentional simplification for v1, not an oversight. A future
    `MergingAggregator` could combine them; don't build it now unless
    asked.
- **`ReportRenderer`** (`renderers.py`) — abstract:
  `render(report: GapReport) -> str`.
  - **`JSONRenderer`** (implement now) — canonical, complete,
    machine-readable output. This is the source of truth; every entry,
    including every low-confidence singleton, always appears here.
  - **`MarkdownRenderer`** (implement now) — the human-facing report.
    Grouped by Module → Slide Range (Module level collapses to a
    single "Ungrouped" section when `module_id` is `null` everywhere —
    detect this and note it plainly in the rendered report's header
    rather than silently omitting the level), sorted by severity
    descending. **Don't dump all 409 singleton entries into the
    human-readable report by default** — show the top-N by severity (or
    those above some severity floor), with a closing line like "N
    additional low-confidence single-question findings recorded in
    gap_report.json." The JSON stays complete regardless; only the
    human-readable rendering is curated. Pick a concrete N/threshold
    and document why.
  - A future `DocxRenderer` (using the project's `docx` skill) could be
    added later behind the same interface if a Word deliverable is
    wanted — don't build it now unless asked.
- **`ReportBuilder`** (`report_builder.py`) — orchestrator, depends on
  `SeverityScorer` + `SlideRangeAggregator` + a `DeckDocument`, never a
  concrete scorer/aggregator. Produces the final:
  ```python
  @dataclass
  class ReportEntry:
      slide_ids: List[int]
      slide_range_label: str      # e.g. "Slides 11-13", from DeckDocument's slide_number
      module_id: Optional[int]    # None when module grouping unavailable
      topic_id: int
      gap_type: str
      severity: float
      guidance: str
      suggested_addition: Optional[str]
      backed_by_questions: int
      confidence: float
      is_noise: bool

  @dataclass
  class GapReport:
      generated_at: str
      total_topics_considered: int
      total_gaps_reported: int         # excludes "covered"
      module_grouping_available: bool  # False until Step 1's grouper exists
      entries: List[ReportEntry]       # sorted by severity, descending
  ```

## 4. Testing philosophy — numeric, using a realistic fixture

`Gap_Verification` doesn't exist yet, so build the fixture by hand:

- `tests/fixtures/sample_verdicts.py` — hand-authored `GapVerdict`
  list mimicking the real shape: a handful of `complete_omission`/
  `shallow_coverage`/`fragmented_context` verdicts with varying
  `backed_by_questions` (mirror real cluster sizes — e.g. one at 51,
  one at 15, one at 5), a couple of `covered` verdicts (must be
  excluded from output), and a long tail of low-confidence singleton
  verdicts (`is_noise=True`, `backed_by_questions=1`).
- **Severity ordering tests**: assert a `complete_omission` with modest
  backing outranks a `shallow_coverage` with heavier backing where the
  formula says it should, and vice versa where it shouldn't — pin down
  actual expected orderings from the formula, don't just assert "some
  order exists."
- **`covered` exclusion test**: assert no `covered` verdict appears
  anywhere in `GapReport.entries`.
- **Module-fallback test**: all verdicts' slides have `module_id=None`
  → `module_grouping_available=False`, Markdown output clearly states
  module grouping wasn't available, and nothing crashes.
- **Singleton-curation test**: with a fixture containing far more
  singletons than the display threshold, assert the Markdown output
  is capped and includes the "N additional findings in gap_report.json"
  line, while the JSON output still contains every entry.
- **Slide-range label test**: verify `ReportEntry.slide_range_label`
  correctly resolves `slide_ids` to human-readable slide numbers via
  the real `DeckDocument`, not the internal 0-indexed `slide_id`.
- Once `Gap_Verification` exists: re-run this folder against its real
  output and report the actual generated `gap_report.md` — read it
  end to end and confirm it's genuinely usable by a human, not just
  structurally correct.

## 5. Open design questions to settle before/while building

1. **Severity formula** (Section 3) is a documented first draft, not
   final — flag for review once real `GapVerdict` data exists.
2. **Singleton display threshold** in `MarkdownRenderer` — pick a
   concrete number/severity floor now, revisit once real data shows
   how many singletons actually score high enough to matter.
3. **Slide-range merging** — `SimpleAggregator`'s no-merge limitation
   (Section 3) may prove annoying in practice once real overlapping
   topics show up; don't build the merging version preemptively.
4. **Output format beyond Markdown/JSON** (Word doc via the `docx`
   skill) — deferred until explicitly requested.

## 6. File structure

```
Gap_Reporting/
  __init__.py
  severity.py             # SeverityScorer ABC + DefaultSeverityScorer
  aggregation.py             # SlideRangeAggregator ABC + SimpleAggregator
  schema.py                     # ReportEntry, GapReport
  renderers.py                     # ReportRenderer ABC + JSONRenderer + MarkdownRenderer
  report_builder.py                   # ReportBuilder orchestrator
  run_reporting.py                       # generic CLI, auto-detects verdict input
tests/
  fixtures/
    sample_verdicts.py      # hand-authored GapVerdicts, see Section 4
  test_severity.py
  test_aggregation.py
  test_renderers.py
  test_report_builder.py
```

Nothing in this folder exists yet — from-scratch build.

## 7. Prompt to run (paste this in)

```
Build Gap_Reporting per CLAUDE.md in this directory.

Specifically:
1. Write tests/fixtures/sample_verdicts.py: a hand-authored, realistic
   set of GapVerdicts per Section 4 — varied gap_types, backed_by_questions
   spanning small to large clusters, at least 2 "covered" verdicts, and
   a long tail of low-confidence singleton verdicts.
2. Write schema.py: ReportEntry, GapReport per Section 3.
3. Write severity.py: SeverityScorer ABC + DefaultSeverityScorer with
   the exact formula in Section 3, documented as a tunable first draft.
4. Write aggregation.py: SlideRangeAggregator ABC + SimpleAggregator
   (v1, no merging — document the limitation).
5. Write renderers.py: ReportRenderer ABC + JSONRenderer (complete,
   canonical) + MarkdownRenderer (curated: Module→Slide Range grouping
   with graceful fallback when module_id is null everywhere, severity-
   sorted, singleton entries capped per a documented threshold with a
   "N more in gap_report.json" note).
6. Write report_builder.py: ReportBuilder orchestrator per Section 3.
7. Write the full test suite per Section 4, including the severity-
   ordering, covered-exclusion, module-fallback, and singleton-curation
   tests.
8. Run report_builder against the fixture and show me the actual
   rendered gap_report.md in full, plus the JSON's entry count, so I
   can read it end-to-end before we wire this to real Gap_Verification
   output once that folder exists.
```