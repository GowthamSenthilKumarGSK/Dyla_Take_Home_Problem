# AI Development Log

This project was developed with AI assistance (Claude Code). This log documents the development process.

## Development Timeline

### Phase 1: Foundation
- Set up project structure, config, models, tools (web_search, fetch_page)
- Implemented the Analyst agent with OpenAI-compatible API, system prompt, planning step, tool-calling loop, answer parser
- Added three-tier provider fallback: Nemotron -> Gemma -> Ollama qwen2.5:7b
- Fixed Python 3 exception scoping bug in `_call_llm()` (except-clause variables deleted on block exit)

### Phase 2: Auditor
- Designed Auditor input/output contract (AnalystAnswer -> AuditReport)
- Implemented independent source verification via fetch_page
- Added Literal verdict types: supported, unsupported, contradicted, no_citation, source_error
- Fixed set-ordering flaky test (changed cited_urls from set to list with seen-set dedup)
- Fixed cost.model reporting when Auditor makes zero LLM calls

### Phase 3: Memory Integration
- Implemented EntityMemory with JSON-backed entity store
- Added pre-plan memory recall and post-answer entity storage
- Added memory_lookup tool for the Analyst's research loop
- Passed memory explicitly (not module-level) for safe repeated/parallel runs
- Added from_memory flag on claims

### Phase 4: Runner and Questions
- Designed 8 research questions with increasing difficulty and entity reuse pattern
- Built evaluation runner with per-question error handling
- Added --fresh-memory and --preserve-memory CLI options
- Verified all 50 tests pass

### Phase 5: Provider Benchmarking
- Benchmarked Groq (llama-3.1-8b-instant retired, qwen3.8-27b and gpt-oss-120b hit TPM limits)
- Tested Gemini API (project-level access denied on all generation endpoints)
- Conclusion: OpenRouter free tier remains the only viable cloud option

### Phase 6: Audit and Fixes
- Ran full 8-question evaluation (fell back to qwen2.5:7b due to exhausted OpenRouter quota)
- Identified cascading model-quality failure: no citations -> no entity storage -> no memory reuse
- Fixed claim parser: filter garbage fragments (lone numbers, short strings, punctuation)
- Added degraded-answer detection to prevent nonsense from entering memory
- Parallelized Auditor source fetches using ThreadPoolExecutor
- Created README.md and DECISIONS.md

### Phase 7: Evidence-Aware Research Loop
- Replaced the LLM-controlled tool-calling loop with a structured evidence-aware research loop
- Added structured plan parsing (QUERIES: / PLAN: format)
- Added deterministic source selection with domain-quality scoring
- Added explicit evidence sufficiency evaluation (separate LLM call)
- Added conflict detection and follow-up research (up to 3 rounds)
- Added timeout configuration (30s cloud, 120s Ollama) and APITimeoutError/APIConnectionError handling
- Added source_error verdict to distinguish fetch failures from unsupported claims

### Phase 8: Frontend, Testing, and Documentation
- Built Streamlit frontend with research interface and evaluation dashboard
- Added research_api.py as frontend API layer (trace timeline, source extraction, provider info)
- Diagnosed and fixed raw-JSON-in-summary bug (model echoing search parameters as answer)
- Repaired corrupted Q1/Q2 trace files
- Extended test suite to 109 tests covering evidence loop, fallback, research API, and integration
- Updated README.md and DECISIONS.md for final submission

## Key AI-Assisted Decisions
- AI suggested the three-tier fallback architecture after testing provider availability
- AI identified the Python 3 exception scoping bug (variables deleted on except-block exit)
- AI designed the entity extraction regex approach as a lightweight alternative to NLP libraries
- AI proposed the cascading-failure diagnosis: model quality -> citation quality -> memory quality
- AI benchmarked alternative providers (Groq, Gemini) to validate the architecture choice

## Runtime Traces
The `q1_trace.json` through `q8_trace.json` files contain the Analyst and Auditor runtime traces from the 8-question evaluation run. `runner_summary.json` contains the aggregate metrics. All 8 traces and the summary correspond to the same single evaluation run.

### What changed between code versions and the evaluation run
- The evaluation run (traces Q1-Q8) was executed with the **original LLM-controlled tool-calling loop** (Phase 6-7 code). The model chose which tools to call on each round.
- After the evaluation run, the **evidence-aware research loop** was implemented (Phase 7), replacing the LLM-controlled loop with structured search → source selection → fetch → evidence evaluation → follow-up.
- The evidence-aware loop is in the current committed code and tested (22 tests in `test_evidence_loop.py`), but the evaluation traces predate it.
- The evaluation run fell back to local Ollama qwen2.5:7b because OpenRouter's free-tier quota was exhausted. The model quality limitations (poor citations, minimal entity storage) are documented in the README.
