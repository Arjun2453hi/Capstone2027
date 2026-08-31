# CLAUDE.md — Confusion_Detection_v2 (project root)

Read this first, before any per-folder CLAUDE.md. This is a from-scratch
rebuild — nothing here imports from or depends on the old
`Confusion_Detection` repo's modules.

---

## What this project does

Given a slide deck and a list of real student questions, find what the
deck fails to explain well, and produce actionable, specific
suggestions for what to fix — ultimately edited directly into the deck
by an autonomous agent, not just written up for a human to act on
manually.

## Why this is a rebuild, not an iteration

The first version (`Confusion_Detection`, old repo) clustered *questions*
independently (HDBSCAN on question embeddings) and separately guessed at
slide context with a fixed radius around a retrieved anchor slide. Two
real, measured failures came from that: ~51% of questions never
clustered with anything (pure noise), and a fixed radius window
genuinely missed real content sitting just outside it (a documented
case: a topic's real explanation was one slide past the window edge).
Deeper than either of those: the actual generation step's real-world
success rate on the entries that mattered was **2 usable suggestions
out of 27 real multi-question clusters** — the whole approach needed
rethinking, not patching.

**The new approach inverts the direction and grounds everything in the
deck's own structure:**
1. Segment the deck into topics by watching where the *content itself*
   shifts — not by guessing a window size around a retrieved slide.
2. Map questions to topics (topic → question direction, not
   question → question clustering) — every question gets scored
   against a small number of stable, content-grounded topics instead of
   trying to find agreement with other noisy, independently-phrased
   questions.
3. Classify + generate per topic, same split-model design as before
   (a small classifier for gap type, a small generator for suggested
   content) — but with a hard quality gate from day one, not bolted on
   after the fact.
4. Aggregate into a report.
5. **New, decided since the last build**: an agentic editor consumes
   that report and edits the actual deck — this wasn't in scope before.

## The 5 stages

**Renumbering note**: stages 4 and 5 were originally planned as separate
classify-then-generate ("Gap Verification") and deterministic-aggregate
("Gap Reporting") steps. They've since been collapsed into one
**agentic** stage (`04_gap_reporting_agent`) — an agent that
investigates before concluding doesn't need a separate classification
pass, since classification is just one field in what it produces after
investigating. See that folder's CLAUDE.md for the full design. The old
stage 6 (`06_agentic_editor`) is renumbered to `05_agentic_editor`.

| # | Folder | Purpose | Status |
|---|---|---|---|
| 1 | `01_deck_parsing` | PDF → structured slide JSON (title, bullets, raw_text, slide_id) | Built |
| 2 | `02_topic_segmentation` | Group slides into topics by content shift, not by question clustering | Built |
| 3 | `03_question_mapping` | Score every question against every topic (topic→question direction), keep matches above a threshold with their score | Built |
| 4 | `04_gap_reporting_agent` | Agentic per-topic investigation + report generation + deterministic severity ranking (merged verification + reporting) | Building next |
| 5 | `05_agentic_editor` | Consume the reports, edit the actual deck | Not built — genuinely open design questions, see that folder's CLAUDE.md |

## Test data

- `data/cc-unit2-slides.pdf` — 288-slide real Cloud Computing (Unit 2)
  deck: hypervisors, virtualization techniques, memory/IO virtualization,
  Popek-Goldberg, VM migration, containers/Docker/namespaces/cgroups,
  DevOps/CI-CD, Kubernetes.
- `data/u2_v2_questions.txt` — 140 scrambled student questions, the
  actual pipeline input. This is the only question data any stage
  should read.

**One objective, verifiable property of the source PDF, not an answer
key — safe to design against:** slides 128-188 are an exact,
byte-for-byte duplicate of slides 189-249 (the entire containers/
Docker/namespaces/cgroups lecture appears twice, word-for-word — this
was confirmed by direct text comparison, not inferred). Any parser or
segmentation approach should be expected to encounter this and handle
it sensibly (e.g. finding two separate topic instances of identical
content), since it's a real, discoverable structural fact about the
deck, not a hidden test answer.

**Deliberately not provided to any stage:** an evaluation answer key
exists separately (which specific questions are genuine coverage gaps,
and where) but is being withheld from the pipeline and from this repo
so that later evaluation of `02_topic_segmentation` through
`05_gap_reporting` stays honest — nothing should be built or tuned
with visibility into which questions "should" come out as gaps.

## Stage 1 — Deck Parsing (`01_deck_parsing`) — build this first, real spec below

**Job:** `data/cc-unit2-slides.pdf` → one structured JSON record per
slide: `slide_id` (0-indexed, permanent), `slide_number` (1-indexed,
human-readable), `title`, `bullets`, `raw_text`, `char_count`.

**Title/bullet detection — carry forward the proven approach, don't
rediscover it from scratch:** plain `page.extract_text()` throws away
font size and position, so a title is indistinguishable from a bullet
in raw text. Read pdfplumber's word-level data (text, font size,
position via `extract_words(extra_attrs=["size"])`), group words into
lines by vertical proximity, and treat the topmost contiguous run of
lines at the page's *maximum* font size as the title; everything after
that is a bullet, with leading marker characters stripped (including
the `(cid:N)` placeholder pdfplumber emits when a bullet glyph isn't in
a font's base encoding and can't map back to Unicode).

**Boilerplate stripping — also carry forward, this deck likely has the
same problem:** real slide-deck PDF exports almost always repeat a
course/unit banner and page-number footer on every page (this deck's
first slide already shows: `"CLOUD COMPUTING"` header and a
`"Dr. Prafullata Kiran Auradkar / Department of..."` footer block
repeating across many slides). If any repeated line renders in a
bigger font than the real slide heading, it can win the "largest font
= title" check and corrupt the real title. Fix: two-pass parsing —
pass 1 collects every page's lines across the whole deck without
classifying anything; a line is flagged boilerplate if, after
normalizing away digit runs (so page numbers count as one repeated
pattern), it appears on at least `max(3, 0.30 × num_pages)` distinct
pages; pass 2 strips flagged lines *before* running title/bullet
detection — order matters, stripping after would already be too late.

**Known edge cases in this specific deck, confirmed by direct
inspection — write tests for these:**
- **Multiple slides have no extractable text at all** (image-only —
  e.g. diagrams with no text layer). `is_empty()` must correctly flag
  these; don't let them silently produce a slide with `title=None,
  bullets=[]` that looks like a parsing failure rather than an
  intentional image-only slide.
- **The exact-duplicate slide range (128-188 / 189-249)** — the parser
  should just parse both halves faithfully and identically; don't try
  to detect or deduplicate this at the parsing stage, that's a
  segmentation-stage concern if it's ever handled at all.
- Slide 1 (and similar title/section-intro slides) contains a long
  acknowledgements paragraph — confirm this doesn't get mistaken for
  boilerplate (it's long-form prose, not a short repeated banner line)
  and doesn't dominate `raw_text` in a way that drowns out the actual
  slide title.

**Testing:** build a small synthetic fixture PDF (a handful of slides,
reportlab is fine) that reproduces the banner-bigger-than-title bug on
purpose, confirm the fix resolves it, then run against the real
288-page deck and spot-check: slide 1's title, a known blank/image-only
slide, and one slide from each half of the duplicate range.

## Design principles carried forward from the old build (proven useful, not being rediscovered from scratch)

- **Dependency injection** for every swappable piece (embedding model,
  segmentation strategy, classifier, generator) — abstract interface in
  `common/` or the owning stage, concrete implementations behind it.
- **`slide_id`** (0-indexed, assigned once at parse time) is the
  permanent identity threaded through every stage, never re-derived.
- **Traceability never lost** — every grouping/aggregation keeps its
  full source data (which questions, which slides) behind it.
- **Numeric testing** — every test suite reports real accuracy/scores,
  not just pass/fail (e.g. % of slides with a non-null title, count of
  detected boilerplate lines removed, spot-checked slide content) —
  without relying on any withheld answer key (see Test data section).
- **Hard quality gates on generated text from day one** — the old
  build's core failure was ungated generator output reaching the final
  report; this must not be re-introduced even for a "fresh start."
- **`common/`** holds shared interfaces/contracts multiple stages
  depend on (the embedding model interface, the slide schema) — no
  stage should import from another stage's internals. This fixes a
  real structural issue from the old build (`Global_Context` importing
  directly from `Similiarity_gen`).

## Build order

Folder skeleton (all 6 + `common/`) gets created now, each with a
one-paragraph placeholder CLAUDE.md. **Only `01_deck_parsing` gets real
code next.** Don't write real logic for stages 2-6 until the stage
before it has real, validated output to design against — guessing at
an interface before its input exists risks building the wrong thing
twice.