# 04_gap_reporting_agent — how it actually works

This documents the **as-built** system: what's really implemented, how
it deviates from the original CLAUDE.md spec, and what real API
behavior has taught us. CLAUDE.md is the aspirational design written
before building; this is the "read this to understand the running
code" reference.

---

## 1. What this stage does

Takes Stage 3's `gap_verification_input.json` (23 real topics + a
synthetic "unmatched questions" topic, each with slide content and
matched student questions) and runs one independent AI agent
investigation per topic. Each agent decides for itself, using tools,
whether that topic's slides adequately cover the questions matched to
it — and if not, writes a substantial report explaining what's
missing and what to add.

**Why an agent instead of one fixed LLM call per topic:** a single
call handed a fixed blob of text can't check anything it wasn't
already given. If Stage 2 drew a topic boundary slightly wrong, or a
question's real answer lives one slide outside the topic's range, a
fixed-window approach is permanently stuck with that mistake. An agent
with tools can go check — expand its search window, look elsewhere in
the deck — before concluding. This makes the report resilient to
upstream imperfection instead of fully dependent on Stages 1–3 being
perfect.

## 2. Architecture at a glance

```
gap_verification_input.json (Stage 3)
        │
        ▼
 orchestrator.py ── loops over every topic + the unmatched-questions topic
        │              (each one gets a fresh, isolated investigation)
        ▼
   agent.py ── run_topic_investigation(chat_model, ctx, topic_id, kickoff)
        │        the tool-calling loop for ONE topic
        ▼
   tools.py ── 5 tools, all reading from InvestigationContext
        │        (the deck, Stage 3's dossiers, an embedding model,
        │         two adaptively-computed thresholds)
        ▼
  GapReport (schema.py) ── topic_id, gap_type, confidence, report_text
        │
        ▼
 severity.py ── deterministic, non-LLM ranking → index.json
```

Output: `output/topic_NN_report.json` per real topic,
`output/topic_unmatched_report.json` for the synthetic topic, and
`output/index.json` ranking all of them worst-first.

## 3. Tech stack, and a real deviation from the spec

| Layer | What's actually used |
|---|---|
| LLM | Groq, model name from `.env` (`GROQ_MODEL_NAME`), validated against Groq's live model list at startup — see `common/llm_client.py` |
| LangChain integration | `langchain-groq`'s `ChatGroq`, used for its `.bind_tools()` tool-calling support |
| Agent loop | **Hand-rolled**, not `AgentExecutor` |
| Structured tool calls | `@tool`-decorated functions (`langchain_core.tools`), one with a Pydantic `args_schema` (`write_report`) |
| Tracing | LangSmith, via the three standard `LANGCHAIN_*` env vars — works automatically on every `ChatGroq` call, no special wiring needed |

**The deviation, explained plainly:** CLAUDE.md's original spec named
`create_tool_calling_agent` + `AgentExecutor` as the framework. Both
were removed from LangChain in its 1.0 rewrite (this project installed
LangChain 1.3.18) in favor of LangGraph-based agents. Rather than pin
an old, unmaintained LangChain version to match a spec written against
a now-gone API — the same kind of stale-assumption mistake this
project already hit with Groq's model lineup — `agent.py` hand-rolls
the loop directly against `ChatGroq.bind_tools()`. This isn't really a
compromise: the spec's own Section 8 already describes the
orchestrator as "the dumb dispatcher... look at which tool the model
chose to call and route accordingly," which a hand-rolled loop matches
more directly than either the removed `AgentExecutor` or LangGraph's
`create_react_agent` (neither has a built-in "this specific tool ends
the loop" concept). LangSmith tracing is unaffected either way — it
instruments the underlying `ChatGroq` calls, not `AgentExecutor`
itself.

## 4. The investigation loop, step by step

One call to `run_topic_investigation(chat_model, ctx, topic_id, kickoff_message)`:

1. Build the 5 tools bound to this topic's `InvestigationContext`.
2. Start the message list: `[SystemMessage(SYSTEM_PROMPT), HumanMessage(kickoff_message)]`.
3. Loop up to `MAX_ITERATIONS` (10) times:
   - Call the model. If it calls `write_report` → done, return `"completed"`.
   - If it calls any other tool(s) → execute each, append the results as `ToolMessage`s, loop again.
   - If it replies with plain text (no tool call) → append a nudge ("continue investigating or call write_report") and loop again — this must not be mistaken for the investigation ending.
4. If the loop exhausts all 10 iterations without a `write_report` call: append the forced-conclusion nudge, make one more call. If it now calls `write_report`, its `confidence` is clamped to ≤ 0.4 regardless of what the model sent, and the outcome is `"cap_hit"`. If it *still* doesn't conclude, a minimal report is constructed by the code itself (`confidence=0.0`, explicit "cut short" text) so the topic is never silently dropped.

Every call to the model goes through `_invoke_with_retry`, which
handles two distinct real failure modes (Section 6).

## 5. The 5 tools

All are thin wrappers — none reimplement retrieval, embedding, or
boundary-scoring logic; they call into Stage 1/2/3's existing code via
`_upstream.py`.

- **`get_topic_slides(topic_id)`** — returns the topic's stitched slide
  text, straight from Stage 3's `gap_verification_input.json` dossier
  (`window_text`). For `topic_id=-1` (the unmatched-questions
  synthetic topic) it returns `""` — there's no assigned slide range.
- **`get_matched_questions(topic_id)`** — returns the real student
  questions matched to this topic with their scores. For `-1`, returns
  every question Stage 3 couldn't confidently match anywhere.
- **`search_expanding_context(anchor_slide_id, what_am_i_looking_for)`**
  — grows a window outward from an anchor slide, radius 1 up to 8. At
  each radius it checks two things: is the window's content now
  relevant enough to `what_am_i_looking_for` (`"found"`), or has
  expansion crossed into a different topic (`"hit_topic_switch"`,
  reusing Stage 2's own `block_similarity`/`depth_score` functions as
  the switch signal — not a new heuristic)? Gives up at radius 8
  (`"gave_up_at_max_radius"`) if neither ever triggers. Both
  thresholds are computed adaptively per run (Section 7), not
  hardcoded.
- **`search_similar_slides(query_text, top_k=5)`** — full-deck cosine
  search over every slide's own embedding, independent of topic
  boundaries. This is what catches "the real answer is somewhere else
  entirely" — and it's how the unmatched-questions topic finds
  candidates at all, since it has no slide range of its own.
- **`write_report(topic_id, slide_ids_examined, gap_type, confidence, report_text)`**
  — the terminal tool. `gap_type` is one of `complete_omission`,
  `shallow_coverage`, `fragmented_context`, `covered`. Calling this
  ends the loop; nothing else does.

## 6. Memory model — precisely what it is and isn't

- **Within one topic's investigation**: the growing `messages` list
  itself — every tool call the model made and every result that came
  back — resent to the model on each turn. This is why a model can
  decide on step 6 "I already checked the neighboring slides in step 3
  and found nothing, let me try a full-deck search instead" — it's
  building on what it already gathered, not re-deciding blind.
- **Across topics: none, deliberately.** Each topic (and the
  unmatched-questions topic) gets a fresh `messages` list and a fresh
  `InvestigationContext`-bound tool set. No topic's investigation can
  see another's findings. This keeps each investigation short,
  independently testable, and avoids the old project's documented
  failure mode (small models getting less reliable on longer,
  less-bounded tasks).

## 7. Adaptive thresholds — not hardcoded

Two thresholds drive `search_expanding_context`, both computed fresh
per real run in `orchestrator.build_investigation_context`:

- **`found_threshold`**: the median (50th percentile) of Stage 3's own
  real matched-question cosine scores across the whole deck. "As
  relevant as a typical real match" is a principled, data-derived bar,
  not a guessed constant.
- **`switch_threshold`**: Stage 2's own adaptive segmentation threshold
  (`TopicSegmenter(...).segment(deck)`'s `diagnostics["threshold"]`),
  re-run here rather than persisted from Stage 2's own output, so this
  stays correct even if Stage 2 is later re-run with a different
  embedding model.

Measured on the real deck: `found_threshold≈0.67`,
`switch_threshold≈0.28`.

## 8. Error handling — two distinct real failure modes, both hit in practice

**Rate limits (HTTP 429).** `_invoke_with_retry` retries up to
`MAX_RATE_LIMIT_RETRIES` (5) times. Critically, it reads Groq's **real**
`retry-after` response header rather than guessing a fixed exponential
schedule — a naive 2s/4s guess was measured, in practice, to be
nowhere near the account's actual daily-quota cooldown (real observed
value: 1066 seconds). If retries are exhausted, that topic gets a
`GapReport` with `confidence=0.0` and explicit "failed due to
persistent rate limiting" text — never a crash, never a silently
missing report. **Real, current constraint**: this project's Groq key
is on a 200,000-token **per-day** budget (not a per-minute one — the
per-minute bucket is a separate, much less binding 8,000 TPM), and a
full 24-topic run can burn through most of a day's budget by itself.

**Malformed tool-call JSON (HTTP 400, `tool_use_failed`).** Discovered
against the real API: a model response with a very long `report_text`
occasionally gets cut off mid-generation, producing invalid JSON that
Groq itself rejects — a completely different failure mode from a rate
limit, and retrying the *same* messages would just reproduce it. The
fix: detect it specifically, append a corrective nudge ("your last
call was malformed, likely too long — retry more concisely"), and
continue the loop. The system prompt was also updated to discourage
exhaustive per-question enumeration in `report_text` in the first
place, and `ChatGroq`'s `max_tokens` was raised to 4096 for headroom.

## 9. Severity ranking — the one non-agentic piece

```
severity = GAP_TYPE_WEIGHT[gap_type] * log1p(backed_by_questions) * confidence
```
`GAP_TYPE_WEIGHT`: `complete_omission=3.0`, `fragmented_context=2.0`,
`shallow_coverage=1.0`, `covered=0.0`. Plain arithmetic over each
report's own fields — deliberately not an LLM call, so the ranking is
reproducible and auditable run to run, even though the report content
itself is fully agent-generated.

## 10. Known real-world findings from actual runs

- **The 24-topic full run has not yet completed successfully.** The
  first attempt processed topic 0 for real, then exhausted the day's
  token quota and every subsequent topic failed immediately
  (`rate_limit_failed`). Root cause confirmed directly against Groq's
  own error response, not guessed.
- **Real reports, when they complete, are genuinely substantial and
  well-grounded** — e.g. topic 5's real report cites specific slide
  numbers for both what's covered and what's missing, and correctly
  distinguishes "this is a segmentation issue" from "this content
  genuinely lives elsewhere in the deck."
- **A `--limit N` CLI flag exists** on `run_gap_reporting.py`
  specifically to allow smaller, quota-conscious runs instead of
  always attempting all 23 topics + unmatched in one shot.

## 11. File structure (as built)

```
04_gap_reporting_agent/
  CLAUDE.md                 # the original spec (aspirational, pre-build)
  AGENT_WORKFLOW.md          # this file (as-built reference)
  run_gap_reporting.py       # CLI entry point (--limit N supported)
  src/
    _upstream.py             # bridges to Stages 1/2/3
    schema.py                # GapReport (Pydantic)
    tools.py                 # InvestigationContext + all 5 tools
    prompts.py               # system prompt, kickoff templates, forced-conclusion nudge
    agent.py                 # the hand-rolled tool-calling loop + error handling
    orchestrator.py          # loops over all topics, writes reports, builds the index
    severity.py               # deterministic severity scoring
  tests/
    fixtures/mock_groq_responses.py
    test_tools.py
    test_search_expanding_context.py
    test_agent_loop.py
    test_real_scenarios.py    # marked slow -- hits the real Groq API
  output/
    topic_NN_report.json      # one per real topic
    topic_unmatched_report.json
    index.json
```

## 12. How to run

```bash
# Full run (all 23 topics + unmatched) -- expensive, ~24 real Groq investigations
python "04_gap_reporting_agent/run_gap_reporting.py"

# Scoped run -- only the first N topics (skips the unmatched-questions topic)
python "04_gap_reporting_agent/run_gap_reporting.py" --limit 5

# Unbuffered output (recommended for anything long-running) so progress
# is visible live instead of sitting in a buffer until the process exits:
python -u "04_gap_reporting_agent/run_gap_reporting.py" --limit 5
```

Requires `GROQ_API_KEY` (and optionally `GROQ_MODEL_NAME`,
`LANGCHAIN_API_KEY`/`LANGCHAIN_TRACING_V2`/`LANGCHAIN_PROJECT` for
tracing) in `.env` — see `.env.example`.
