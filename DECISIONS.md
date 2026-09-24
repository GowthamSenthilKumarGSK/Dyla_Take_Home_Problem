# DECISIONS.md

## Architecture: Separate Analyst and Auditor

The Analyst researches and answers questions. The Auditor independently verifies claims. They are separate agents because:

- **Independence**: the Auditor must not trust the Analyst's reasoning. It re-fetches cited sources via `fetch_page()` and uses an LLM to judge whether the source actually supports the claim.
- **Separation of concerns**: research quality and verification quality can be measured and improved independently.
- **Testability**: each agent has focused unit tests with mocked dependencies.

## Explicit Planning Step

The Analyst generates a research plan in a separate LLM call (without tools) before the tool-calling loop begins. This produces better search queries and more structured research compared to letting the model immediately start calling tools. The plan is recorded in the trace for observability.

## Tool Design

Three tools available to the Analyst:
- `web_search` — Tavily API search returning titles, URLs, snippets, and optional raw content
- `fetch_page` — HTTP fetch + trafilatura extraction for reading specific pages
- `memory_lookup` — query the EntityMemory for facts from earlier questions (only available when memory is provided)

The Auditor reuses `fetch_page` to independently retrieve cited sources. It does not call `web_search` — it verifies only what the Analyst cited, not the broader topic.

## Memory and Entity Reuse

`EntityMemory` is a JSON-backed store that transfers knowledge between questions:
- **Pre-plan recall**: before planning, entities are extracted from the question and matched against memory. Known facts are injected into the planning prompt.
- **Post-answer storage**: only cited claims containing recognizable proper nouns are stored. Uncited claims and degraded answers are never stored.
- **`from_memory` flag**: claims in answers where memory contributed are marked, but citations are still required from live sources.

This design means memory accumulates only verified, cited knowledge — not hallucinations.

## Provider Fallback Strategy

Three-tier fallback with sticky switching:
1. **Nemotron 120B** (OpenRouter free) — 120B MoE, 12B active params, best quality
2. **Gemma 4 31B** (OpenRouter free) — dense 31B, solid fallback
3. **qwen2.5:7b** (Ollama local) — no API dependency, works offline

Once fallback triggers, the session sticks with the working provider for remaining rounds. This avoids repeated failed attempts and keeps latency predictable.

**Rejected alternatives**:
- **Groq**: extremely fast inference but free-tier TPM limits (7K-8K) are too low for research agents that feed search results back into context. Unusable after a single search.
- **Gemini API**: project-level access denied on all generation endpoints despite valid API key. Model listing works but content generation returns 403.

## Parallelization

The Analyst's tool-calling loop is sequential by design — each tool call depends on prior results (e.g., search results inform which pages to fetch). Parallelizing this would break the reasoning chain.

The Auditor's source fetches are parallelized using `ThreadPoolExecutor` — cited URLs are independent HTTP requests with no ordering dependency. LLM verification calls remain sequential because each uses a different source page.

## Answer Quality

A lightweight sanity check detects obviously malformed answers (e.g., Base64 decode instructions, extremely short responses). Degraded answers are flagged in the trace and excluded from memory storage, preventing nonsense from polluting the entity store.

The claim parser filters garbage fragments: lone numbers, punctuation-only strings, and fragments shorter than 8 characters are not treated as claims.

## Testing

61 tests across 5 files, all using mocks:
- `test_tools.py` — web search, page fetch, memory operations
- `test_auditor.py` — verdict parsing, summary generation, all verdict types
- `test_memory_integration.py` — entity extraction, storage, lookup, recall, degraded answer handling, claim filtering
- `test_runner.py` — question validation, metrics, error handling, fresh/preserve memory
- `test_analyst.py` / `test_integration.py` — end-to-end analyst and analyst-to-auditor flow

## Known Limitations

- **Model-dependent citation quality**: the local qwen2.5:7b model rarely produces inline `[URL]` citations. This cascades: no citations → no entity storage → limited memory reuse → Auditor mostly reports `no_citation`. With cloud models (Nemotron/Gemma), citations work correctly.
- **Regex entity extraction**: proper noun detection uses regex, not NLP. Multi-word capitalized phrases and initials are caught, but some entities may be missed.
- **No auditor feedback loop**: the Auditor does not feed results back to the Analyst for re-research. This is a potential extension.
- **Single-threaded LLM calls**: Auditor verification calls are sequential. With many cited claims, this adds latency linearly.

## Next Steps

- Run evaluation with cloud models (Nemotron/Gemma) when OpenRouter quota resets
- Consider auditor feedback loop for contradicted claims
- Add cost tracking for paid model tiers if moving beyond free tier
