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

## Broken layouts

`src/layout_check.py` ranks slides whose layout looks broken (text drawn
over text in the PDF, pictures covering paragraphs). All 6 slides known
broken by eye (5, 98, 99, 100, 278, 282) are in its top 10, alongside
false positives (2, 4, 26, 42), so it only proposes candidates:
`run_editor.py --find-broken`, or the last section of the demo. Repairing
them is not implemented.

## Not done

- Only inserts slides: adding to or rewriting an existing slide, and
  repairing broken layouts, are not implemented.
- No evaluation against the withheld answer key yet.
