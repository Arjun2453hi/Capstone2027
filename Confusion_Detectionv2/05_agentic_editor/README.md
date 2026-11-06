# 05_agentic_editor

The main editor is now **Margin**, a React workspace backed by a local
FastAPI server. It includes topic search, real source/proposed slide
previews, explicit approvals, PowerPoint exports, layout repairs,
evaluations and durable run history. Groq remains the model provider.

## Start the React workspace

From `Confusion_Detectionv2` (Windows PowerShell):

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
cd 05_agentic_editor/frontend
npm install
npm run build
cd ../..
.venv\Scripts\python 05_agentic_editor/serve.py
```

Open **http://127.0.0.1:8000**. Saved suggestions work without an API key.
For live generation, set `GROQ_API_KEY` in the project's server `.env`;
the key never enters the React client. Generation runs in a one-worker
queue, with progress and cancellation in Activity. Nothing is applied
until you approve changes and click **Export deck**. Export links include
the edited PowerPoint and a change log. The original deck is preserved.

Node 20.19+ or 22.12+ is required for the Vite build; Node is not needed
at runtime after building. On macOS/Linux use `.venv/bin/python`.
Development: start the Python server, then run `npm run dev` in
`frontend/` and open http://127.0.0.1:5173. Vite proxies `/api` locally.

Use `--state-dir path/to/workspace` to open a separate local workspace.
Local proposals, approvals, run checkpoints and exports live under
`.cache/editor-workspace/` and survive restarts. A changed source deck
requires a fresh workspace directory to avoid carrying approvals over
to a different presentation. A failed or interrupted job does not
automatically repeat Groq calls. Pending tasks and run history are visible
in Activity. The server binds only to loopback and is for one local
workspace; the request token is not multi-user authentication.

The harness components, memory strategy, limits and guardrail boundaries
are documented in [HARNESS.md](HARNESS.md).

## Validation

```powershell
.venv\Scripts\python -m pytest 05_agentic_editor/tests -q
cd 05_agentic_editor/frontend
npm run build
# With serve.py running; uses installed Microsoft Edge in headless mode:
npm run test:e2e
```

Set `EDITOR_BROWSER=chrome` or another Playwright channel to use a
different installed browser. `EDITOR_URL` overrides the test server URL.
The browser test checks previews, approval/reset, tabs, evaluation form
download/upload, Activity and desktop/mobile overflow. It makes no model
calls. Screenshots are saved under `.cache/editor-ui/`.

## Legacy Streamlit demo

Setup, API keys and how to run the live demo are in the project README:
[`Confusion_Detectionv2/README.md`](../README.md).

Short version, from `Confusion_Detectionv2`:

```bash
.venv/bin/streamlit run 05_agentic_editor/app.py
```

How the editor works (planner, checks, fact-check, applier) and what it
has achieved so far: [CLAUDE.md](CLAUDE.md).

## Measurable editor evaluations

The React **Evaluation** page and Streamlit **Editor evaluation** section evaluate current suggestions or
saved gap/repair plans, exports a human-review form, and accepts the
completed form. All evaluation runs offline and uses no Groq tokens.

From `Confusion_Detectionv2`, create a report and blank review form:

```powershell
python 05_agentic_editor/evaluate_editor.py --questions data/u2_v2_questions.txt --export-review 05_agentic_editor/output/evaluation/human_review.json
```

This writes `metrics.json` and `report.md` under
`05_agentic_editor/output/evaluation/`. Review export refuses to overwrite
an existing form. Use `--input` to select another plan, including
`output/repair_plan.json` or `output/reviewed_suggestions.json`.

Automatic measurements include the recorded pass rate (accepted / all
saved proposals), blocked count, proportion with revisions, accepted
among revised proposals, and counts by operation. These describe the
saved final proposals; they cannot measure discarded drafts, failed
generation calls, or first-attempt success. Older demo files mix manual
review with the gate results, so their pass rate is not an independent
correctness metric. Mirrored slides count as one proposal.

For independent human evaluation, name the reviewer in the form and set
each edit's four criteria to `true` or `false`:

- **Factual correctness:** statements verified against trusted references.
- **Purpose fulfilled:** the explanation resolves the cited confusion;
  a layout repair preserves the original meaning.
- **Readability:** the rendered slide is clear and readable.
- **Useful change:** adds useful explanation without unnecessary
  duplication, or fixes a broken layout.

All four passing means usable without substantive correction. Leave
unreviewed criteria `null`. Include blocked proposals in the review.
The report shows usable / fully reviewed, review completion, and
confirmed usable / all proposals. It also reports usefulness among
reviewed accepted edits and individual criterion pass rates. Partial
reviews always retain their explicit denominators; no labels means
correctness and usefulness remain unmeasured.

For before/after coverage, identify `original_deck` and `edited_deck` in
the form and assess each question against those actual decks. Set
`before`/`after` to whether the deck adequately answers it. Do not use
the model's `addresses_questions` claims as human labels. Only paired
reviews enter the comparison: coverage change is `100 * (after_count -
before_count) / paired_count` percentage points. Newly answered
questions, regressions, and paired-review completion are also reported.
If only selected proposals were applied, evaluate that actual edited
deck. Proposal usability and applied-deck coverage are distinct measures.

Then recompute:

```powershell
python 05_agentic_editor/evaluate_editor.py --reviews 05_agentic_editor/output/evaluation/human_review.json
```

Reviews are bound to the exact input snapshot by SHA-256. Changing the
plan requires a fresh review form. Keep withheld evaluation answers
outside model prompts and tuning; this evaluator never sends reviews
to the pipeline. For stronger evidence, use independent blinded
reviewers and evaluate beyond the single demonstration deck.
