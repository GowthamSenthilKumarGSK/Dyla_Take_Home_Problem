"""Analyst agent — plans, searches the web, and answers research questions."""
from __future__ import annotations
import json
import re
import time
from openai import OpenAI, RateLimitError, APIStatusError, APITimeoutError, APIConnectionError

import config
from models import SearchResponse, PageContent, Claim, CostRecord, AnalystAnswer, Fact
from memory import EntityMemory
from tools import web_search, fetch_page, TOOL_DEFINITIONS

# Cost per token (USD) for supported models
_PRICING = {
    "gpt-4o":      {"input": 2.50 / 1_000_000, "output": 10.00 / 1_000_000},
    "gpt-4o-mini": {"input": 0.15 / 1_000_000, "output": 0.60 / 1_000_000},
    "google/gemma-4-31b-it:free":              {"input": 0, "output": 0},
    "nvidia/nemotron-3-super-120b-a12b:free":  {"input": 0, "output": 0},
    "qwen2.5:7b":                              {"input": 0, "output": 0},
}

SYSTEM_PROMPT = """\
You are a research analyst. Your job is to answer factual questions using
evidence gathered from the live web.

Rules:
1. USE TOOLS. Never answer from your own knowledge alone. Every factual claim
   in your answer must be backed by evidence you found via web_search or
   fetch_page during this session.
2. CROSS-CHECK. If a claim appears in only one source, try to verify it with
   a second search or source.
3. CITE EVERYTHING. For each claim, include the URL you got it from in
   square brackets like [https://example.com].
4. SAY "I DON'T KNOW". If you cannot find reliable evidence for something,
   say so explicitly rather than guessing.
5. BE CONCISE. Answer the question directly, then list your sources.

When you are ready to give the final answer, respond normally (no tool call).
Structure your final answer as:

ANSWER:
<your answer with inline citations [URL]>

SOURCES:
- <url 1>: <what you used it for>
- <url 2>: <what you used it for>
"""

PLANNING_PROMPT = """\
You are a research analyst. Given the following research question, produce a
short research plan.

Respond in EXACTLY this format (no other text):

QUERIES:
- "first search query"
- "second search query"
(2-4 specific search queries)

PLAN:
- What specific facts you need to find
- How you will verify or cross-check claims
{memory_section}
Question: {question}
"""

EVIDENCE_EVAL_PROMPT = """\
You are evaluating whether collected web evidence is sufficient to answer a
research question accurately.

Question: {question}

Evidence collected from {n_sources} source(s):
{evidence_summaries}

Assess the evidence and respond in EXACTLY this format:
SUFFICIENT: YES or NO
CONFLICTS: describe any disagreements between sources, or "none"
MISSING: what specific information is still needed, or "nothing critical"
FOLLOW_UP: a specific search query to fill the gap, or "none"
STOP_REASON: one sentence explaining why research should stop or continue
"""

MAX_TOOL_ROUNDS = 15
MAX_RESEARCH_ROUNDS = 3
MAX_SOURCES_PER_ROUND = 3
MAX_EVIDENCE_CHARS = 3000

CLOUD_TIMEOUT = 30
OLLAMA_TIMEOUT = 120


def _is_provider_error(exc: Exception) -> bool:
    """True for errors indicating the model/provider is temporarily unavailable.
    False for auth errors (401), bad requests (400), or application errors."""
    if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
        return True
    if isinstance(exc, APIStatusError) and exc.status_code in (404, 502, 503, 529):
        return True
    return False


def _execute_tool_call(name: str, arguments: dict,
                       memory: EntityMemory | None = None) -> str:
    """Execute a tool call and return the result as a string for the LLM."""
    if name == "web_search":
        result: SearchResponse = web_search(**arguments)
        if result.error:
            return f"Search error: {result.error}"
        parts = []
        for r in result.results:
            entry = f"Title: {r.title}\nURL: {r.url}\nSnippet: {r.snippet}"
            if r.raw_content:
                preview = r.raw_content[:3000]
                entry += f"\nContent preview:\n{preview}"
            parts.append(entry)
        return "\n---\n".join(parts) if parts else "No results found."

    elif name == "fetch_page":
        result: PageContent = fetch_page(**arguments)
        if result.error:
            return f"Fetch error: {result.error}"
        text = result.text[:5000]
        return f"Title: {result.title}\nURL: {result.url}\nContent:\n{text}"

    elif name == "memory_lookup":
        if not memory:
            return "Memory not available -- search the web instead."
        entity_name = arguments.get("entity_name", "")
        results = memory.search(entity_name)
        if not results:
            return f"No prior knowledge found for '{entity_name}'. Search the web."
        parts = []
        for r in results:
            facts_str = "\n".join(
                f"  - {f.text} [source: {f.source}]" for f in r.facts
            )
            related = ", ".join(r.related_entities) if r.related_entities else "none"
            parts.append(
                f"Entity: {r.name} (type: {r.entity_type})\n"
                f"Known facts:\n{facts_str}\n"
                f"Related entities: {related}"
            )
        return "\n---\n".join(parts)

    else:
        return f"Unknown tool: {name}"


def _track_cost(usage, model: str) -> CostRecord:
    pricing = _PRICING.get(model, {"input": 0, "output": 0})
    input_tok = usage.prompt_tokens
    output_tok = usage.completion_tokens
    cost = input_tok * pricing["input"] + output_tok * pricing["output"]
    return CostRecord(
        input_tokens=input_tok,
        output_tokens=output_tok,
        model=model,
        cost_usd=cost,
    )


def _is_retriable(exc: Exception) -> bool:
    """True for errors that should trigger a fallback attempt."""
    if isinstance(exc, RuntimeError) and "Empty choices" in str(exc):
        return True
    return _is_provider_error(exc)


def _make_ollama_client() -> OpenAI:
    return OpenAI(api_key="ollama", base_url=config.OLLAMA_BASE_URL,
                  timeout=OLLAMA_TIMEOUT, max_retries=0)


def _call_llm(client: OpenAI, model: str, messages: list,
              trace: list, round_num: int, start_time: float,
              tools: list | None = None):
    """Call the LLM with three-tier fallback: primary cloud -> cloud fallback
    -> local Ollama. Returns (response, model_used, client_used)."""
    kwargs = dict(model=model, messages=messages, temperature=0.2)
    if tools:
        kwargs["tools"] = tools

    # Python 3 deletes except-clause variables on block exit; save to outer scope.
    _primary_err: Exception | None = None
    _cloud_fb_err: Exception | None = None

    # Tier 1: primary cloud model
    try:
        response = client.chat.completions.create(**kwargs)
        if not response.choices:
            raise RuntimeError("Empty choices in response")
        return response, model, client
    except Exception as exc:
        if not _is_retriable(exc):
            raise
        _primary_err = exc

    # Tier 2: cloud fallback model (same client)
    trace.append({
        "round": round_num,
        "event": "model_fallback",
        "from_model": model,
        "to_model": config.FALLBACK_MODEL,
        "reason": f"{type(_primary_err).__name__}: {_primary_err}",
        "timestamp": time.time() - start_time,
    })
    kwargs["model"] = config.FALLBACK_MODEL
    try:
        response = client.chat.completions.create(**kwargs)
        if not response.choices:
            raise RuntimeError("Empty choices from cloud fallback")
        return response, config.FALLBACK_MODEL, client
    except Exception as exc:
        if not _is_retriable(exc):
            raise
        _cloud_fb_err = exc

    # Tier 3: local Ollama model
    trace.append({
        "round": round_num,
        "event": "ollama_fallback",
        "from_model": config.FALLBACK_MODEL,
        "to_model": config.OLLAMA_MODEL,
        "reason": f"{type(_cloud_fb_err).__name__}: {_cloud_fb_err}",
        "timestamp": time.time() - start_time,
    })
    ollama_client = _make_ollama_client()
    kwargs["model"] = config.OLLAMA_MODEL
    try:
        response = ollama_client.chat.completions.create(**kwargs)
        if not response.choices:
            raise RuntimeError("Empty choices from Ollama")
        return response, config.OLLAMA_MODEL, ollama_client
    except Exception as exc:
        trace.append({
            "round": round_num,
            "event": "ollama_failed",
            "model": config.OLLAMA_MODEL,
            "reason": f"{type(exc).__name__}: {exc}",
            "timestamp": time.time() - start_time,
        })
        raise RuntimeError(
            f"All providers failed. Primary ({model}): {_primary_err}. "
            f"Cloud fallback ({config.FALLBACK_MODEL}): {_cloud_fb_err}. "
            f"Ollama ({config.OLLAMA_MODEL}): {exc}"
        ) from exc


def _extract_entities(text: str) -> list[str]:
    """Best-effort extraction of proper noun phrases from text."""
    multi = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text)
    initialed = re.findall(
        r'\b([A-Z]\.?\s*[A-Z]\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b', text,
    )
    combined = multi + initialed
    return list(dict.fromkeys(combined))


def _store_entities(answer: AnalystAnswer, memory: EntityMemory):
    """Store cited entity-level facts from the analyst's answer into memory."""
    for claim in answer.claims:
        if not claim.citation:
            continue
        entities = _extract_entities(claim.text)
        if not entities:
            continue
        fact = Fact(text=claim.text, source=claim.citation)
        primary = entities[0]
        related = entities[1:] if len(entities) > 1 else []
        memory.add_facts(primary, [fact], related=related)


def _generate_plan(client: OpenAI, model: str, question: str,
                   trace: list, start_time: float,
                   total_cost: CostRecord,
                   memory_context: str = "") -> tuple[str, OpenAI, str]:
    """Explicit planning step — asks the LLM to produce a research plan
    before any tools are available. Returns (plan_text, client, model)
    so the caller can stick with the provider that worked."""
    memory_section = ""
    if memory_context:
        memory_section = (
            "\nPreviously known about entities in this question:\n"
            f"{memory_context}\n"
        )
    plan_messages = [
        {"role": "user", "content": PLANNING_PROMPT.format(
            question=question, memory_section=memory_section,
        )},
    ]

    response, used_model, used_client = _call_llm(
        client, model, plan_messages, trace, round_num=0, start_time=start_time,
    )

    if response.usage:
        rc = _track_cost(response.usage, used_model)
        total_cost.input_tokens += rc.input_tokens
        total_cost.output_tokens += rc.output_tokens
        total_cost.cost_usd += rc.cost_usd

    plan_text = response.choices[0].message.content or ""

    trace.append({
        "round": 0,
        "event": "plan",
        "model": used_model,
        "plan": plan_text,
        "timestamp": time.time() - start_time,
    })

    return plan_text, used_client, used_model


def _parse_plan_queries(plan_text: str) -> tuple[list[str], str]:
    """Parse structured plan into (search_queries, plan_body).
    Falls back to extracting quoted strings if format is not followed."""
    queries: list[str] = []
    plan_lines: list[str] = []
    in_queries = False
    in_plan = False

    for line in plan_text.strip().splitlines():
        stripped = line.strip()
        if stripped.upper().startswith('QUERIES:'):
            in_queries = True
            in_plan = False
            continue
        elif stripped.upper().startswith('PLAN:'):
            in_queries = False
            in_plan = True
            continue

        if in_queries:
            quoted = re.findall(r'"([^"]+)"', stripped)
            if quoted:
                queries.extend(quoted)
            else:
                clean = stripped.lstrip('-•*0123456789.').strip()
                if clean and len(clean) > 5:
                    queries.append(clean)
        elif in_plan:
            plan_lines.append(stripped)

    plan_body = '\n'.join(plan_lines) if plan_lines else plan_text

    if not queries:
        queries = re.findall(r'"([^"]{5,})"', plan_text)

    return queries[:6], plan_body


def _select_sources(search_results: list, already_fetched: set,
                    max_sources: int = MAX_SOURCES_PER_ROUND) -> list[dict]:
    """Deterministically select the best sources to fetch based on
    Tavily relevance score and domain quality heuristics."""
    candidates = []
    seen_urls: set[str] = set()

    for r in search_results:
        if r.url in already_fetched or r.url in seen_urls:
            continue
        seen_urls.add(r.url)

        score = r.score or 0.5
        domain = r.url.lower()

        if any(d in domain for d in ['.gov', '.edu', 'wikipedia.org']):
            score += 0.15
        elif any(d in domain for d in ['.org', 'reuters.com', 'bloomberg.com',
                                        'bbc.com', 'economictimes.com']):
            score += 0.08

        if any(d in domain for d in ['pinterest.com', 'quora.com']):
            score -= 0.15

        candidates.append({
            'url': r.url,
            'title': r.title,
            'snippet': r.snippet,
            'score': round(score, 3),
            'original_score': r.score,
        })

    candidates.sort(key=lambda x: x['score'], reverse=True)
    return candidates[:max_sources]


def _evaluate_evidence(question: str, evidence: dict,
                       client, model, trace, round_num, start_time,
                       total_cost) -> tuple[dict, str, 'OpenAI']:
    """LLM call to assess whether collected evidence is sufficient."""
    summaries = []
    for url, info in evidence.items():
        preview = info['text'][:MAX_EVIDENCE_CHARS]
        summaries.append(f"Source: {url}\nTitle: {info['title']}\nContent:\n{preview}")

    evidence_text = "\n---\n".join(summaries) if summaries else "No evidence collected."

    messages = [{"role": "user", "content": EVIDENCE_EVAL_PROMPT.format(
        question=question,
        n_sources=len(evidence),
        evidence_summaries=evidence_text,
    )}]

    response, used_model, used_client = _call_llm(
        client, model, messages, trace,
        round_num=round_num, start_time=start_time,
    )

    if response.usage:
        rc = _track_cost(response.usage, used_model)
        total_cost.input_tokens += rc.input_tokens
        total_cost.output_tokens += rc.output_tokens
        total_cost.cost_usd += rc.cost_usd

    eval_text = response.choices[0].message.content or ""
    result = _parse_evaluation(eval_text)

    trace.append({
        "round": round_num,
        "event": "evidence_evaluation",
        "model": used_model,
        "sufficient": result['sufficient'],
        "conflicts": result['conflicts'],
        "missing": result['missing'],
        "follow_up_query": result['follow_up_query'],
        "stop_reason": result['stop_reason'],
        "sources_evaluated": len(evidence),
        "timestamp": time.time() - start_time,
    })

    return result, used_model, used_client


def _parse_evaluation(text: str) -> dict:
    """Parse the structured evidence evaluation response."""
    result = {
        'sufficient': False,
        'conflicts': 'none',
        'missing': '',
        'follow_up_query': None,
        'stop_reason': '',
    }

    for line in text.strip().splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith('SUFFICIENT:'):
            val = stripped.split(':', 1)[1].strip().upper()
            result['sufficient'] = 'YES' in val
        elif upper.startswith('CONFLICTS:'):
            result['conflicts'] = stripped.split(':', 1)[1].strip()
        elif upper.startswith('MISSING:'):
            result['missing'] = stripped.split(':', 1)[1].strip()
        elif upper.startswith('FOLLOW_UP:') or upper.startswith('FOLLOW-UP:'):
            val = stripped.split(':', 1)[1].strip()
            if val.lower() not in ('none', 'n/a', '') and len(val) > 3:
                result['follow_up_query'] = val.strip('"\'')
        elif upper.startswith('STOP_REASON:'):
            result['stop_reason'] = stripped.split(':', 1)[1].strip()

    return result


def run_analyst(question: str, model: str | None = None,
                memory: EntityMemory | None = None) -> AnalystAnswer:
    """Run the analyst agent with evidence-aware research loop.

    Flow: Plan → Search → Select sources → Fetch → Evaluate evidence
    → (follow-up search or generate answer)."""

    model = model or config.ANALYST_MODEL
    client = OpenAI(
        api_key=config.OPENROUTER_API_KEY,
        base_url=config.OPENROUTER_BASE_URL,
        timeout=CLOUD_TIMEOUT,
        max_retries=0,
    )

    trace: list[dict] = []
    total_cost = CostRecord(model=model)
    start_time = time.time()
    memory_used = False

    # --- Step 0: Pre-plan memory lookup ---
    memory_context = ""
    if memory:
        entity_names = _extract_entities(question)
        if entity_names:
            memory_context = memory.get_context_for_entities(entity_names)
            if memory_context:
                memory_used = True
                trace.append({
                    "round": 0,
                    "event": "memory_recall",
                    "entities": entity_names,
                    "context_length": len(memory_context),
                    "timestamp": time.time() - start_time,
                })

    # --- Step 1: Planning with structured queries ---
    plan_text, client, model = _generate_plan(
        client, model, question, trace, start_time, total_cost,
        memory_context=memory_context,
    )

    search_queries, plan_body = _parse_plan_queries(plan_text)
    if not search_queries:
        search_queries = [question]

    trace.append({
        "round": 0,
        "event": "parsed_queries",
        "queries": search_queries,
        "timestamp": time.time() - start_time,
    })

    # --- Step 2: Evidence-aware research loop ---
    collected_evidence: dict[str, dict] = {}
    all_search_results: list = []
    evaluation: dict | None = None

    for research_round in range(1, MAX_RESEARCH_ROUNDS + 1):
        # 2a: Execute searches
        for query in search_queries:
            results = web_search(query)
            result_urls = [r.url for r in results.results] if not results.error else []
            trace.append({
                "round": research_round,
                "event": "search",
                "query": query,
                "result_count": len(results.results),
                "urls": result_urls,
                "error": results.error,
                "timestamp": time.time() - start_time,
            })
            if not results.error:
                all_search_results.extend(results.results)

        # 2b: Select sources (deterministic — no LLM call)
        selected = _select_sources(
            all_search_results, set(collected_evidence.keys()),
        )
        trace.append({
            "round": research_round,
            "event": "source_selection",
            "selected": [{"url": s["url"], "title": s["title"],
                         "score": s["score"]} for s in selected],
            "total_candidates": len(all_search_results),
            "already_fetched": len(collected_evidence),
            "timestamp": time.time() - start_time,
        })

        if not selected:
            trace.append({
                "round": research_round,
                "event": "research_complete",
                "reason": "No new sources to fetch",
                "sources_collected": len(collected_evidence),
                "timestamp": time.time() - start_time,
            })
            break

        # 2c: Fetch selected pages
        for source in selected:
            page = fetch_page(source["url"])
            trace.append({
                "round": research_round,
                "event": "fetch_page",
                "url": source["url"],
                "title": page.title or source["title"],
                "success": not bool(page.error),
                "error": page.error,
                "text_length": len(page.text) if page.text else 0,
                "timestamp": time.time() - start_time,
            })
            if not page.error and page.text.strip():
                collected_evidence[source["url"]] = {
                    "title": page.title or source["title"],
                    "text": page.text,
                    "snippet": source.get("snippet", ""),
                }

        if not collected_evidence:
            trace.append({
                "round": research_round,
                "event": "research_complete",
                "reason": "All page fetches failed",
                "sources_collected": 0,
                "timestamp": time.time() - start_time,
            })
            break

        # 2d: Evaluate evidence (LLM call)
        evaluation, model, client = _evaluate_evidence(
            question, collected_evidence, client, model, trace,
            round_num=research_round, start_time=start_time,
            total_cost=total_cost,
        )

        # 2e: Decision
        has_conflicts = (evaluation['conflicts']
                        and evaluation['conflicts'].lower() != 'none')

        if has_conflicts:
            trace.append({
                "round": research_round,
                "event": "conflicts_detected",
                "conflicts": evaluation['conflicts'],
                "timestamp": time.time() - start_time,
            })

        if evaluation['sufficient']:
            trace.append({
                "round": research_round,
                "event": "research_complete",
                "reason": evaluation['stop_reason'] or "Evidence sufficient",
                "sources_collected": len(collected_evidence),
                "timestamp": time.time() - start_time,
            })
            break

        if evaluation.get('follow_up_query'):
            search_queries = [evaluation['follow_up_query']]
            trace.append({
                "round": research_round,
                "event": "follow_up_search",
                "query": evaluation['follow_up_query'],
                "reason": evaluation.get('missing', ''),
                "timestamp": time.time() - start_time,
            })
        else:
            trace.append({
                "round": research_round,
                "event": "research_complete",
                "reason": "Insufficient evidence but no follow-up suggested",
                "sources_collected": len(collected_evidence),
                "timestamp": time.time() - start_time,
            })
            break
    else:
        trace.append({
            "round": MAX_RESEARCH_ROUNDS,
            "event": "research_complete",
            "reason": f"Maximum research rounds ({MAX_RESEARCH_ROUNDS}) reached",
            "sources_collected": len(collected_evidence),
            "timestamp": time.time() - start_time,
        })

    # --- Step 3: Generate final answer from evidence ---
    conflict_note = ""
    if evaluation and evaluation.get('conflicts') and evaluation['conflicts'].lower() != 'none':
        conflict_note = (
            "\n\nIMPORTANT — Conflicting information detected between sources:\n"
            f"{evaluation['conflicts']}\n"
            "Address these conflicts: resolve using stronger/more recent evidence, "
            "or explicitly report the disagreement."
        )

    evidence_block = ""
    for url, info in collected_evidence.items():
        preview = info['text'][:4000]
        evidence_block += f"\n\nSource: {url}\nTitle: {info['title']}\n{preview}\n"

    memory_note = ""
    if memory_context:
        memory_note = (
            "\n\nPreviously known from earlier research (still cite fresh sources):\n"
            f"{memory_context}"
        )

    answer_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Research question: {question}\n\n"
            f"Your research plan:\n{plan_text}\n\n"
            f"Evidence collected from {len(collected_evidence)} source(s):"
            f"{evidence_block}"
            f"{conflict_note}"
            f"{memory_note}\n\n"
            "Based on the evidence above, provide your final answer. "
            "Cite every factual claim with the source URL in [brackets]. "
            "If evidence is insufficient for any part, say so explicitly."
        )},
    ]

    response, used_model, client = _call_llm(
        client, model, answer_messages, trace,
        round_num=MAX_RESEARCH_ROUNDS + 1,
        start_time=start_time,
    )
    model = used_model

    if response.usage:
        rc = _track_cost(response.usage, used_model)
        total_cost.input_tokens += rc.input_tokens
        total_cost.output_tokens += rc.output_tokens
        total_cost.cost_usd += rc.cost_usd

    answer_text = response.choices[0].message.content or ""
    trace.append({
        "round": MAX_RESEARCH_ROUNDS + 1,
        "event": "final_answer",
        "model": used_model,
        "evidence_sources": len(collected_evidence),
        "timestamp": time.time() - start_time,
    })

    # --- Step 4: Parse and return ---
    claims, sources, summary = _parse_answer(answer_text)

    degraded_reason = _is_degraded_answer(answer_text, question)
    if degraded_reason:
        trace.append({
            "event": "degraded_answer",
            "reason": degraded_reason,
            "timestamp": time.time() - start_time,
        })
        if collected_evidence:
            titles = [info['title'] for info in collected_evidence.values()]
            summary = (
                f"Could not generate a natural-language answer. "
                f"Evidence was collected from {len(titles)} source(s): "
                + "; ".join(titles[:5]) + "."
            )
            claims = []
            sources = list(collected_evidence.keys())
        else:
            summary = "Could not generate an answer — no evidence was collected."

    if memory_used:
        for c in claims:
            c.from_memory = True

    total_cost.model = used_model
    answer = AnalystAnswer(
        question=question,
        plan=plan_text,
        claims=claims,
        summary=summary,
        sources_used=sources,
        tool_trace=trace,
        cost=total_cost,
    )

    if memory and not degraded_reason:
        _store_entities(answer, memory)

    return answer


def _clean_url(url: str) -> str:
    """Strip trailing bracket characters and punctuation from a URL."""
    url = re.sub(r'[\]\)】》>]+[.,:;!?\s]*$', '', url)
    url = re.sub(r'[.,:;!?\s]+$', '', url)
    return url


def _extract_urls(text: str) -> list[str]:
    """Find all URLs in text, handling both [URL] and 【URL】 citation styles."""
    raw = re.findall(r'https?://[^\s,\]\)】》>]+', text)
    return [_clean_url(u) for u in raw]


_CITE_PATTERN = re.compile(r'[\[【]\s*(https?://[^\]\)】》>\s]+)\s*[\]】]')

# Patterns for text that contains periods but should NOT trigger a sentence split.
_ABBREVS = re.compile(
    r'\b(Dr|Mr|Mrs|Ms|Jr|Sr|vs|etc|Inc|Ltd|Corp|Co|Prof|Gen|Gov|approx|est)\.'
)
_INITIALS = re.compile(r'\b([A-Z])\. ')


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, preserving initials like C. K. Venkataraman
    and abbreviations like Co. Ltd."""
    _PLACEHOLDER = "�"
    work = _ABBREVS.sub(lambda m: m.group(1) + _PLACEHOLDER, text)
    work = _INITIALS.sub(lambda m: m.group(1) + _PLACEHOLDER + " ", work)

    parts = re.split(r'(?<=[.!?])\s+', work)

    result = []
    for p in parts:
        p = p.replace(_PLACEHOLDER, ".").strip()
        if p:
            result.append(p)
    return result


def _extract_source_section(text: str) -> tuple[str, list[str]]:
    """Split off the SOURCES section and return (answer_body, source_urls).
    Handles SOURCES:, **Sources:**, Sources:, numbered references, etc."""
    source_urls: list[str] = []

    # Try several common header patterns
    for pattern in [
        r'\n\s*\*{0,2}SOURCES\*{0,2}\s*:',
        r'\n\s*\*{0,2}Sources\*{0,2}\s*:',
        r'\n\s*\*{0,2}References\*{0,2}\s*:',
    ]:
        match = re.search(pattern, text)
        if match:
            answer_body = text[:match.start()].strip()
            source_block = text[match.end():].strip()
            source_urls.extend(_extract_urls(source_block))
            return answer_body, source_urls

    return text, source_urls


def _strip_answer_prefix(text: str) -> str:
    """Remove ANSWER: / **Answer:** prefix variants."""
    for pattern in [
        r'^\*{0,2}ANSWER\*{0,2}\s*:\s*',
        r'^\*{0,2}Answer\*{0,2}\s*:\s*',
    ]:
        text = re.sub(pattern, '', text, count=1).strip()
    return text


def _clean_claim_text(text: str) -> str:
    """Remove citation markup, URLs, brackets, and leading/trailing markdown."""
    text = _CITE_PATTERN.sub('', text)
    text = re.sub(r'https?://[^\s,\]\)】》>]+', '', text)
    text = re.sub(r'[\[【】\]]', '', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    # Strip leading/trailing markdown bold/italic markers
    text = re.sub(r'^[\s*_]+|[\s*_]+$', '', text).strip()
    return text


_GARBAGE_RE = re.compile(r'^[\d\s\.\-\*#:,;!?]+$')


def _is_substantive(text: str) -> bool:
    """Return False for fragments that are not real claims: lone numbers,
    punctuation, markdown artifacts, or strings shorter than 8 characters
    after stripping formatting."""
    stripped = re.sub(r'[\s\*#\-_>]', '', text)
    if len(stripped) < 8:
        return False
    if _GARBAGE_RE.match(text):
        return False
    return True


_NONSENSE_MARKERS = [
    "base64 decode", "encoded data", "string of characters",
    "cannot interpret", "cannot determine", "here-string",
    "prompt injection", "as an ai",
]


def _is_degraded_answer(answer_text: str, question: str) -> str | None:
    """Return a short reason if the answer is obviously malformed, else None."""
    stripped = answer_text.strip()
    if stripped.startswith("{") and '"query"' in stripped[:500]:
        return "answer is raw JSON (model echoed tool-call data)"
    lower = answer_text.lower()
    for marker in _NONSENSE_MARKERS:
        if marker in lower:
            return f"answer contains nonsense marker: '{marker}'"
    words = answer_text.split()
    if len(words) < 10:
        return f"answer too short ({len(words)} words)"
    return None


def _parse_answer(text: str) -> tuple[list[Claim], list[str], str]:
    """Parse the final answer text into claims, sources, and a summary."""
    # Step 1: separate the sources section from the answer body
    answer_text, section_sources = _extract_source_section(text)
    answer_text = _strip_answer_prefix(answer_text)
    summary = answer_text

    # Step 2: split into sentences and extract per-claim citations
    claims: list[Claim] = []
    inline_sources: list[str] = []
    any_inline_citation = False

    sentences = _split_sentences(answer_text)
    for sent in sentences:
        # Find inline citations [URL] or 【URL】
        cited_urls = _CITE_PATTERN.findall(sent)
        citation = _clean_url(cited_urls[0]) if cited_urls else None

        # Also catch bare URLs
        if not citation:
            bare = _extract_urls(sent)
            if bare:
                citation = bare[0]

        if citation:
            any_inline_citation = True
            inline_sources.append(citation)

        clean = _clean_claim_text(sent)
        if clean and _is_substantive(clean):
            claims.append(Claim(text=clean, citation=citation))

    # Step 3: merge and deduplicate all sources
    all_sources = section_sources + inline_sources
    all_sources = list(dict.fromkeys(all_sources))
    return claims, all_sources, summary
