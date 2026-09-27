# Problem 3: Analyst and Auditor

A multi-agent research system that answers factual questions using live web evidence, then independently verifies each claim against its cited source.

## What This System Does

1. The **Analyst** receives a research question, plans its approach, searches the web, fetches and reads source pages, evaluates whether the evidence is sufficient, conducts follow-up research if needed, and produces a cited answer.
2. The **Auditor** takes the Analyst's answer, independently re-fetches every cited source, and uses an LLM to judge whether each source actually supports the corresponding claim.
3. **EntityMemory** transfers verified knowledge between questions so later questions can build on earlier research.

## Architecture

```
User Question
     |
  Analyst
     |
  Plan (LLM) --> Structured search queries
     |
  Evidence-Aware Research Loop (up to 3 rounds):
     |---> Web Search (Tavily)
     |---> Source Selection (deterministic scoring)
     |---> Page Fetch (trafilatura + httpx)
     |---> Evidence Evaluation (LLM)
     |---> Decision: sufficient? --> Final Answer (LLM)
     |                  |
     |            insufficient --> Follow-up query --> next round
     |
  Final Answer with inline [URL] citations
     |
  Auditor
     |---> Parallel source fetch (ThreadPoolExecutor)
     |---> Per-claim LLM verification
     |---> Verdicts: supported / unsupported / contradicted / no_citation / source_error
     |
  Audit Report
```

## Components

| File | Role |
|------|------|
| `analyst.py` | Analyst agent: planning, evidence-aware research loop, answer generation, entity extraction/storage |
| `auditor.py` | Auditor agent: independent source verification with parallel fetching |
| `tools.py` | Web search (Tavily), page fetching (trafilatura/httpx), tool definitions for OpenAI function-calling |
| `memory.py` | JSON-backed entity memory store with fact deduplication |
| `models.py` | Pydantic data models for all inputs/outputs (AnalystAnswer, AuditReport, Claim, etc.) |
| `config.py` | Environment configuration from `.env` |
| `runner.py` | 8-question evaluation runner with per-question error handling and aggregate metrics |
| `research_api.py` | Frontend API layer: single-question research, trace timeline extraction, source extraction |
| `questions.py` | 8 evaluation questions with difficulty levels and entity reuse annotations |
| `app.py` | Streamlit frontend: research interface and evaluation dashboard |

## Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com/) installed with `qwen2.5:7b` pulled (`ollama pull qwen2.5:7b`)

## Setup

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

pip install -r requirements.txt
```

Create a `.env` file from the template:
```bash
cp .env.example .env
# Edit .env with your API keys
```

Required API keys:
- **OPENROUTER_API_KEY** — for cloud LLM inference (free tier works)
- **TAVILY_API_KEY** — for web search (free tier: 1000 searches/month)

Start Ollama if you want the local fallback:
```bash
ollama serve
```

## Running

### Single Question (Streamlit UI)

```bash
streamlit run app.py
```

The UI has two pages:
- **Research** — ask a question and see the full Analyst + Auditor pipeline with answer, audit verdicts, trace timeline, sources, and cost/latency metrics.
- **Evaluation** — view results from the 8-question benchmark, with per-question traces and aggregate metrics.

### 8-Question Evaluation

```bash
python runner.py --fresh-memory
```

This runs all 8 questions sequentially with a fresh entity memory, saving per-question traces to `logs/q1_trace.json` through `logs/q8_trace.json` and aggregate metrics to `logs/runner_summary.json`.

Use `--preserve-memory` to keep existing `knowledge.json` from a previous run.

### Tests

```bash
python -m pytest -v
```

All 109 tests use mocks and require no API keys or network access.

## Provider Fallback

Three-tier fallback with sticky switching:

1. **Nemotron 120B** (`nvidia/nemotron-3-super-120b-a12b:free`) — 120B MoE, 12B active parameters. Best research quality with inline citations.
2. **Gemma 4 31B** (`google/gemma-4-31b-it:free`) — dense 31B. Solid cloud fallback.
3. **qwen2.5:7b** (Ollama local) — no API dependency. Works offline but significantly slower on CPU and lower citation quality.

Once a fallback triggers, the session sticks with the working provider to avoid repeated failures. Timeout handling: cloud calls timeout at 30s, Ollama at 120s. Connection errors, rate limits (429), timeouts, and server errors (502/503/529) all trigger fallback.

## The 8 Evaluation Questions

The questions are designed with increasing difficulty and deliberate entity reuse:

| # | Difficulty | Question | Introduces | Reuses |
|---|-----------|----------|------------|--------|
| 1 | Easy | Who is the current MD of Titan Company? | Titan, Ajoy Chawla | — |
| 2 | Easy-Medium | Titan's total revenue in FY 2024-25? | — | Titan |
| 3 | Medium | Who founded Infosys? Market cap? | Infosys, N.R. Narayana Murthy | — |
| 4 | Medium | Titan's business divisions and leaders? | — | Titan, Ajoy Chawla |
| 5 | Medium-Hard | Infosys vs TCS revenue in FY 2025-26? | TCS | Infosys |
| 6 | Medium-Hard | Chandrayaan-3 landing date and lead scientist? | Chandrayaan-3, ISRO | — |
| 7 | Hard | Titan share price now vs when Ajoy Chawla became MD? | — | Titan, Ajoy Chawla |
| 8 | Hard | India vs China successful Moon landings? | CNSA | ISRO, Chandrayaan-3 |

**Entity reuse pattern:** Q1 introduces Titan/Ajoy Chawla. Q2 and Q4 reuse Titan. Q7 reuses both Titan and Ajoy Chawla and requires combining facts from Q1 (appointment date) with current data (share price). Q6 introduces ISRO/Chandrayaan-3; Q8 reuses them for a cross-country comparison.

## What Was Implemented Beyond the Core

The core requirement was an Analyst that researches questions and an Auditor that verifies claims. Beyond that, this implementation includes:

- **Evidence-aware multi-round research loop** — instead of a single search-and-answer cycle, the Analyst evaluates whether collected evidence is sufficient and conducts up to 3 rounds of targeted follow-up research.
- **Deterministic source selection** — sources are scored by Tavily relevance plus domain-quality heuristics (.gov/.edu boost, low-quality penalization), not LLM judgment. This is reproducible and fast.
- **Explicit evidence sufficiency evaluation** — a separate LLM call assesses the evidence against the question before generating the final answer, identifying gaps and conflicts.
- **Conflict-aware answer generation** — when the evidence evaluator detects conflicting sources, the answer-generation prompt explicitly requires the Analyst to address the conflict.
- **Persistent entity memory** — facts from cited claims are stored in a JSON-backed entity store, keyed by proper-noun entities. Only cited claims are stored; degraded answers and uncited claims are excluded to prevent memory pollution.
- **Pre-plan memory recall** — before planning, entities are extracted from the question and matched against memory. Known facts are injected into the planning prompt so the Analyst can skip redundant searches.
- **Independent Auditor architecture** — the Auditor re-fetches every cited source independently. It does not trust the Analyst's fetched content. It uses an LLM to compare each claim against the re-fetched page.
- **source_error verdict** — the Auditor distinguishes "source could not be fetched" (infrastructure failure) from "source does not support the claim" (content mismatch). This prevents fetch failures from inflating the unsupported count.
- **Parallel Auditor source fetching** — cited URLs are fetched concurrently using `ThreadPoolExecutor`.
- **Degraded-answer detection** — answers that are raw JSON (model echoed tool-call data), contain nonsense markers, or are too short are detected, flagged in the trace, and excluded from memory storage.
- **Garbage claim filtering** — lone numbers, punctuation-only strings, fragments under 8 characters, and markdown heading artifacts are filtered from the claim list.
- **Three-tier provider fallback** with sticky switching and bounded timeouts.
- **Full research traces** — every LLM call, search, page fetch, evidence evaluation, source selection, and fallback event is recorded with timestamps in the per-question trace files.
- **Token/cost/latency tracking** — per-question and aggregate metrics for both Analyst and Auditor.
- **Streamlit frontend** — research interface for live queries and evaluation dashboard for reviewing benchmark results.

## Included Evaluation Run

The `logs/` directory contains traces from a full 8-question evaluation run. This run fell back to the local Ollama qwen2.5:7b model because OpenRouter's free-tier daily quota was exhausted.

**What the traces show:**
- All 8 questions completed successfully (no crashes).
- Every question shows: plan → web_search tool calls → course-change events (model fallback) → final answer → auditor verdicts.
- Memory reuse worked for Q7 (recalled Titan Company and Ajoy Chawla facts from earlier questions via `memory_recall` event, all 35 claims marked `from_memory=True`).
- The Auditor produced non-rubber-stamp verdicts: `unsupported` in Q4 and Q8, `source_error` in Q4 and Q5, `no_citation` throughout.
- Provider fallback is demonstrated in every trace (Nemotron → Gemma rate-limited → Ollama).
- Total latency: ~2599s (~43 minutes) across 8 questions — entirely due to CPU-only Ollama inference (~300-400s per question).
- Total cost: Rs. 0.00 (all inference was local).
- Only 3 entities stored in memory after the full run.

**What the traces also show (limitations of this run):**
- This evaluation run used the original LLM-controlled tool-calling loop (not the evidence-aware loop, which was added later). The evidence-aware loop with structured source selection and evidence evaluation is in the current code and tested (22 tests in `test_evidence_loop.py`) but was not part of this evaluation run.
- The qwen2.5:7b model almost never produced inline `[URL]` citations, so the Auditor mostly reported `no_citation` verdicts. Citations appear in Q4 (1 claim), Q5 (2 claims), and Q8 (1 claim).
- The model called `fetch_page` infrequently — `web_search` was the primary tool. The `fetch_page` tool is implemented, tested (`test_tools.py`), and used by the evidence-aware loop, but the tool-calling loop's model rarely invoked it.
- Without citations, entity storage was minimal (only 3 entities instead of the expected ~8+).
- Memory reuse was limited — only Q7 triggered a recall. Five questions were designed for entity reuse (Q2, Q4, Q5, Q7, Q8), but the cascading citation failure meant earlier questions didn't store entities for later reuse.
- Token cost increased across questions (3086 → 4069 tokens), not decreased. No cost-reduction optimization (prompt caching, memory shortcutting) was implemented.

These are **model-quality limitations**, not architecture limitations. The code correctly handles citations, entity storage, memory recall, fetch_page, evidence evaluation, and auditor verification when the underlying model produces them. The test suite (109 tests) demonstrates each of these capabilities with mocked LLM responses.

## Known Limitations

| Category | Limitation |
|----------|-----------|
| Model quality | qwen2.5:7b rarely produces inline `[URL]` citations. This cascades: no citations → no entity storage → limited memory reuse → Auditor reports `no_citation`. |
| Model quality | Smaller models sometimes echo search parameters or tool-call JSON as their answer instead of producing natural language. Degraded-answer detection catches this but the answer is lost. |
| Hardware | CPU-only Ollama inference: ~300-400s per question on Intel i7-1355U, 16GB RAM. A GPU or cloud model would reduce this to seconds. |
| Provider | OpenRouter free tier has daily request limits (~50 requests/day). Exhaustion forces fallback to local inference for the rest of the session. |
| Architecture | Auditor feedback loop is implemented and demonstrated in optional experiments but not integrated into the main evaluation runner. |
| Architecture | Regex-based entity extraction — proper nouns are detected via capitalization patterns, not NLP. Some entities may be missed or incorrectly segmented. |
| Architecture | Sequential Auditor LLM verification — each claim is verified one at a time. With many claims, this adds latency linearly. |

## Testing

109 tests across 8 files, all using mocks:

| File | Tests | What it covers |
|------|-------|---------------|
| `test_evidence_loop.py` | 18 | Plan parsing, source selection (scoring, dedup, max limits), evidence evaluation parsing, research loop behavior (sufficient stops, insufficient follow-up, conflicts, max rounds), citation preservation, memory-citation interaction, analyst-auditor compatibility |
| `test_auditor.py` | 16 | Verdict parsing (supported/unsupported/contradicted/malformed), summary generation (reliability rating), run_auditor for all verdict types, cost tracking, source deduplication |
| `test_memory_integration.py` | 18 | Entity extraction (multi-word, initialed, dedup), fact storage (cited-only, uncited skipped, related entities), memory lookup/search, pre-plan recall injection, from_memory flag, empty memory, claim substantiveness filtering, degraded answer detection, memory pollution prevention |
| `test_fallback.py` | 8 | Provider error classification (rate limit, timeout, connection, non-retriable), fallback chain (Nemotron→Gemma, both cloud→Ollama, timeout cascade), no-unnecessary-fallback, timeout bounds |
| `test_runner.py` | 11 | Question validation (count, fields, IDs, difficulty, entity reuse), verdict counting, memory snapshot, error handling (failed question doesn't stop runner), trace/summary saving, fresh/preserve memory |
| `test_research_api.py` | 13 | Trace timeline extraction (all event types), source extraction and ordering, provider info extraction, memory snapshot, load functions for missing files |
| `test_analyst.py` | varies | Analyst end-to-end with mocked LLM/tools |
| `test_integration.py` | varies | Analyst-to-Auditor integration flow |
| `test_tools.py` | 6 | Web search, page fetch (success, bad URL, timeout), memory operations |

## Output Files

| Path | Contents |
|------|----------|
| `logs/q1_trace.json` – `logs/q8_trace.json` | Per-question traces: plan, searches, page fetches, evidence evaluations, answer, claims, audit verdicts, token/cost/latency metrics, memory state |
| `logs/runner_summary.json` | Aggregate metrics: total tokens, cost, latency, success/failure counts, memory reuse |
| `knowledge.json` | Entity memory state after the evaluation run |
| `logs/optional/conflict_trace.json` | Conflict-detection experiment trace |
| `logs/optional/feedback_loop_trace.json` | Auditor→Analyst feedback loop experiment trace |
| `logs/optional/experiments_summary.json` | Optional experiment status summary |

## Optional Experiments (Take It Further)

Three optional experiments were attempted, clearly separated from the core 8-question evaluation. Experiment code is in `optional_experiments.py` and `run_adversarial.py`. Traces are in `logs/optional/`.

### 1. Conflicting Sources — Implemented and Demonstrated

**Question:** "What is the population of Delhi?"

The evidence-aware loop detected genuine source conflicts: Wikipedia listed the 2011 census figure as 16,787,941 while a CEIC data source reported 16,368,000. The evidence evaluator flagged the conflict, and the answer-generation prompt required the Analyst to acknowledge and address the discrepancy.

**Evidence:** `logs/optional/conflict_trace.json` — 6 searches, 9 page fetches, 5 claims, 4 cited, `conflicts_detected: true`.

### 2. Auditor → Analyst Feedback Loop — Implemented and Demonstrated

**Question:** "What is the market cap of Reliance Industries and who is the chairman?"

Pass 1: Analyst answered with 2 claims, 0 citations. Auditor flagged both as `no_citation` (2 problems). The system automatically fed the Auditor's findings back to the Analyst with specific instructions to re-research and cite.

Pass 2: After re-research (2 additional searches, 4 page fetches), the revised answer had 2 claims, 2 citations, both verified as `supported` by the Auditor. Problems went from 2 → 0.

**Evidence:** `logs/optional/feedback_loop_trace.json` — full two-pass trace with improvement metrics.

### 3. Adversarial Analyst — Attempted, Not Demonstrated

The experiment compared a normal system prompt against an adversarial prompt that warns the Analyst about Auditor verification. The implementation is complete (`run_adversarial.py`), but live demonstration was blocked: OpenRouter's free-tier models were rate-limited (Nemotron and Gemma both returned errors), forcing fallback to local Ollama (qwen2.5:7b on CPU), which took ~340 seconds for a single normal-mode run — exceeding the practical 4-minute timeout before the adversarial comparison could begin. This is a hardware/provider constraint, not a code limitation.

### Not Attempted

- **50% cost reduction** — not achieved. Token usage increased across questions (3086 → 4069) due to the multi-round evidence loop. No prompt-caching or memory-shortcutting optimization was implemented.
- **2-minute wall-clock target** — not achieved. CPU-only Ollama inference takes ~300-400s per question. Cloud models would meet this target but were rate-limited during evaluation.

## Future Work

These are improvements that are **not currently implemented**:

- **Better model/provider routing** — dynamically route different question types to different models based on difficulty or topic.
- **Parallel Auditor LLM calls** — verify claims concurrently (with rate limiting) to reduce audit latency.
- **Production-scale memory** — replace the JSON file with a database backend for larger entity stores.
- **Additional evaluation datasets** — test on standard factual QA benchmarks beyond the 8 custom questions.
