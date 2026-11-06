# LLM evaluation and local inference research

Research date: 5 November 2026. This is a proposed evaluation design, not measured results. No provider or runtime configuration has been changed.

## Recommendation

Add an offline evaluation harness for question mapping, gap detection, and slide improvement. Start with deterministic metrics and human labels; add a local LLM judge as supporting evidence. Benchmark Ollama with Qwen3 4B and 8B before choosing a replacement for Groq.

Local inference removes the dependency on Groq's request/token quotas. It still uses local RAM/VRAM, compute time, storage, and electricity. Downloading open weights does not make a hosted inference service unlimited or free.

## Findings from this repository

- Stage 3 maps student questions to deck topics and calls an LLM for ambiguous cases.
- Stage 4 uses a LangChain tool-calling loop to investigate gaps.
- Stage 5 generates structured slide edits and performs deterministic checks and an LLM fact-check.
- The shared client is in `common/llm_client.py`; it constructs Groq-specific clients and a LangChain ChatGroq instance. Switching providers needs adapter and configuration work, rather than only changing a model name.
- The README says gap finding has not been evaluated against the withheld answer key. Existing tests and quality gates do not establish accuracy on that key.
- Root `CLAUDE.md` explicitly reserves the answer key for later evaluation. Keep it outside prompts, retrieval, and tuning. Use a separate development set for choosing prompts and thresholds; freeze those choices before the final held-out run.
- The input includes duplicated lecture sections. Equivalent relevant slides/topics should receive credit, and duplicate sections must stay together when splitting data to avoid leakage.

## Evaluation methods to add

| Component | Proposed measures | Required evidence |
|---|---|---|
| Question-to-topic mapping | Top-1 accuracy, Recall@3, MRR; precision/recall for rejecting out-of-scope questions | Human-labeled acceptable topic/slide IDs, allowing multiple equivalent matches |
| Gap detection | Binary gap precision, recall, F1, confusion matrix; macro-F1 for gap types where labeled | Independently labeled gap/no-gap decisions and gap type at the same granularity as predictions |
| Report evidence | Correct cited-slide rate, evidence relevance, unsupported-claim rate | Review whether evidence actually supports each conclusion; a valid slide ID alone is insufficient |
| New slide quality | Question coverage, factual correctness, clarity, useful new content, duplication | Fixed human rubric; optionally a calibrated LLM judge |
| Agent behavior | Task completion, valid tool arguments, invalid tool-call rate, unnecessary calls, loop-limit failures | Saved tool traces and manually specified task expectations |
| Runtime reliability | JSON/schema success, first-attempt and final success, retry count, p50/p95 latency, tokens, quota failures | Per-request logs, including failed and blocked outputs |
| End-to-end improvement | Coverage before and after edits; instructor acceptance; added-slide count | Same questions assessed on original and edited decks with blinded review |

Gap precision answers: of the gaps flagged, how many are real? Gap recall answers: of the real gaps, how many were found? F1 balances these. Report class counts, since accuracy can hide poor gap detection when most questions are already covered. A topic-level verdict must not silently be scored as a separate prediction for every question without a justified conversion rule.

### RAG-style evaluation

[Ragas](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) provides context precision/recall, response relevancy, faithfulness, factual correctness, and agent/tool metrics. These can inform evaluation of retrieved slide evidence and generated content. Some metrics require reference answers or judge LLM calls; the free framework alone does not make judging free.

For this project, faithfulness measures support from the supplied evidence. It does not establish external factual truth. A missing explanation may legitimately require content absent from the original deck: mark it as requiring verification and check it against instructor-approved references, rather than automatically treating every new claim as false. Conversely, consistency with a mistaken original slide is not factual correctness.

[DeepEval](https://github.com/confident-ai/deepeval) is another framework to consider for rubric-based and regression evaluations. Begin with the existing scikit-learn dependency and a small evaluator; add one LLM evaluation framework when its metrics are needed.

### Human and LLM judging

Use a 1–5 rubric for correctness, relevance to the student's confusion, clarity, and useful added explanation. Reviewers should score independently; reconcile disagreements and report agreement. Blind model names and randomize original/edited order.

Calibrate any automated judge against human scores on a separate development sample. Prefer a different judge model from the generator where feasible, but do not assume that this guarantees independence. Run pairwise comparisons in both orders. [LLM-as-a-judge research](https://arxiv.org/abs/2306.05685) documents position, verbosity, and self-enhancement biases. A small local judge should not be the sole authority for technical correctness.

## Free local model candidates

| Candidate | Verified distribution | Proposed use |
|---|---|---|
| Qwen3 4B | Ollama `qwen3:4b`, Q4_K_M, approximately 2.5 GB download, Apache 2.0 | Lower-memory baseline for mapping and constrained generation |
| Qwen3 8B | Ollama `qwen3:8b`, Q4_K_M, approximately 5.2 GB download, Apache 2.0 | Main candidate to benchmark for investigation and slide generation |

Sources: [4B listing](https://ollama.com/library/qwen3:4b), [8B listing](https://ollama.com/library/qwen3:8b), [publisher's 8B model card](https://huggingface.co/Qwen/Qwen3-8B).

Download sizes are not total runtime memory requirements. Context length, inference cache, simultaneous requests, OS usage, and CPU/GPU placement affect memory and speed. Hardware suitability and quality have not been tested here; no claim is made that either matches the current Groq-hosted models. Benchmark the exact quantized artifact that would be deployed.

Ollama documents [tool calling](https://docs.ollama.com/capabilities/tool-calling), [structured outputs](https://docs.ollama.com/capabilities/structured-outputs), and an [OpenAI-compatible API subset](https://docs.ollama.com/api/openai-compatibility). These capabilities fit the pipeline's interface needs, but tool selection, argument correctness, final response parsing, and long-context behavior still need project-specific verification. Use local inference rather than Ollama cloud when eliminating hosted quotas is the goal.

After installing Ollama, a manual smoke test would be:

```powershell
ollama run qwen3:4b
# Benchmark the larger candidate separately:
ollama run qwen3:8b
```

## Proposed integration and experiment

1. Add shared provider selection (`groq` or `ollama`) with separate generation and evaluation model settings. Keep Groq available for comparison. Stage 4 needs a LangChain-compatible local tool-calling adapter; stages 3 and 5 need structured-output support. Audit Groq-specific retry, validation, monitoring, and UI key requirements.
2. Build development labels without accessing the withheld answer key. Include supported questions, partial coverage, genuinely missing explanations, out-of-scope questions, duplicate topics, and image-only evidence. Text-only evidence cannot establish what a diagram explains; record that limitation.
3. Compare the existing embedding-only mapping baseline, the current Groq pipeline, Qwen3 4B, and Qwen3 8B using identical inputs, evidence budgets, tool budgets, and scoring. Record provider/model/version, quantization, prompts, decoding settings, context settings, hardware, seeds where supported, and all failures.
4. Add ablations: mapping with/without LLM fallback, gap investigation with/without tools, and editor with/without correction retries. Do not apply ablation outputs to the production deck.
5. Repeat a subset to measure consistency. Report raw counts and confidence intervals, recognizing that questions from one topic/deck are correlated; use topic-grouped uncertainty estimates where possible. One deck is a pilot, not evidence of cross-course generalization.
6. Freeze choices, then evaluate on the separately held answer key through an evaluator that cannot feed labels back into inference. Report both end-to-end performance and editor quality conditional on a correctly identified gap.

Practical first milestone: an offline score report for mapping and gap detection, a human rubric for generated slides, and a small Groq-versus-local comparison. Choose the deployment model from those measurements, rather than model size or general leaderboard scores alone.

Additional references: [scikit-learn evaluation metrics](https://scikit-learn.org/stable/modules/model_evaluation.html), [Groq rate-limit documentation](https://console.groq.com/docs/rate-limits).
