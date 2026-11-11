# Confusion Detection v2

Finds what a lecture deck explains badly, using the questions students
actually asked, and fixes the deck: an LLM writes the missing slides,
they are checked and fact-checked, and the approved ones are inserted
into a copy of the PowerPoint file.

Works on any deck: a `.pptx` (full editing), a `.pdf` (new slides are
inserted as pages of a copy), or both. Registered decks live in `decks/`:
Cloud Computing Unit 2 (288 slides, 140 real student questions) and
Software Engineering Units 1-4 (PDF, synthetic questions for now).

---

## Any deck: add it, run it, open it

```powershell
.venv\Scripts\python run_pipeline.py add "SE - UNIT 4.pdf" --title "Software Engineering - Unit 4"
.venv\Scripts\python run_pipeline.py questions se-unit-4 questions.txt    # real student questions, one per line
.venv\Scripts\python run_pipeline.py run se-unit-4                        # stages 1-4 into decks/se-unit-4/
.venv\Scripts\python run_pipeline.py list
.venv\Scripts\python 05_agentic_editor\serve.py                           # pick the deck in the header
```

- **No real questions yet?** `run ... --synthetic-questions` has the LLM
  write student-style questions from each topic's slides
  (`questions.synthetic.txt`). They are marked synthetic everywhere (UI
  badge, manifest, evaluation reports) and must not be reported as real
  student results. Adding real questions later replaces them.
- **Resumable.** Each stage is skipped when its output is newer than its
  inputs; stage 4 only re-investigates topics without a report or whose
  last attempt failed (rate limit, provider error). LLM calls are
  recorded (`LLM_REPLAY_MODE=auto`), so a rerun after a quota stop
  replays finished calls instead of paying for them again.
- **Keys.** `GROQ_API_KEY`, or several keys from different accounts as
  `GROQ_API_KEY_1`, `_2`, ... in `.env`: every stage rotates across them.
- **Per-deck paths.** Every entry point takes a deck id
  (`run_editor.py --deck-id`, `eval/score.py --deck-id`,
  `eval/closure.py --deck-id`, `serve.py --deck`, `DECK_ID=...`); none
  falls back to a built-in deck. With several decks and no id given they
  stop and ask.
- **PDF-only decks** get new slides only (drawn at the page size, below
  the deck's own banner, with an "Added slide" footer). Adding bullets
  to an existing slide and layout repair need the `.pptx`.

The stage folders' own CLIs still accept explicit paths; their defaults
point at the original Cloud Computing files for backward compatibility.

---

## Main interface: React workspace

The editor now has a React interface with source/proposal slide comparison,
explicit approvals, layout repairs, evaluations and persistent run history.
Groq remains the model provider. From this directory on Windows:

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
cd 05_agentic_editor/frontend
npm install
npm run build
cd ../..
.venv\Scripts\python 05_agentic_editor/serve.py
```

Open **http://127.0.0.1:8000**. Node 20.19+ or 22.12+ is needed only for
building. Use `.venv/bin/python` on macOS/Linux. Set `GROQ_API_KEY` in the
server `.env` for live generation; saved suggestions work without it.
See the [editor README](05_agentic_editor/README.md) for development and
tests, and [HARNESS.md](05_agentic_editor/HARNESS.md) for task orchestration,
bounded context memory, execution limits, cancellation and guardrails.

## Legacy Streamlit demo

You need **Python 3.9 or newer** and **git**.

**macOS / Linux**

```bash
git clone -b v2-mapping-and-agent-fixes https://github.com/Arjun2453hi/Capstone2027.git
cd Capstone2027/Confusion_Detectionv2
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run 05_agentic_editor/app.py
```

**Windows (PowerShell)**

```powershell
git clone -b v2-mapping-and-agent-fixes https://github.com/Arjun2453hi/Capstone2027.git
cd Capstone2027\Confusion_Detectionv2
py -m venv .venv
.venv\Scripts\pip install --upgrade pip
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\streamlit run 05_agentic_editor\app.py
```

The install takes a few minutes (`sentence-transformers` is large). The
demo opens at **http://localhost:8501**.

Without any API key you can already browse the gaps, read the agent's
reports and the students' questions, see the slides, and run the
broken-layout check. **Suggesting slides needs a Groq API key** (next
section): paste it into the sidebar box, or put it in `.env`.

---

## API keys

Get a free Groq key at https://console.groq.com/keys. Then either paste
it into the demo's sidebar (kept for that browser session only), or:

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

and edit `.env`:

| Variable | Needed for | Value |
|---|---|---|
| `GROQ_API_KEY` | anything that calls an LLM (stages 3–5, the demo's "Suggest") | your Groq key |
| `GROQ_MODEL_NAME` | stages 3 and 4 | `openai/gpt-oss-20b` (default) or `openai/gpt-oss-120b` |
| `GROQ_EDITOR_MODEL_NAME` | optional, stage 5 and the demo | defaults to `openai/gpt-oss-120b` |
| `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` | optional: LangSmith traces of the stage 4 agent | set `LANGCHAIN_TRACING_V2=false` if you have no LangSmith key |

Optional, for long stage 4 runs: put extra keys in `.env.groq-rotation`
as `GROQ_API_KEY_1=...`, `GROQ_API_KEY_2=...`, and so on. The agent
switches key when one hits its rate limit. Keys from the same Groq
account share one quota, so only keys from different accounts (for
example, teammates') help.

`.env` and `.env.groq-rotation` are git-ignored. **Never commit a key.**

---

## Using the demo

1. **Pick a gap** in the sidebar. The 12 topics with a gap are listed
   worst first.
2. **Read what the gap-reporting agent found**: gap type, its
   confidence, the full report, and the students' questions.
3. **Suggest slides for this gap.** The LLM writes up to two slides;
   plain-code checks and a second LLM fact-check run on each. Each
   suggestion is shown next to the slide it would follow, with any
   problems found. Slides that pass are pre-approved; you can approve or
   un-approve any of them.
4. **Apply approved slides.** They are inserted into a copy of the deck
   (`05_agentic_editor/output/cc-unit2-slides.demo.pptx`); the original
   is never changed. Download it, or on a Mac with PowerPoint, open it
   directly.

Suggestions are either a **new slide** after an existing one, or a few
**bullets added to an existing slide** that has room for them.

At the bottom, **Find slides with a broken layout** lists slides whose
text overlaps other text or is covered by pictures. Tick the ones that
really are broken and click **Plan repairs**: each slide is rebuilt from
its own text and pictures, shown next to the original with a preview,
and once approved it is inserted right after the original, which is
hidden from the slideshow (nothing is deleted).

**Pre-checked or live.** By default the demo loads results generated
ahead of time by this same pipeline and read by a person
(`05_agentic_editor/output/reviewed_suggestions.json`): instant, and no
API key needed. Switch off **Use pre-checked results** in the sidebar to
generate live: each suggestion is written, checked, fact-checked and, if
flagged, corrected by the model up to twice (about 10 seconds to 3
minutes, depending on Groq's rate limits). Suggestions that still fail
are kept in a collapsed "Blocked by the safety checks" section.

---

## The pipeline

Every stage reads the previous stage's output file, and every output is
committed, so **you never need to rerun earlier stages** to use the demo
or a later stage.

| # | Folder | What it does | Output |
|---|---|---|---|
| 1 | `01_deck_parsing` | PDF → one record per slide (title, bullets, text) | `parsed_deck.json` |
| 2 | `02_topic_segmentation` | Splits the deck into 23 topics where the content shifts | `topics.json` |
| 3 | `03_question_mapping` | Assigns each question to the topic holding its best-matching slide | `question_mapping.json`, `gap_verification_input.json` |
| 4 | `04_gap_reporting_agent` | An LLM agent investigates each topic with tools and writes a gap report | `output/topic_XX_report.json`, `output/index.json` |
| 5 | `05_agentic_editor` | Plans new slides from the reports, checks, fact-checks and inserts them | `output/edit_plan.json`, `output/change_log.json`, the edited `.pptx` |

Each folder's `CLAUDE.md` explains its design in detail.

### Running stages yourself

Run from the `Confusion_Detectionv2` folder. Use `.venv/bin/python`
(Windows: `.venv\Scripts\python`). Run the scripts by path, not with
`python -m`: the folder names start with a digit.

```bash
# 1. Parse the deck (output is already committed)
.venv/bin/python -c "import importlib; p = importlib.import_module('01_deck_parsing.src'); p.save_deck_json(p.extract_deck('data/cc-unit2-slides.pdf'), '01_deck_parsing/parsed_deck.json')"

# 2. Topics
.venv/bin/python 02_topic_segmentation/run_segmentation.py

# 3. Question mapping (uses the LLM for a few ambiguous questions)
.venv/bin/python 03_question_mapping/run_mapping.py

# 4. Gap reports: all topics, or only some (-1 = the unmatched questions)
.venv/bin/python 04_gap_reporting_agent/run_gap_reporting.py
.venv/bin/python 04_gap_reporting_agent/run_gap_reporting.py --topic-ids 6 12

# 5. Editor: plan and review, then apply
.venv/bin/python 05_agentic_editor/run_editor.py --plan-only
.venv/bin/python 05_agentic_editor/run_editor.py --apply-plan 05_agentic_editor/output/edit_plan.json
.venv/bin/python 05_agentic_editor/run_editor.py --topic-ids 6 12 20    # plan + apply for some topics
.venv/bin/python 05_agentic_editor/run_editor.py --find-broken          # list slides with a broken layout
.venv/bin/python 05_agentic_editor/run_editor.py --repair-slides 5 282  # rebuild slides you confirmed are broken
```

A full stage 4 run makes hundreds of LLM calls. On Groq's free tier
(8,000 tokens per minute, 200,000 per day per model) that means rate-limit
waits, and possibly running out of the daily quota.

---

## Tests

```bash
.venv/bin/python -m pytest -q -m "not slow"   # ~160 fast tests, no network, a few seconds
.venv/bin/python -m pytest -q -m slow         # 6 tests that call the real Groq API (~10 minutes)
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Tests fail with `attempted relative import beyond top-level package` | pytest is 8.1 or newer. Run `pip install "pytest<8.1"` (`requirements.txt` already pins this). |
| `GROQ_API_KEY not set` | Create `.env` from `.env.example`, or paste the key into the demo's sidebar. |
| `Invalid API Key` (401) | The key is wrong, or has a space or quote around it in `.env`. |
| `rate_limit_exceeded` (429), or the demo says the LLM call failed | Free-tier limit. Wait a minute and retry, or switch `GROQ_EDITOR_MODEL_NAME` to the other model (each model has its own quota). |
| `Port 8501 is already in use` | Another demo is running. Close it, or add `--server.port 8502`. |
| "Open it in PowerPoint" does nothing | It needs macOS with PowerPoint. Use "Download the edited deck" instead. |
| The first run of stage 3 or 4 is slow | It downloads the embedding model once (~130 MB). |
| `NotOpenSSLWarning` printed on macOS | Harmless: macOS's built-in Python uses LibreSSL. |

---

## What is not done yet

- Gap finding has not been evaluated against the withheld answer key.
- The editor adds slides, adds bullets to existing slides, and rebuilds
  broken slides. It does not rewrite an existing slide's own text.
- The fact-check and the layout repairs are LLM output: the checks
  catch a lot but not everything, so read every change before approving.
