# Editor harness

The harness is the application code around the model: it supplies the task
and evidence, restricts actions, validates results, manages failure and
memory, and records what happened. A prompt by itself is not a harness.

## Components implemented here

| Component | Implementation |
|---|---|
| Task orchestration | One local worker; up to four active/queued tasks; one topic or confirmed repair per task; duplicate active requests rejected |
| Model adapter | Groq, with SDK generation retries disabled; one bounded application retry policy |
| Task instructions | Planner, verifier and revision roles; evidence serialized as source data under explicit trust-boundary instructions |
| Working/context memory | Rebuilt per request from original slides, relevant questions, report and current proposal; no accumulating conversation archive |
| Retrieval/compaction | Question-term relevance or anchor proximity; exact duplicate excerpts replaced by source references; hard 6,000-character serialized slide budget including labels |
| Action interface | Typed insert-slide, append-bullets and repair-slide operations; the model cannot execute commands, choose file paths, or directly write a presentation |
| Validation | Operation, slide/topic membership, source-question provenance, length, duplication, repair word preservation, credential/control-character checks |
| Semantic review | Separate fact-check request; malformed/failed checks block acceptance; at most two correction rounds |
| Human control | Explicit approval persisted by exact proposal content; blocked edits cannot be approved/exported through the API; deterministic checks rerun on approval and export |
| Execution budgets | Call, estimated token, response-size and time limits; finite retries; rate-limit wait ceiling; circuit breaker |
| Cancellation | Cancellation event interrupts retry waits and prevents committing cancelled work; in-flight HTTP calls end within their request timeout |
| Durable memory | Local SQLite WAL store for proposals, approvals, runs, events and task checkpoints; interrupted runs marked after restart, never automatically replayed |
| Traceability | Metadata for requests, actual provider usage when available, retries, initial/revision check counts, cancellation, checkpoints, decisions and exports |
| Evaluation | Independent editor evaluator and human review forms; model claims are not ground-truth coverage labels |
| Local boundary | Loopback server, allowed hosts/origins, per-server mutation token, 1 MB request cap, escaped React text, fixed export destinations |

## Memory strategy

**Working memory:** Keep source text immutable. Reserve source labels before
allocating excerpts, rank evidence by question relevance or distance from
the insertion anchor, and collapse exact duplicate text while retaining
slide IDs. The whole slide-context string fits its budget. Truncation and
omission markers are evidence limitations, not proof of missing coverage.
Image-only slides are marked explicitly. Report excerpts are bounded;
the full report remains available in the workspace.

**Revision memory:** Each correction sees the current proposal, its current
findings and bounded original evidence, rather than every previous draft.
Newly generated facts are never promoted into a trusted knowledge store.
Only previously approved edits are supplied to cross-proposal duplicate
checks in the React workspace.

**Durable task memory:** SQLite stores final proposals and user decisions,
not the conversation or API keys. Identity is content-based: changing a
proposal produces a different ID and requires fresh approval. Completed
proposals survive restarts. A task interrupted before checkpointing does
not silently resume or regenerate. A changed source PowerPoint requires a
fresh workspace state directory; export also verifies the source checksum.

**Resource memory:** Preview PNGs use an LRU cache with a 12 MB byte limit,
12-entry limit and five-minute TTL. Open PDF pages, bitmaps, PIL images and
SQLite connections are closed. Rendering is serialized. Metadata retains
100 recent runs and at most 150 events per run; oldest unapproved proposals
are pruned beyond 300 recent entries. Approved proposals are retained.
Export files are retained locally for the user and are not automatically
deleted. These are cache/context bounds, not a hard process RSS limit.

## Default execution policy

| Limit | Default |
|---|---:|
| Provider generation attempts per run, including retries | 18 |
| Estimated input + reserved output per request | 8,000 tokens |
| Estimated cumulative reservations per run | 80,000 tokens |
| HTTP request timeout | 45 seconds, capped to remaining deadline |
| Run deadline after worker starts | 360 seconds |
| Application attempts per request | 2 |
| Longest automatic retry wait | 30 seconds |
| Consecutive failures before circuit opens | 3 |
| Maximum output payload | 48,000 bytes |

Token preflight uses serialized UTF-8 bytes / 3 plus overhead. It is a
conservative heuristic for this English workload, not an exact tokenizer
or an authoritative Groq quota. Usage returned by Groq is recorded
separately. A long retry-after causes a visible failure rather than a
many-minute hidden wait. The budgets include retries and reasoning/output
reservation. The model-list startup check is not a generation call.

Every field in `HarnessPolicy` can be overridden in the server `.env`
using `EDITOR_HARNESS_` plus its uppercase field name, for example:

```dotenv
EDITOR_HARNESS_MAX_CALLS=18
EDITOR_HARNESS_MAX_REQUEST_TOKENS=8000
EDITOR_HARNESS_MAX_RUN_ESTIMATED_TOKENS=80000
EDITOR_HARNESS_REQUEST_TIMEOUT_SECONDS=45
EDITOR_HARNESS_DEADLINE_SECONDS=360
EDITOR_HARNESS_MAX_RETRY_DELAY_SECONDS=30
```

Tune request limits to the actual model/account limits, with the evaluator
and recorded failures guiding changes. Increasing a budget does not
increase Groq's provider quota. Invalid/nonpositive settings fail startup;
attempts cannot exceed three. CLI/Streamlit tasks receive fresh budgets
per topic, shared between generation, fact-check and revision.

## Guardrail boundaries

Prompt-injection instructions are untrusted even when embedded in a slide,
report or student question. Data separation and explicit instructions help,
but are not a guarantee that an LLM will ignore every injection. The actual
authority boundary is code: no arbitrary tools, scripts, paths, unrestricted
edits or automatic application. The API accepts stored proposal IDs only.
Deterministic checks do not establish technical truth. A separate model
fact-check can share the generator's mistakes; source decks can contain
errors, and text extraction cannot fully assess diagram meaning. Instructor
review and the human evaluation rubric remain necessary.

The service is for one local workspace. The mutation token is CSRF
protection, not multi-user authentication. Do not expose it as an internet
or LAN service without authentication, per-user storage, deployment hardening
and appropriate operational limits. Server-side credentials stay in `.env`;
no API key is entered in React or stored in browser localStorage. Persisted
request traces omit prompts, bodies and raw provider errors. Proposal text
and human decisions are necessarily stored as workspace artifacts.

References: [Groq SDK retry/timeout controls](https://github.com/groq/groq-python),
[OWASP prompt-injection guidance](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html),
[FastAPI lifespan management](https://fastapi.tiangolo.com/advanced/events/).
