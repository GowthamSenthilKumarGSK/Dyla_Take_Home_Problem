# Problem 3: Analyst and Auditor

A multi-agent research system that answers factual questions using live web evidence, then independently verifies each claim.

## Architecture

- **Analyst agent** — plans research, searches the web, fetches pages, produces cited answers
- **Auditor agent** — independently opens cited sources and verifies each claim
- **EntityMemory** — transfers knowledge between questions via a JSON-backed entity store
- **Runner** — orchestrates the 8-question evaluation with per-question error handling

## Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com/) installed with `qwen2.5:7b` pulled (`ollama pull qwen2.5:7b`)

## Environment Variables

Create a `.env` file in the project root:

```
OPENROUTER_API_KEY=<your OpenRouter API key>
TAVILY_API_KEY=<your Tavily API key>
```

The system uses OpenRouter's free tier for cloud models. Tavily provides web search (free tier: 1000 searches/month).

Optional overrides (defaults work out of the box):
```
ANALYST_MODEL=nvidia/nemotron-3-super-120b-a12b:free
PLANNING_MODEL=nvidia/nemotron-3-super-120b-a12b:free
AUDITOR_MODEL=nvidia/nemotron-3-super-120b-a12b:free
FALLBACK_MODEL=google/gemma-4-31b-it:free
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=qwen2.5:7b
SEARCH_MAX_RESULTS=5
FETCH_TIMEOUT_SECONDS=15
```

## Installation

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

pip install -r requirements.txt
```

Ensure Ollama is running if you want the local fallback:
```bash
ollama serve
```

## Running Tests

```bash
python -m pytest -v
```

All tests use mocks and do not require API keys or network access.

## Running the 8-Question Evaluation

```bash
# Fresh memory (default) — starts with clean knowledge.json
python runner.py --fresh-memory

# Preserved memory — keeps existing knowledge.json from a previous run
python runner.py --preserve-memory
```

## Output

- `logs/q1_trace.json` through `logs/q8_trace.json` — per-question traces with plan, tool calls, claims, audit verdicts, token counts, latencies
- `logs/runner_summary.json` — aggregate metrics across all questions
- `knowledge.json` — entity memory state after the run

Each trace includes: the research plan, every tool call with arguments and result previews, the final answer with parsed claims and citations, audit verdicts for each claim, and per-question token/cost/latency metrics.

## Provider Behavior

The system uses a three-tier fallback:

1. **Nemotron 120B** (OpenRouter free tier) — best research quality, inline citations, cross-checking
2. **Gemma 4 31B** (OpenRouter free tier) — solid cloud fallback
3. **Ollama qwen2.5:7b** (local) — no API dependency, but significantly slower on CPU and lower quality

If OpenRouter's daily free limit is exhausted (50 requests/day), all questions fall back to the local Ollama model. The local model is slower (3-5 minutes per question on CPU) and produces lower quality answers with fewer citations. For best results, run the evaluation when OpenRouter quota is available.

## Known Limitations

- Ollama qwen2.5:7b rarely produces inline `[URL]` citations, which cascades: no citations means no entity storage, limited memory reuse, and the Auditor reports `no_citation` on most claims
- Local CPU inference is slow (~200-400s per question on Intel i7-1355U)
- Entity extraction uses regex-based proper noun detection, not NLP
- No parallelization of the sequential Analyst reasoning loop (tool calls depend on prior results)
- Auditor source fetches are parallelized; LLM verification calls are sequential (each depends on the fetched page)
