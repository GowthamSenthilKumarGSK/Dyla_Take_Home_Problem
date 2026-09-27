# DECISIONS.md

## Architecture

**Separate Analyst and Auditor agents.** The Analyst researches and answers; the Auditor independently re-fetches every cited source and verifies each claim. Independence is the point — the Auditor does not trust the Analyst's fetched content.

**Evidence-aware research loop** (up to 3 rounds). After each round of search → source selection → page fetch, the Analyst evaluates whether the collected evidence is sufficient. If not, it generates a targeted follow-up query. This replaced an earlier tool-calling loop where the LLM controlled search/fetch directly — the structured loop is more predictable and observable. Note: the included evaluation traces (`logs/q*_trace.json`) predate this change and show the original tool-calling loop with qwen2.5:7b. The evidence-aware loop is in the current code and tested (22 tests) but was not re-evaluated due to time and provider constraints.

**Deterministic source selection.** Sources are ranked by Tavily relevance score plus domain-quality heuristics (.gov/.edu boost, Pinterest/Quora penalty). No LLM call is needed for selection, making this step fast, reproducible, and auditable.

## Alternatives Rejected

- **Groq**: fast inference but free-tier token-per-minute limits (7K-8K) are too low — a single search result fed back into context exhausts the budget.
- **Gemini API**: project-level access denied on all generation endpoints despite valid key.
- **NLP-based entity extraction** (spaCy/NLTK): would add ~500MB of dependencies. Regex-based proper-noun detection is adequate for the entity types in these questions.
- **LLM-controlled tool loop** (initial approach): the model decided when to search and what to fetch. Smaller models often failed to call `fetch_page` at all, or echoed tool-call JSON as their answer. The evidence-aware loop calls tools directly and only uses the LLM for planning, evaluation, and final answer generation.

## Key Trade-offs

- **Memory stores only cited claims.** This prevents hallucinations from polluting the entity store but means uncited answers (common with weaker models) contribute nothing to memory.
- **Provider fallback is sticky.** Once a fallback triggers, all subsequent calls in that question use the working provider. This avoids repeated failures but means a temporarily unavailable provider isn't retried within the same question.
- **Auditor source fetches are parallel; LLM verification is sequential.** HTTP fetches are I/O-bound and independent. LLM calls could theoretically be parallelized, but the three-tier fallback and rate limits make concurrent LLM calls risky on the free tier.

## What Broke and What Was Corrected

- **Raw JSON in answers.** The LLM (especially after fallback to qwen2.5:7b) sometimes returned search query JSON as its text response. Added `_is_degraded_answer` detection for JSON-shaped responses and a fallback summary built from evidence metadata.
- **Garbage claims.** The claim parser accepted lone numbers, punctuation fragments, and markdown heading artifacts. Added `_is_substantive` filtering.
- **Memory pollution.** Degraded or uncited answers were being stored in entity memory. Added guards: degraded answers skip storage entirely; uncited claims are excluded from storage.
- **Missing timeout configuration.** Cloud calls had no explicit timeout; Ollama had no timeout. Added 30s cloud timeout and 120s Ollama timeout with `APITimeoutError` and `APIConnectionError` in the fallback classification.
- **Auditor source fetch latency.** Sequential HTTP fetches for cited sources. Parallelized using `ThreadPoolExecutor`.
- **source_error conflated with unsupported.** When the Auditor could not fetch a cited source, it was marked "unsupported." Added a distinct `source_error` verdict to separate infrastructure failures from content mismatches.

## What Remains Limited

- **Model-dependent citation quality.** qwen2.5:7b almost never produces inline `[URL]` citations. This cascades: no citations → no entity storage → limited memory reuse → Auditor reports `no_citation`. The architecture handles citations correctly when the model produces them.
- **Auditor feedback loop demonstrated but not in main runner.** Implemented and demonstrated in optional experiments (problems 2→0 after one feedback cycle), but not integrated into the 8-question evaluation runner.
- **CPU-only local inference.** ~300-400s per question. A GPU or staying on cloud models would reduce this to seconds.
- **Regex entity extraction.** Misses entities that don't follow capitalization patterns.

## Testing

109 tests across 8 files, all mocked (no API keys or network required). Coverage includes: evidence loop behavior (sufficiency, follow-up, conflicts, termination), source selection scoring, plan parsing, verdict parsing, all verdict types, memory operations (store, recall, pollution prevention, claim filtering), provider fallback chain, timeout bounds, runner error handling, question validation, and trace/source extraction.

## Optional Experiments

Three "take it further" experiments were attempted, separate from the core evaluation:

1. **Conflict detection** — demonstrated. Delhi population question surfaced genuine source conflicts (Wikipedia vs CEIC census data). Evidence evaluator flagged the conflict; answer addressed the discrepancy. Trace: `logs/optional/conflict_trace.json`.
2. **Auditor feedback loop** — demonstrated. Reliance Industries question: pass 1 had 0 citations and 2 problems; after automated feedback and re-research, pass 2 had 2 citations, both `supported`, 0 problems. Trace: `logs/optional/feedback_loop_trace.json`.
3. **Adversarial analyst** — attempted but not demonstrated. Implementation complete (`run_adversarial.py`), but OpenRouter rate limits forced Ollama fallback, which took ~340s per question on CPU-only hardware, exceeding the practical timeout.

**Not attempted:** 50% cost reduction (token usage increased with multi-round loop) and 2-minute wall-clock target (CPU-only Ollama takes ~300-400s per question).

## With Two More Weeks

1. **Run evaluation with cloud models** — get traces that demonstrate full citation/memory/audit behavior.
2. **Integrate feedback loop into main runner** — currently demonstrated as a standalone experiment.
3. **Parallel LLM verification** in the Auditor with rate limiting.
4. **Adversarial evaluation** — run on GPU hardware or with cloud models to complete the comparison.
5. **Structured evaluation scoring** — automated comparison of answers against ground-truth for the 8 benchmark questions.
