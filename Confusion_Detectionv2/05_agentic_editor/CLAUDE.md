# CLAUDE.md — 05_agentic_editor

Turns `04_gap_reporting_agent`'s per-topic reports into new slides and
inserts them into a COPY of `data/cc-unit2-slides.pptx`. The source deck
is never written to.

## Flow (one topic)

1. **Plan** (`src/planner.py`, one LLM call): the gap report, the topic's
   matched questions and the full text of its slides (`src/grounding.py`,
   budgeted to fit Groq's 8k tokens/minute) → up to 2 `SlideEdit`s: title,
   3–6 bullets, the slide to insert after, the questions answered.
   Reports marked `covered`, below 0.6 confidence, or without a slide
   range (the unmatched group) are skipped without a call.
2. **Check** (`src/checker.py`, plain code): anchor inside the topic,
   length limits, no advice-to-the-instructor, cited questions must be
   the topic's own, ≤ 80% word overlap with the anchor slide.
3. **Fact-check** (`src/verifier.py`, second LLM call): the slides around
   the insertion point + the proposed slide → contradictions, wrong
   statements, invented numbers. Any finding, or a failed call, blocks
   automatic acceptance.
4. **Apply** (`src/applier.py`, python-pptx, no LLM): new slide after its
   anchor, header (banner, orange title, rule) copied from the nearest
   standard slide, inserted into both copies of the duplicated lecture
   (slides 128–188 / 189–249), speaker notes naming the questions.

No MCP or external service edits the deck: the LLM only returns
structured fields, and tested code writes the file.

## Running

    python 05_agentic_editor/run_editor.py --topic-ids 6 12 20 --plan-only
    python 05_agentic_editor/run_editor.py --apply-plan 05_agentic_editor/output/edit_plan.json
    streamlit run 05_agentic_editor/app.py        # the live demo

The editor model defaults to `openai/gpt-oss-120b`
(`GROQ_EDITOR_MODEL_NAME` overrides it), separate from the pipeline-wide
`GROQ_MODEL_NAME`.

## Measured so far

- Topics 6, 12, 20 before grounding + fact-check: 3 of 4 applied slides
  had a factual error on reading.
- All 12 eligible gaps, with grounding + fact-check (output/edit_plan.json):
  16 slides proposed, 4 accepted, all 4 correct on reading. Rejected:
  9 by the fact-check -- read by hand, 7 findings are right, 1 is
  debatable (ECS "fully managed": its control plane is, EC2 hosts are
  not) and 1 is wrong (overlay2 IS a union filesystem) -- 1 for
  overflowing the slide, 1 for "see slide 130" advice, 1 as a repeat of
  another topic's slide for the same place (topics 14 and 18 are the two
  copies of the duplicated lecture). The edited deck goes 288 -> 294:
  three of the four slides land in both copies.
- The fact-check is still an LLM. On the topic 6/12/20 run it passed one
  slide with an overstated claim, so the demo asks a person to approve
  each slide.

## Adding to existing slides

The planner may choose `append_bullets` instead of a new slide: 1-3
bullets added to the end of an existing slide, for a small missing
detail that belongs there. The checker allows it only while the slide
stays under 850 characters (this deck's slides: median 604, 75th
percentile 794), and the applier formats the new paragraphs as copies
of the slide's last bullet and notes the addition in the speaker notes.
Duplicated-lecture slides get the same bullets.

## Broken layouts

1. **Detect** (`src/layout_check.py`, no LLM): ranks slides whose layout
   looks broken (text drawn over text in the PDF, pictures covering
   paragraphs). All 6 slides known broken by eye (5, 98, 99, 100, 278,
   282) are in its top 10, alongside false positives (2, 4, 26, 42), so
   a person confirms which to repair.
2. **Split** (`src/slide_parts.py`): the slide's paragraphs (for the LLM)
   vs. its pictures and their labels (carried over as they are).
   Whole-slide backdrop pictures are dropped, slide-sized groups are
   opened up, a picture repeated by a flattened animation is kept once
   (the least stretched copy), stretched pictures get their true
   proportions back (labels move with them), and scrambled or stacked
   labels are dropped.
3. **Rebuild text** (`src/repair.py`, one LLM call, one retry): the
   slide's own sentences restructured into a short title and up to 10
   bullets. The checker rejects a rebuild with more than 15% words not
   on the original slide, or keeping under 50% of its words; a rejected
   first try is sent back once with the findings.
4. **Apply**: the rebuild goes right after the original (standard header,
   text on one side, the pictures scaled into a panel beside or below
   it, font fitted to the text), and the original is hidden from the
   slideshow, never deleted. Picture relationships are re-linked and
   shape ids renumbered so PowerPoint opens the file cleanly.

`run_editor.py --find-broken`, then `--repair-slides 5 98 ...`
(1-indexed), or the last section of the demo, which also draws a
preview of each rebuilt slide (`src/preview.py`).

Measured on the six known broken slides (output/repair_plan.json): all 6
rebuilds pass the checks after at most one retry; 5 read faithfully. The
word checks cannot see ORDER or MEANING: one run of slide 100 joined a
phrase split across two text boxes into "control back to the VMM"
(the slide says "back to the guest OS"), and another dropped its key
first sentence. Every repair needs a person's approval for that reason.
Text-heavy originals (278: ~1600 chars) still end up at 12pt.

## Not done

- Rewriting an existing slide's own text (other than a layout repair)
  is not implemented; the editor adds slides, adds bullets, or rebuilds
  a broken slide.
- No evaluation against the withheld answer key yet.
