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

## 4. Threshold — locally adaptive, not deck-wide

**Revision from the first build**: a single deck-wide threshold
(computed once from the whole deck's score distribution) is the
prime suspect behind a real failure observed on the test deck — three
lecture-length sections early in the deck share dense, overlapping
low-level vocabulary, so their genuine topic-shift dips are small in
absolute terms; meanwhile other transitions later in the deck (into
lexically distinct material) produce much bigger dips. A single global
threshold calibrated against the deck's biggest dips misses the
smaller-but-real ones in the vocabulary-dense region, while still
catching the obvious ones elsewhere. Confirmed failure: slides 0-49
(three real lectures: Hypervisor Types, Para/Full Virtualization,
Trap-and-Emulate) collapsed into a single segment across two build
attempts, unchanged.

**Fix: compute the threshold from a local window of nearby candidate
scores, not the whole deck.**

```
window(i) = positions in [i - W, i + W], clipped to the valid
            candidate range at deck edges (W = 20 by default —
            tunable, but pin this as the starting value; don't leave
            it unset)

local_threshold(i) = mean( boundary_score(j) for j in window(i) )
                    + 1.0 * stdev( boundary_score(j) for j in window(i) )
```

Cut at position *i* if `boundary_score(i) > local_threshold(i)`
(where `boundary_score(i)` is still `content_depth(i) +
bounded_structural_boost(i)` from Section 3, unchanged).

**New required step — non-maximum suppression**: because a local
threshold can cause several adjacent positions around one real dip to
all exceed their own (similarly-shaped) local threshold at once, don't
cut at every qualifying position. Within any run of consecutive
qualifying positions, **keep only the single highest-scoring position**
and discard the rest as duplicates of the same underlying dip. Use the
same window size as the minimum-segment-length merge step below for
this suppression radius, so the two don't fight each other.

**Post-processing (unchanged from before):** merge any segment shorter
than a minimum length (2-3 slides) into whichever neighbor it's more
similar to.

**If this alone doesn't resolve the observed failure**, the next
things to try, in order, are (a) making depth *relative* rather than
absolute (`depth(i) / average(left_peak, right_peak)`, a percentage
drop instead of a raw difference), and only after that (b) suspecting
the embedding model itself can't separate this vocabulary-dense
region at all, which would need a different kind of fix (a secondary
lexical-overlap signal). Don't jump to either of those yet — isolate
whether the local threshold change alone closes the gap first.

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
- **New, specifically targeting this fix**: a synthetic test with two
  regions in one constructed embedding sequence — one region where
  baseline similarity is high and the real topic-shift dip is small
  but genuine, and a second region where baseline similarity is low
  and the dip is large. Assert the local-threshold approach correctly
  cuts a boundary in **both** regions. As a sanity check, also compute
  what a single global threshold would have done on the same synthetic
  data and confirm it misses the small-dip region — this is the test
  that actually proves the fix addresses the diagnosed failure mode,
  not just that the code runs.
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
3. Write the EmbeddingModel-consuming multi-scale block-similarity +
   depth-score computation per Section 2, using the exact scale-pinned
   peak-search formula given there (not a global max, not a different
   window size than k) — this is the primary logic, spend the most
   effort making it correct.
4. Write the bounded structural-boost logic per Section 3 — keep it
   visibly minor in the code (a small capped addend), not structured
   as anything resembling an override.
5. Write the locally-adaptive threshold + non-maximum suppression +
   minimum-segment merging per Section 4 — this is the actual fix for
   the diagnosed failure (slides 0-49 collapsing into one topic across
   two prior runs). Replace the old global-threshold logic entirely,
   don't leave it as a fallback path.
6. Write Topic + the output list per Section 5.
7. Write the visualization per Section 6 (matplotlib is fine) —
   combined signal curve, boundaries marked and labeled with
   confidence, structural-boosted boundaries visually distinguished.
8. Write the test suite per Section 7, specifically including the test
   that a structural boost alone cannot create a boundary.
9. Run against the real parsed cc-unit2-slides.pdf and show me: the
   PNG plot, total segments found, size distribution, and the full
   list of predicted (start_slide_id, boundary_confidence) pairs,
   AND which embedding model was actually used for the run. Also
   print the raw content_depth(i) values (before threshold, before
   structural boost) at positions 12, 29, and 62 specifically — I need
   to see whether a real dip exists there now that wasn't crossing the
   old global threshold, to confirm this fix addressed the actual
   cause rather than coincidentally shifting other boundaries.
```