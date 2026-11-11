# eval/ — scoring the pipeline against human labels

Measures how accurately the pipeline finds gaps and points at the right
slides. Nothing here is imported by the pipeline, and labels never feed
back into it.

## Files

| Path | What |
|---|---|
| `configs/cc-unit2.json` | Which deck, questions and pipeline outputs to score; duplicate-slide ranges; seed |
| `datasets/splits/cc-unit2.json` | Frozen dev / held-out split (93 / 47 questions, 16 topic groups) |
| `datasets/gold/cc-unit2.dev.labels.csv` | Blank form: label these, tune on these |
| `datasets/gold/cc-unit2.held_out.labels.csv` | Blank form: label these, but **do not look at scores on them until the final run** |
| `split.py` | Makes the split and forms (refuses to overwrite a frozen split) |
| `score.py` | Scores saved pipeline outputs; writes `reports/<run>/metrics.json` and `report.md` |

## Labelling

Open the CSV in Excel or Sheets. For each question fill:

- **gap_type**: one of
  - `missing`: the deck does not explain this at all
  - `thin`: mentioned, but not explained well enough to answer the question
  - `scattered`: the answer is in the deck, but split across places that are hard to connect
  - `covered`: a student could answer it from the deck
  - `off_topic`: outside what this deck is meant to teach
- **slide_numbers**: the slides (as numbered in PowerPoint) where the answer
  is or should be, e.g. `92; 95-97`. Either copy of the duplicated lecture
  (pages 128–188 = 189–249) counts. Leave empty for `off_topic`.
- **annotator**: your name. **notes**: anything unclear.

Leave `topic_ids` empty: it is derived from the slides. Label from the
deck alone. Do not look at the pipeline's reports while labelling.
Leaving a row's `gap_type` empty means "not labelled yet": it is skipped
and counted.

## Scoring

From `Confusion_Detectionv2`:

```powershell
.venv\Scripts\python eval\score.py                       # dev split
.venv\Scripts\python eval\score.py --unmatched-as off_topic
.venv\Scripts\python eval\score.py --labels answer_key.csv --split held_out --final   # once, at the end
```

The answer key can be scored directly if it is in the same CSV columns.
`--final` runs are appended to `reports/final_runs.log`, so every look at
the held-out set is on record.

## How predictions are made from pipeline outputs

Stage 4 judges topics, labels are per question. A question takes the
verdict of its highest-scoring mapped topic. Questions stage 3 left
unmatched take the unmatched-group report's verdict (`--unmatched-as
report`, default) or count as off-topic (`--unmatched-as off_topic`).
Pipeline type names map to paper names: complete_omission → missing,
shallow_coverage → thin, fragmented_context → scattered.

Every report shows an "always say gap" baseline. The current pipeline
flags about three quarters of questions as gaps, so its F1 has to beat
that baseline to mean anything.

## Reproducible LLM runs

`common/llm_replay.py` records every model call. Set in `.env`:

```
LLM_REPLAY_MODE=record   # live calls, saved to .cache/llm_replay
LLM_REPLAY_MODE=replay   # no network, no key; a missing recording is an error
LLM_REPLAY_MODE=auto     # replay when recorded, otherwise call live and record
```

Default is `off` (unchanged behaviour). Replay reproduces recorded
responses exactly; it does not mean a live rerun would give the same output.

## Gap closure: did the edits fix the gaps?

```powershell
.venv\Scripts\python eval\closure.py --edited 05_agentic_editor\output\cc-unit2-slides.edited.pptx --change-log 05_agentic_editor\output\change_log.json
.venv\Scripts\python eval\closure.py --edited ... --change-log ... --rejudge    # + fresh stage 4 verdicts (LLM)
```

Both the original and the edited `.pptx` go through the same parser
(`01_deck_parsing/src/pptx_parser.py`) and the same stage 3 run, so a
before/after difference comes from the edits, not the parser. Slides are
aligned by content (added slides are the unmatched ones, so hand edits
work too); added slides join the topic of the slide before them.

- **Gap Closure Rate** (`--rejudge` only): targeted gaps whose fresh
  stage 4 verdict on the edited deck is `covered` ÷ targeted gaps. A
  failed re-investigation counts as not closed. Needs `GROQ_API_KEY`, or
  `LLM_REPLAY_MODE=replay` with recordings of an earlier run.
- **Evidence support** (no LLM): for each question an edit claims to
  address, its best-slide score before vs after, and whether its best
  slide is now a new slide. Relevance, not proof the question is answered.

First run on the committed plan (12 accepted edits, 19 slides added,
alignment exact): 9 of 34 targeted questions now match a new slide best;
for the other 25 an existing slide still matches better. Closure rate
not yet measured (no API key on this machine).

## Known measurement caveats

- Topic boundaries depend on the parser: on this deck the PDF and PPTX
  parses give 23 vs 22 topics, 19 boundaries within one slide of each
  other. Do not mix reports from one parse with topics from the other.
- The committed unmatched-questions report predates the `off_topic`
  verdict and calls five off-topic questions a "complete omission".
  Rerun it with a key: `run_gap_reporting.py --topic-ids -1`.
