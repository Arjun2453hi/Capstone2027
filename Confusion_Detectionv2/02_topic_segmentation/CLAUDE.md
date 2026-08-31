# CLAUDE.md — 02_topic_segmentation

Context for any Claude session (chat or Claude Code) working inside
`02_topic_segmentation/`. Read the project-root `CLAUDE.md` and
`01_deck_parsing/CLAUDE.md` first, then this.

---

## 1. The problem, precisely

Given Stage 1's parsed deck (ordered slides with `slide_id`, `title`,
`title_font_size`, `bullets`, `raw_text`), find where the *content*
shifts topic and cut the deck into contiguous, non-overlapping topic
segments. Must generalize to any deck — no tuning specific to
`cc-unit2-slides.pdf`.

**Content similarity (cosine, on slide embeddings) is the primary and
dominant signal. Structural cues are minor, bounded nudges only — not
independent decision-makers.** This is a deliberate rebalancing:
structure alone is too deck-specific to trust heavily on a deck we've
never seen, whereas semantic content shift is the one signal that
should generalize.

## 2. Core signal: multi-scale block cosine similarity

1. Embed every slide (title + bullets) via the injected `EmbeddingModel`
   from `common/embeddings` — import the interface only, never a
   concrete embedding library directly in this module. **Default
   implementation: `SentenceTransformerEmbeddingModel`
   (`all-MiniLM-L6-v2`), not TF-IDF.** Reasoning specific to
   segmentation: two adjacent slides can continue the same topic while
   sharing almost no exact vocabulary (e.g. one slide says "the VMM,"
   the next says "the hypervisor layer") — TF-IDF only sees literal
   token overlap and would likely read that as a false similarity dip,
   manufacturing a spurious boundary. `TfidfEmbeddingModel` remains
   available as an explicit no-network/offline fallback only, not a
   co-equal default — if it's used, that should be a deliberate,
   logged choice, not silently interchangeable.
2. At every candidate boundary position *i* (between slide *i* and
   *i+1*), compute block similarity: cosine similarity between the
   average embedding of the *k* slides before *i* and the average
   embedding of the *k* slides after *i*. Block-vs-block, not
   slide-vs-slide — a single oddly-worded slide mid-topic shouldn't
   read as a boundary.
3. **Compute this at multiple window sizes** — k=2, k=3, and
   k=round(sqrt(N)) where N is the deck's slide count — and combine by
   taking the strongest (lowest-similarity) signal across scales at
   each position. A real boundary tends to show up across multiple
   window sizes; a boundary that only appears at one specific k is
   more likely an artifact of that choice. This multi-scale approach
   is what makes the segmentation deck-agnostic — a single fixed k is
   implicitly a bet on "typical topic length," which varies by deck.
4. Convert the resulting similarity curve into a **depth score** per
   position, with peak search **pinned to the same scale k being
   evaluated** — not a whole-deck global max, and not some other
   arbitrary window. Rationale: a global max could be dominated by an
   unrelated, unusually-coherent section far away in the deck that has
   nothing to do with position *i*; tying the peak search to the same
   k keeps the depth score locally meaningful and self-consistent with
   what's actually being measured at that scale.

   For scale k, let `sim_k(j)` be that scale's block-similarity value
   at position j. Then at position i:
   ```
   left_peak(i, k)  = max( sim_k(j) for j in [i-k, i) )
   right_peak(i, k) = max( sim_k(j) for j in [i, i+k) )
   depth_k(i) = (left_peak(i,k) - sim_k(i)) + (right_peak(i,k) - sim_k(i))
   ```
   **Edge handling**: if fewer than k positions exist on either side of
   *i* (near the start/end of the deck), that position is **excluded
   from boundary candidacy at that scale** rather than computed with a
   shrunk or padded window — there isn't enough context on that side to
   trust the comparison, so don't guess at it.

5. **Combine across scales**: `combined_depth(i) = max_k( depth_k(i) )`
   — take the strongest signal across k=2, k=3, k=round(sqrt(N)) at
   each position (a position excluded at one scale simply doesn't
   contribute at that scale; it can still contribute via the other
   scales).

**This combined depth score is the primary quantity driving
segmentation.** Everything in Section 3 only adjusts it slightly.

## 3. Structural signals — minor, bounded boosts only

- A slide with a title but zero bullets, or a slide whose
  `title_font_size` is a local maximum relative to its immediate
  neighbors: apply a small, capped additive boost to that position's
  depth score (e.g. no more than ~10-15% of the typical depth-score
  range for this deck — tune empirically, but keep it clearly
  secondary).
- A slide that's a near-exact duplicate of another, non-adjacent slide
  elsewhere in the deck (reuse `01_deck_parsing`'s generic near-
  duplicate detector at the whole-slide level — don't hardcode any
  specific template phrase like "THANK YOU"): also a bounded boost,
  **not a hard override**. A repeating template slide is still strong
  evidence of a boundary, but it should win *because* it pushes an
  already-borderline cosine dip over the adaptive threshold, not by
  force-cutting regardless of what the content signal says.

**No structural signal should be able to create a boundary the content
signal doesn't at least weakly support**, and no structural signal
should be able to suppress a boundary the content signal strongly
supports. They tip close calls; they don't make calls on their own.

## 4. Threshold — adaptive to this deck's own score distribution

`boundary_score(i) = content_depth(i) + bounded_structural_boost(i)`

Cut at position *i* if `boundary_score(i)` exceeds an adaptive
threshold computed from *this deck's* own distribution of scores (e.g.
mean + 1 stdev, or top-Nth-percentile within this deck) — not a fixed
absolute number, since what counts as "a real dip" varies by how
homogeneous a given deck's content is overall.

**Post-processing:** merge any segment shorter than a minimum length
(e.g. 2-3 slides) into whichever neighbor it's more similar to, to
avoid spurious single-slide segments from noisy threshold crossings.

## 5. Output schema

```python
@dataclass
class Topic:
    topic_id: int
    start_slide_id: int
    end_slide_id: int
    slide_ids: List[int]
    boundary_confidence: float   # the combined score that triggered this cut
```

Deliberately excludes assembled slide text — Stage 3 pulls content
from Stage 1's JSON via `slide_ids` when needed. Keep this module
single-purpose: boundaries only.

## 6. Visualization output — required, for visual sanity-checking

Produce a plot, saved as a PNG, showing:
- **X-axis**: slide position (0 to N).
- **Y-axis**: the combined multi-scale similarity/depth signal used to
  drive segmentation.
- **Vertical dashed lines** at every predicted `start_slide_id`,
  labeled with that boundary's `boundary_confidence`.
- Visually distinguish (e.g. line color or marker) boundaries that
  received a structural boost from ones driven by content alone, so
  it's easy to see how much the minor signals actually contributed.

This is a required deliverable alongside the JSON topic list — the
point is to let a human glance at the curve and the cut points
together and sanity-check whether the segmentation looks reasonable,
not just trust the numbers blindly.

## 7. Testing

- Unit tests for depth-score computation on synthetic embedding
  sequences with known, constructed dips (deterministic, no real model
  needed).
- Unit test confirming a structural boost alone (with a flat/
  unsupportive content signal) does NOT create a boundary — this is
  the test that actually enforces "cosine-dominant," not just a design
  intention in prose.
- Unit test for minimum-segment-length merging.
- **Real-deck run**: run against the actual parsed `cc-unit2-slides.pdf`
  JSON, produce the JSON topic list AND the PNG plot, report segment
  count and size distribution. Do not attempt to score this against
  any withheld answer key — just produce and report the output.
  Scoring happens separately against an independently-derived boundary
  silver standard, prepared outside this module's build.

## 8. Prompt to run

```
Build 02_topic_segmentation per this CLAUDE.md.

Specifically:
1. Confirm 01_deck_parsing's Slide schema includes title_font_size,
   and that its near-duplicate detector is importable and generic
   enough to run at the whole-slide level. Fix either one there first
   if not.
2. Confirm common/embeddings has a working SentenceTransformerEmbeddingModel
   and wire it as this module's default (install sentence-transformers
   and actually download all-MiniLM-L6-v2 — don't silently fall back to
   TfidfEmbeddingModel and call it equivalent; if network access is
   genuinely unavailable in this environment, use Tfidf but say so
   explicitly in the run output rather than leaving it unstated).
2. Write the EmbeddingModel-consuming multi-scale block-similarity +
   depth-score computation per Section 2, using the exact scale-pinned
   peak-search formula given there (not a global max, not a different
   window size than k) — this is the primary logic, spend the most
   effort making it correct.
3. Write the bounded structural-boost logic per Section 3 — keep it
   visibly minor in the code (a small capped addend), not structured
   as anything resembling an override.
4. Write the adaptive-threshold cutting + minimum-segment merging per
   Section 4.
5. Write Topic + the output list per Section 5.
6. Write the visualization per Section 6 (matplotlib is fine) —
   combined signal curve, boundaries marked and labeled with
   confidence, structural-boosted boundaries visually distinguished.
7. Write the test suite per Section 7, specifically including the test
   that a structural boost alone cannot create a boundary.
8. Run against the real parsed cc-unit2-slides.pdf and show me: the
   PNG plot, total segments found, size distribution, and the full
   list of predicted (start_slide_id, boundary_confidence) pairs.
```


# CLAUDE.md — Bugfix: Groq Model Drift + Stage 2 Boundary Gap (topic 17 region)

Two unrelated bugs, one doc. Bug 1 is a quick, contained fix inside
`common/`. Bug 2 needs real investigation — it's the same root-cause
*pattern* (unvalidated threshold assumptions) that's now shown up
three times in this project (Stage 1 boilerplate detection, Stage 2
segmentation, Stage 3 matching thresholds), in a new, subtler place.

---

## Bug 1 — Groq model name drift (quick fix)

**What happened:** `llama-3.3-70b-versatile` was hardcoded as the LLM
fallback model. Groq removed it from their hosted lineup since the
original project used it — not a logic bug, just an external API
changing under us. Already patched to `openai/gpt-oss-20b` by querying
Groq's live model list directly.

**The actual fix needed — don't just swap the string, prevent this
from silently recurring:**
1. Move the model name into `.env` (`GROQ_MODEL_NAME=openai/gpt-oss-20b`)
   — a future drift becomes a one-line config change, not a code
   change requiring a rebuild.
2. On startup, validate the configured model actually exists in Groq's
   current model list (one cheap API call) and **fail loudly with a
   clear error** ("configured model X is not in Groq's current
   lineup, see https://... for available models") rather than letting
   it fail deep inside a retry loop or produce a cryptic API error
   during the actual fallback call.
3. Add one test that mocks Groq's model-list endpoint returning a list
   that does NOT include the configured model, and asserts the startup
   check catches it before any real question-mapping work begins.

That's the whole fix — contained to `common/llm_client.py` (or
wherever the Groq client lives) plus `.env`/`.env.example`. Nothing
else in the pipeline needs to change.

---

## Bug 2 — Stage 2 boundary gap, now with a measured downstream cost

### The symptom, precisely

Real Kubernetes questions ("benefits of using Kubernetes," "master vs
worker nodes") failed to match topic 17 (slides 268-287, which should
be the orchestration/Kubernetes content) and instead landed unmatched
or pointed toward topic 16 (DevOps, 248-267) — a topic that shouldn't
contain Kubernetes-internals content at all.

**This confirms, with a real functional failure, what was previously
only an abstract concern**: topic 17 likely contains an undetected
internal boundary — general container-orchestration concepts (tool
comparisons, orchestration-in-general) blended together with
Kubernetes-specific internals (Pods, kubelet, kube-proxy, master/worker
architecture) into one segment. A single aggregate embedding for that
blended segment sits in a semantic "middle ground" that doesn't
strongly represent either sub-topic — diluted enough toward generic
deployment/automation vocabulary that it can end up closer to DevOps
than to specific Kubernetes internals.

### Two independent hypotheses — investigate both, don't assume which one it is

**Hypothesis A — the same vocabulary-density issue already diagnosed
for slides 0-49.** If general orchestration and Kubernetes-specific
content share enough surface vocabulary ("deployment," "cluster,"
"automation"), the real topic-shift dip between them may simply be
small — same failure mode as before, and the already-requested
locally-adaptive threshold fix (see `02_topic_segmentation/CLAUDE.md`)
may resolve this on its own once it's actually run. **First action:
confirm whether that fix was applied and re-run before this Stage 3
result was produced.** If the Stage 3 report used a Stage 2 output
from before the local-threshold fix, this may already be
partially or fully resolved and hasn't been re-tested yet — check this
before writing any new code.

**Hypothesis B — an edge effect specific to the local-threshold fix
itself, independent of vocabulary density.** The local-threshold
window defaults to W=20 slides on each side. Slides 268-287 sit within
20 slides of the deck's actual end (287 is the last slide, 0-indexed,
in a 288-slide deck). **Any candidate boundary position inside this
range has a badly clipped right-side window** — there may be fewer
than 20 valid slides after it to compute local statistics from. A
mean/stdev computed from a much smaller-than-intended sample can be
noisy or unrepresentative, potentially causing the local threshold to
behave unreliably exactly in this tail region — a distinct problem
from vocabulary density, and one the current local-threshold spec
doesn't explicitly handle (peak-search already excludes candidates
near edges with insufficient context; the local-threshold window
computation was not given the same treatment when it was written).

**Fix for Hypothesis B, if confirmed:** require a minimum valid sample
size (e.g. at least W/2 positions) on each side before trusting a
local threshold computed there. If a candidate position doesn't have
enough neighbors on one side, either (a) compute the local threshold
from whatever asymmetric window is actually available rather than
silently treating a small sample as if it were full-size, or (b) blend
the local statistic with the deck's global statistic, weighted by how
much data was actually available. Don't leave this unhandled the way
it currently is.

### Recommended investigation order

1. **Confirm which Stage 2 output Stage 3 actually ran against** —
   before/after the local-threshold fix. This alone might explain the
   whole thing.
2. If the local-threshold fix was already applied and this persists:
   pull the raw `content_depth(i)` values for every candidate position
   inside 268-287 (same diagnostic as was done for 12/29/62 earlier) —
   this distinguishes Hypothesis A (real dip too small) from
   Hypothesis B (window-size/edge artifact) directly, rather than
   guessing.
3. Implement whichever fix the evidence points to — don't implement
   both preemptively.
4. Re-run Stage 2 → Stage 3 end to end and confirm specifically that
   Kubernetes-internals questions now match a Kubernetes-specific
   topic rather than DevOps or unmatched.

### A complementary, independent mitigation worth building regardless of the outcome above

Even a well-tuned Stage 2 will never perfectly segment every deck —
some real topics genuinely blend together at the slide level, or a
deck's true topic boundaries are inherently fuzzy. **Stage 3 currently
represents each topic as a single aggregate embedding** (however it's
computed — likely a mean over member-slide embeddings), which is
fragile to exactly this failure mode: a topic that internally mixes
two sub-concepts produces one blurry average that represents neither
well.

**Suggested change, independent of the Stage 2 fix above**: instead of
one embedding per topic, keep **each member slide's own embedding**
and match a question against the *best-scoring individual slide*
within a topic, not the topic's single averaged vector — then roll
that up to "this question matches topic 17" via its best-matching
slide inside it. This is strictly more robust to internally-mixed
topics: a Kubernetes-internals question can still score well against
whichever specific slides in topic 17 are actually about Kubernetes
internals, even if the topic as a whole also contains general
orchestration content diluting a single averaged representation. This
is defense-in-depth, not a replacement for fixing Stage 2 — do both,
since Stage 2 will always be imperfect on some decks and Stage 3
shouldn't be fully dependent on it being flawless.

### Testing for Bug 2

- Diagnostic script (not a permanent test): print raw `content_depth`
  for every position in 268-287, both before and after whichever fix
  is chosen — this is evidence, keep the output in the PR/commit
  description even after the code changes.
- Unit test reproducing Hypothesis B directly: construct a synthetic
  deck where a real, clearly-should-be-detected boundary sits within
  W slides of the deck's end, and assert the local-threshold fix (with
  the minimum-sample-size handling) still detects it — this is the
  regression test that prevents this specific edge case from
  recurring on some future deck.
- If the slide-level topic-matching change is also made: a unit test
  with a synthetic topic containing two clearly different sub-blocks
  of slides, and a query matching only one sub-block, asserting the
  slide-level approach finds it while a mean-pooled aggregate
  embedding (kept as a comparison baseline in the test, not in
  production) would have scored it lower.
- Full re-run of Stage 2 → Stage 3 on the real deck, reporting: does
  topic 17 now split into two topics (or does its matching improve
  without splitting, if the slide-level fix alone resolves it);
  do the specific previously-failing Kubernetes questions now match
  correctly.

---

## Prompt to run

```
Fix both bugs per this CLAUDE.md.

Bug 1 (Groq model drift):
1. Move the Groq model name into .env, add the startup validation
   check against Groq's live model list, and add the mocked-failure
   test, per the "Bug 1" section above.

Bug 2 (Stage 2 boundary gap / Stage 3 mismatch):
1. First confirm whether the Stage 3 report was generated using a
   Stage 2 output from before or after the locally-adaptive-threshold
   fix. State this explicitly before doing anything else.
2. If before: re-run Stage 2 with the local-threshold fix applied,
   then re-run Stage 3 on the new output, and report whether topic 17
   / the Kubernetes-question mismatch is now resolved. Stop here if so
   and report the result — don't implement Hypothesis B's fix
   speculatively if it turns out not to be needed.
3. If the problem persists after the local-threshold fix: pull and
   print the raw content_depth(i) values for every position in
   268-287, and determine whether Hypothesis A (real dip too small) or
   Hypothesis B (edge-window sample-size artifact) better explains it.
4. Implement the fix indicated by that evidence (minimum-sample-size
   handling for Hypothesis B; otherwise reconsider Hypothesis A's
   options from 02_topic_segmentation/CLAUDE.md Section 4's fallback
   list).
5. Separately, implement the slide-level topic-matching change in
   Stage 3 (match against best individual member-slide embedding, not
   one averaged topic embedding) as a defense-in-depth improvement,
   regardless of whether step 4 was needed.
6. Write the tests described in the "Testing for Bug 2" section.
7. Re-run Stage 2 -> Stage 3 end to end on the real deck and confirm:
   does topic 17's structure change, and do the previously-failing
   Kubernetes questions now match correctly. Show me the before/after
   comparison directly.
```