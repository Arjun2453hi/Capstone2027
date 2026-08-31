# CLAUDE.md — 04_gap_reporting_agent

Context for any Claude session (chat or Claude Code) working inside
`04_gap_reporting_agent/`. Read the project-root `CLAUDE.md`,
`01_deck_parsing/CLAUDE.md`, `02_topic_segmentation/CLAUDE.md`, and
`03_question_mapping/CLAUDE.md` first — this stage consumes all three.

---

## 1. Where this fits — renumbering note

This collapses what were originally planned as two separate stages
(a classify+generate "Gap Verification" step, then a deterministic
"Gap Reporting" aggregation step) into **one agentic stage**. An agent
that investigates before concluding doesn't need a separate
classification pass — classification is just one field in what it
produces after investigating. This is now Stage 4 of 5;
`06_agentic_editor` from earlier planning becomes `05_agentic_editor`.

| # | Stage | Status |
|---|---|---|
| 1 | `01_deck_parsing` | Built, one bugfix pending re-verification |
| 2 | `02_topic_segmentation` | Built, threshold fix in progress |
| 3 | `03_question_mapping` | Built |
| 4 | `04_gap_reporting_agent` | **This stage. Not built.** |
| 5 | `05_agentic_editor` | Not designed yet — consumes this stage's per-topic reports directly |

## 2. Why this is an agent, not a fixed pipeline step

Earlier designs used one-shot classify-then-generate: hand the model a
fixed window of text, get one answer back. That's fragile against
exactly the failures this project has already hit — a fixed context
window missing real content just outside it, or a segmentation
boundary drawn slightly wrong. An **agent** is given tools to actively
investigate instead of being handed everything upfront: it can check
neighboring slides, search the whole deck, and decide for itself when
it has enough information before writing anything. This makes the
report **resilient to upstream imperfection** in Stages 1-3, instead
of being permanently stuck with whatever those stages produced.

**The final output is a full, verbose, LLM-written report per topic**
— not a short structured label. The tools' entire purpose is gathering
evidence; the actual writing happens once the model itself decides
it's investigated enough, guided by an explicit checklist in the
system prompt (Section 7). Nothing about "use tools first" is meant to
produce a thin report — it's the opposite: better-gathered evidence
should produce a *more* substantial, specific write-up than handing
the model a fixed blob of text ever could.

## 3. Tech stack

| Layer | Choice | Why |
|---|---|---|
| LLM | **A real, currently-valid Groq model — see Section 4, do not hardcode a guess** | This project has already broken once from a hardcoded model name going stale |
| Agent framework | **LangChain** (`langchain-groq`, `create_tool_calling_agent`, `AgentExecutor`) | Handles the tool-calling loop, scratchpad memory, and structured tool-call parsing — chosen explicitly over hand-rolling for flexibility and because tracing (LangSmith) comes free with it |
| Tracing | **LangSmith — required, not optional** | Every LLM call and tool call automatically captured; this is how the agent's *reasoning process* gets inspected, not just its final answer — non-negotiable for trusting agent output |
| Structured terminal output | Pydantic model bound to the `write_report` tool schema | Guarantees the terminal answer is always valid, parseable — the model can write freely in the `report_text` field, but the surrounding metadata is always well-formed |
| Memory (within one topic) | LangChain `AgentExecutor`'s built-in scratchpad | Automatic — every tool call and result in this topic's investigation accumulates and is visible to the model on every subsequent turn. No custom code needed. |
| Memory (across topics) | **None, deliberately, for v1** | Each topic gets a fresh, isolated agent session. Keeps investigations short and independently testable; this project has direct evidence (the old generator's 2/27 real success rate) that small models get less reliable on longer, less-bounded tasks. Revisit only if genuine cross-topic gaps are found in practice, not speculatively. |
| Loop control | `AgentExecutor(max_iterations=10)` | Declarative hard cap — see Section 8 for the forced-conclusion behavior when hit |

## 4. Groq model — must be validated, not guessed

**Do not hardcode a model name in this module.** Reuse the shared Groq
client and startup validation already built in `common/` (see the
project's Bugfix doc — Groq previously broke when
`llama-3.3-70b-versatile` was silently removed from Groq's hosted
lineup). Concretely:
- Import the shared client from `common/llm_client.py` — do not
  instantiate a second, separate `ChatGroq` client here with its own
  hardcoded name.
- Read the model name from `.env` (`GROQ_MODEL_NAME`), same variable
  `03_question_mapping` already uses (`openai/gpt-oss-20b` at time of
  writing) — reuse the same validated model, don't introduce a second
  one for no reason.
- The startup validation check (confirming the configured model still
  exists in Groq's live model list) must run before this stage begins
  processing any topic — fail loudly and immediately if it doesn't,
  not partway through an 18-topic run.

## 5. Memory — precisely what it is and isn't

**Within one topic's investigation**: "memory" is the growing list of
messages — the agent's own tool-call decisions and the results that
came back — that `AgentExecutor` automatically maintains and resends
to the model on every turn. This is *why* the model can decide, on
step 6, "I already checked the adjacent slides in step 3 and found
nothing, so now I should try a full-deck search instead" — it's not
re-deciding blindly, it's building on what it already gathered.

**One of the tools (`search_expanding_context`) actively uses this
memory as an input, not just as passive context**: its
`what_am_i_looking_for` argument should be something the model
constructs *from what it has already learned* in this investigation —
e.g., after reading the topic's slides and noticing dropout is
mentioned but never explained, it should call this tool with
`what_am_i_looking_for = "explanation of how dropout works during
training vs inference"`, not a generic restatement of the topic title.
This isn't a separate mechanism to build — it's the natural
consequence of tool-calling once the system prompt (Section 7) makes
clear that's how the argument should be constructed. Write the tool's
own docstring to reinforce this too, since the model reads that
docstring directly.

**Across topics**: no shared memory. Each of the ~18 topic
investigations is a fresh `AgentExecutor` run with no visibility into
any other topic's findings.

## 6. Tools

All five are thin LangChain `@tool`-decorated wrappers around logic
that **already exists elsewhere in the pipeline** — this module should
not reimplement retrieval, embedding, or boundary-scoring logic.

### `get_topic_slides(topic_id: int) -> str`
Returns the topic's own slide range's stitched text, from Stage 1's
parsed JSON via Stage 2's topic boundaries. The obvious first call in
any investigation.

### `get_matched_questions(topic_id: int) -> List[dict]`
Returns the questions Stage 3 matched to this topic, each with its
similarity score.

### `search_expanding_context(anchor_slide_id: int, what_am_i_looking_for: str) -> dict`
The tool that directly fixes the exact failure this project has hit
twice already (fixed-radius windows missing real content one slide
away; Stage 2 boundaries drawn slightly wrong). Grows outward from an
anchor slide, checking at each radius whether it's found something
relevant or crossed into a genuinely different topic:

```python
def search_expanding_context(anchor_slide_id: int, what_am_i_looking_for: str) -> dict:
    radius = 1
    while radius <= MAX_RADIUS:  # pin MAX_RADIUS, e.g. 8
        window = get_slides_in_range(anchor_slide_id - radius, anchor_slide_id + radius)
        relevance = cosine_similarity(
            embed(window.text),           # reuse common/embeddings
            embed(what_am_i_looking_for),
        )
        # reuse 02_topic_segmentation's own boundary-scoring logic as the
        # switch signal — do not reimplement a separate "is this a
        # different topic" heuristic here
        switch_signal = topic_segmentation.combined_boundary_score(anchor_slide_id, radius)

        if relevance > FOUND_THRESHOLD:      # pin a concrete value, computed adaptively per Stage 3's lesson, not guessed
            return {"status": "found", "window_slide_ids": window.slide_ids, "relevance": relevance}
        if switch_signal > SWITCH_THRESHOLD:  # reuse Stage 2's own adaptive threshold, don't invent a new one
            return {"status": "hit_topic_switch", "window_slide_ids": window.slide_ids, "relevance": relevance}
        radius += 1

    return {"status": "gave_up_at_max_radius", "window_slide_ids": window.slide_ids}
```

`FOUND_THRESHOLD` and `SWITCH_THRESHOLD` must be computed adaptively
from real score distributions, the same lesson Stage 2 and Stage 3
both already had to learn the hard way — do not hardcode a guessed
constant here.

### `search_similar_slides(query_text: str) -> List[dict]`
Full-deck semantic search, reusing Stage 3's retriever directly — not
a new embedding index. This is the tool that catches a genuine
"the real answer is somewhere completely different" case. **Confirmed:
freely searchable across the whole deck, including slides that belong
to other topics' own ranges, and free to grow/expand across slides as
needed** — if two different topic-agents both independently surface
the same distant slide, that's acceptable redundancy, even useful as
informal cross-validation, not a bug to prevent.

### `write_report(...)` — terminal tool, ends the investigation
```python
class GapReport(BaseModel):
    topic_id: int
    slide_ids_examined: List[int]      # traceable evidence of what was actually checked
    gap_type: Literal["complete_omission", "shallow_coverage", "fragmented_context", "covered"]
    confidence: float                   # 0-1, the model's own honest assessment
    report_text: str                    # the actual verbose write-up — several paragraphs
```
`report_text` is where the real generation happens: what was checked,
what was found, exactly what's missing and why it matters, and —
when genuinely confident — concrete suggested content to add. This is
the terminal action; calling it ends the loop (`AgentExecutor`
recognizes it as the finishing tool, not another information-gathering
step).

## 7. Prompts

**System prompt** (sent once per topic investigation):
```
You are a curriculum gap-detection investigator. You will be given one
topic from a lecture deck, its slide range, and real student questions
matched to this topic by semantic similarity.

Investigate before concluding. Do not call write_report until you have:
1. Read the topic's own slide content (get_topic_slides).
2. Reviewed the actual student questions matched to it
   (get_matched_questions).
3. If the slides seem to only partially address a question, used
   search_expanding_context to check whether the fuller explanation
   exists just outside this topic's boundary. Construct your
   "what_am_i_looking_for" argument from what you've specifically
   noticed is missing or thin — not a generic restatement of the
   topic's title.
4. If a question seems entirely unaddressed by this topic and its
   neighbors, used search_similar_slides to check the rest of the deck
   before concluding it's a true omission rather than a segmentation
   error.

You have at most {MAX_STEPS} tool calls before you must conclude with
whatever you've gathered.

When ready, call write_report with a genuinely substantial write-up:
what you checked, what you found, and — if you're confident — a
concrete suggestion for what to add and where. A lower-confidence
report that's honest about uncertainty is more useful than a
confident-sounding guess that turns out wrong.
```

**Per-topic kickoff message:**
```
Topic {topic_id}, slides {start}-{end}.
{N} questions matched to this topic by semantic similarity:
{question list with scores}

Investigate whether this topic's slides adequately answer these
questions, then write your report.
```

**Forced-conclusion nudge** (only fires if `max_iterations` is hit
without the model calling `write_report` on its own):
```
You've reached the maximum investigation steps. Call write_report now
using only what you've already gathered. Confidence must not exceed
0.4, and report_text must explicitly note the investigation was cut
short before you could fully verify your conclusion.
```

## 8. Orchestration — the dumb dispatcher, precisely

`AgentExecutor` does not evaluate whether the model has "enough
information" — that judgment lives entirely in the model, driven by
the system prompt's checklist. The orchestrator's only job is to look
at *which* tool the model chose to call and route accordingly:
information-gathering tools get executed and their results appended
to the scratchpad, looping again; `write_report` is recognized as
terminal and stops the loop immediately, returning the validated
`GapReport`. The one place the orchestrator does override the model:
the hard `max_iterations` cap, which forces a stop and the
forced-conclusion prompt if the model never decides to conclude on its
own — a safety net, not the primary mechanism.

## 9. Orchestrating across all topics + unmatched questions

- Run one fresh, isolated `AgentExecutor` session per topic from Stage
  2/3 (~18 total on the real deck).
- **Unmatched questions get their own new topic, investigated the same
  way as any real topic — not a lightweight side bucket.** Collect all
  of Stage 3's `unmatched_questions` into one synthetic topic:
  ```python
  UnmatchedTopic:
      topic_id = -1
      start_slide_id = None
      end_slide_id = None
      slide_ids = []              # deliberately empty — no assigned range
      questions = unmatched_questions
  ```
  This topic runs through the exact same `AgentExecutor` loop and the
  exact same 5 tools, with one adjustment: since there's no assigned
  slide range, `get_topic_slides(-1)` returns empty, and the kickoff
  message must say so explicitly — the agent's *first* real move here
  should be `search_similar_slides` per question (not
  `get_topic_slides`), to find whatever candidate slides exist at all,
  before optionally using `search_expanding_context` anchored at
  whatever it finds to confirm or rule out real coverage. Add a
  topic-specific variant kickoff message:
  ```
  {N} questions did not confidently match any topic in this deck:
  {question list}

  There is no assigned slide range for this investigation. Use
  search_similar_slides for each question first to find whatever
  candidate content might exist anywhere in the deck, then use
  search_expanding_context if a candidate looks partially relevant, to
  check whether fuller coverage exists nearby. Only conclude
  complete_omission for a question if a genuine full-deck search turns
  up nothing relevant at all.
  ```
  This produces a real `GapReport` for `topic_id: -1` like every other
  topic — written into `output/topic_-1_report.json` (or
  `unmatched_topic_report.json`, pick one consistent naming) and
  included in `index.json`'s severity ordering like any other finding.
  This replaces the earlier lighter-weight "Unresolved Questions
  bucket" design — nothing here should be treated as second-class or
  skip the actual investigation.

## 10. Output — reports per topic (the actual deliverable)

**The primary output is one `GapReport` per topic — not a single
merged narrative document.** Store them as individual files, including
the unmatched-questions topic as a real report like any other:
```
04_gap_reporting_agent/output/
  topic_00_report.json
  topic_01_report.json
  ...
  topic_17_report.json
  topic_unmatched_report.json    # the -1 synthetic topic from Section 9 — a real GapReport, not a side file
  index.json                      # see below
```

**A lightweight, deterministic index/manifest is a secondary,
convenience artifact only** — it does not replace the per-topic
reports, it just orders them:
```python
severity(report) = GAP_TYPE_WEIGHT[report.gap_type] * log1p(backed_by_questions) * report.confidence
```
computed the same way this project's earlier reporting design always
did — plain arithmetic, not an LLM opinion, so the ordering is
reproducible and auditable. `index.json` lists all topic report
filenames sorted worst-first by this score. **This severity
computation is the one deliberately non-agentic, non-LLM piece of this
whole stage** — the actual per-topic content is fully agent-generated,
but ranking that content should not itself be subject to model
variance between runs.

## 11. Rate limits and API error handling — required, not optional

18+ topic investigations (19 including the unmatched topic), each up
to 10 LLM calls, means up to ~190 real Groq API calls in one run.
Cheap and fast on Groq specifically, but a silent failure partway
through — a rate limit hit at topic 12 that just hangs or crashes
without explanation — would waste the whole run and be confusing to
debug. Build this in from the start:

- **Detect rate-limit responses specifically** (Groq returns a
  standard HTTP 429 with retry-after information) — don't let this
  surface as a generic, unexplained exception.
- **Exponential backoff with a small number of retries** (e.g. 3
  attempts, doubling wait time) on a detected rate limit, logged
  clearly each time ("rate limit hit on topic 7, step 4 — waiting Xs,
  attempt 2/3") — not a silent retry, and not an infinite one.
- **If retries are exhausted**: stop that topic's investigation
  cleanly, write a `GapReport` for it with `confidence=0.0` and
  `report_text` explicitly stating it failed due to persistent rate
  limiting (not a content judgment) — don't let one topic's API
  failure crash the entire multi-topic run or silently produce a
  missing report with no explanation.
- **At the end of a full run**, print a clear summary: how many topics
  completed normally, how many hit the forced-conclusion step cap
  (Section 8), and how many failed due to rate limits/API errors —
  three genuinely different outcomes that should never be conflated
  in the final output.

**Testing**: mock a Groq client that returns a 429 on a specific call,
and assert the backoff/retry logic actually triggers with the right
wait behavior, and that exhausting retries produces the clearly-marked
failed `GapReport` described above rather than an unhandled exception.

## 12. Testing — scenario-based, trace-inspecting, not just output-checking

You cannot test an agent the way earlier deterministic stages were
tested — asserting "output equals X" misses whether the *process* that
got there was sound.

- **Fast/deterministic tests** (majority of the suite, mirroring Stage
  3's 7-fast/3-real split): mock Groq's responses with scripted
  tool-call sequences, testing `AgentExecutor`'s routing logic, the
  `max_iterations` cap and forced-conclusion behavior, and
  `search_expanding_context`'s radius-growth logic against constructed
  embedding sequences — none of this needs a real LLM call.
- **Real scenario tests** (a small number, hitting the real Groq API):
  run the full agent against 2-3 topics corresponding to the original
  test deck's 7 hand-verified deliberate gaps (from the earlier,
  separately-generated ground truth — still withheld from the pipeline
  itself, but usable here as an external check). Check both the final
  `gap_type` **and** the LangSmith trace — did it actually call
  `get_topic_slides` and genuinely investigate, or did it call
  `write_report` after one lazy look? A wrong process that happens to
  land on the right label is a trap this test is specifically meant to
  catch.
- **Cap test**: deliberately construct a case designed to be confusing
  or ambiguous, confirm the agent hits `max_iterations` gracefully and
  produces a clearly-flagged, low-confidence report rather than
  erroring or hanging.
- **Manual trace review, not just automated assertions**: read a
  handful of real LangSmith traces by eye before trusting the stage's
  output on the real 18-topic deck — this is genuinely part of testing
  an agent, not optional polish.

## 13. File structure

```
04_gap_reporting_agent/
  CLAUDE.md
  src/
    tools.py              # all 5 @tool-decorated functions, thin wrappers over existing pipeline logic
    schema.py                # GapReport (Pydantic)
    prompts.py                 # system prompt, kickoff template, forced-conclusion nudge
    agent.py                     # builds the AgentExecutor, runs one topic's investigation
    orchestrator.py                 # loops over all topics + unmatched-question handling
    severity.py                        # deterministic index/manifest scoring (Section 10)
    run_gap_reporting.py                  # CLI entry point
  tests/
    fixtures/
      mock_groq_responses.py    # scripted tool-call sequences for fast tests
    test_tools.py
    test_agent_loop.py             # cap test, forced-conclusion test
    test_search_expanding_context.py
    test_real_scenarios.py            # the 3 real-API tests against known gaps
  output/                                # per-topic reports land here (Section 10)
```

## 14. Prompt to run

```
Build 04_gap_reporting_agent per this CLAUDE.md.

Specifically:
1. Confirm common/llm_client.py's Groq startup validation (from the
   Bugfix doc) is reusable here as-is — wire this stage to it, do not
   create a second Groq client.
2. Write schema.py: the GapReport Pydantic model per Section 6.
3. Write tools.py: all 5 tools per Section 6, each a thin wrapper —
   get_topic_slides and get_matched_questions read from Stage 1/2/3's
   existing outputs; search_expanding_context reuses
   02_topic_segmentation's own boundary-scoring function as its switch
   signal (do not reimplement); search_similar_slides reuses Stage 3's
   retriever directly.
4. Compute FOUND_THRESHOLD and SWITCH_THRESHOLD adaptively from real
   score distributions on the actual deck, per Section 6 — do not
   hardcode guessed constants, this project has already had to fix
   that mistake twice.
5. Write prompts.py per Section 7, verbatim.
6. Write agent.py: build the LangChain AgentExecutor
   (create_tool_calling_agent + AgentExecutor(max_iterations=10)),
   wired to the shared Groq client and all 5 tools.
7. Write orchestrator.py: loop over all real topics from Stage 2/3,
   handle unmatched questions per Section 9, write each topic's
   GapReport to output/.
8. Write severity.py and generate index.json per Section 10.
9. Set up LangSmith tracing (the 3 required env vars) and confirm
   traces are actually appearing for a test run before proceeding to
   the real run.
10. Implement rate-limit detection, backoff/retry, and the
    clearly-marked failed-report fallback per Section 11 — test this
    with a mocked 429 response before running against the real deck.
11. Write the full test suite per Section 12, including the real
    scenario tests against the 3 known hand-verified gaps.
12. Run against the real 18-topic deck output from Stage 2/3 (19
    including the unmatched-questions topic), and show me: all
    GapReports (or at least the top 5 by severity in full, plus the
    unmatched topic's report in full), the end-of-run summary counting
    normal/cap-hit/rate-limit-failed outcomes, and 2-3 full LangSmith
    traces so I can inspect the actual investigation process, not just
    the final text.
```