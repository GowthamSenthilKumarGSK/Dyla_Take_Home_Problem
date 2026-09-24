"""Analyst agent — plans, searches the web, and answers research questions."""
from __future__ import annotations
import json
import re
import time
from openai import OpenAI, RateLimitError, APIStatusError

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
short plan (2-4 bullet points) describing:
- What specific facts you need to find
- What search queries you will use
- How you will cross-check claims

Respond with ONLY the plan, no other text. Do not answer the question yet.
{memory_section}
Question: {question}
"""

MAX_TOOL_ROUNDS = 15


def _is_provider_error(exc: Exception) -> bool:
    """True for errors indicating the model/provider is temporarily unavailable.
    False for auth errors (401), bad requests (400), or application errors."""
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIStatusError) and exc.status_code in (502, 503, 529):
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
    return OpenAI(api_key="ollama", base_url=config.OLLAMA_BASE_URL)


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


def run_analyst(question: str, model: str | None = None,
                memory: EntityMemory | None = None) -> AnalystAnswer:
    """Run the analyst agent on a single question. Returns the structured
    answer together with the full tool-call trace."""

    model = model or config.ANALYST_MODEL
    client = OpenAI(
        api_key=config.OPENROUTER_API_KEY,
        base_url=config.OPENROUTER_BASE_URL,
    )

    # Include memory_lookup tool only when memory is provided
    if memory:
        tools = list(TOOL_DEFINITIONS)
    else:
        tools = [t for t in TOOL_DEFINITIONS if t["function"]["name"] != "memory_lookup"]

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

    # --- Step 1: Explicit planning (no tools available) ---
    plan, client, model = _generate_plan(
        client, model, question, trace, start_time, total_cost,
        memory_context=memory_context,
    )

    # --- Step 2: Research loop with tools ---
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Research question: {question}\n\n"
            f"Your research plan:\n{plan}\n\n"
            "Now execute the plan using the available tools. "
            "When you have enough evidence, give your final answer."
        )},
    ]

    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        response, used_model, client = _call_llm(
            client, model, messages, trace, round_num, start_time,
            tools=tools,
        )
        model = used_model

        choice = response.choices[0]
        msg = choice.message

        # Track cost
        if response.usage:
            rc = _track_cost(response.usage, used_model)
            total_cost.input_tokens += rc.input_tokens
            total_cost.output_tokens += rc.output_tokens
            total_cost.cost_usd += rc.cost_usd

        # If the model wants to call tools
        if msg.tool_calls:
            messages.append(msg)

            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments)

                trace_entry = {
                    "round": round_num,
                    "tool": fn_name,
                    "arguments": fn_args,
                    "model": used_model,
                    "timestamp": time.time() - start_time,
                }

                result_str = _execute_tool_call(fn_name, fn_args, memory=memory)
                trace_entry["result_preview"] = result_str[:1000]
                trace.append(trace_entry)

                if fn_name == "memory_lookup" and "No prior knowledge" not in result_str:
                    memory_used = True

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

        # If the model returns a final text answer (no tool calls)
        elif msg.content:
            trace.append({
                "round": round_num,
                "event": "final_answer",
                "model": used_model,
                "timestamp": time.time() - start_time,
            })

            answer_text = msg.content
            claims, sources, summary = _parse_answer(answer_text)

            if memory_used:
                for c in claims:
                    c.from_memory = True

            total_cost.model = used_model
            answer = AnalystAnswer(
                question=question,
                plan=plan,
                claims=claims,
                summary=summary,
                sources_used=sources,
                tool_trace=trace,
                cost=total_cost,
            )

            if memory:
                _store_entities(answer, memory)

            return answer

        else:
            trace.append({"round": round_num, "event": "empty_response"})
            break

    # If we hit the max rounds without a final answer
    return AnalystAnswer(
        question=question,
        plan=plan,
        summary="Agent reached maximum tool rounds without a final answer.",
        tool_trace=trace,
        cost=total_cost,
    )


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
        if clean:
            claims.append(Claim(text=clean, citation=citation))

    # Step 3: merge and deduplicate all sources
    all_sources = section_sources + inline_sources
    all_sources = list(dict.fromkeys(all_sources))
    return claims, all_sources, summary
