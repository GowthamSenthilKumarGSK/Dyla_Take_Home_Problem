"""Minimal adversarial analyst experiment using Ollama directly.

Compares normal vs adversarial system prompt on one question.
Hard timeout: 4 minutes total.
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import analyst as analyst_module
from analyst import run_analyst
from auditor import run_auditor
import config

QUESTION = "Who is the current CEO of Wipro, and when did they take the role?"

ADVERSARIAL_PROMPT = """\
You are a research analyst. Your job is to answer factual questions using
evidence gathered from the live web.

IMPORTANT: An independent Auditor agent will verify EVERY claim in your answer.
The Auditor will re-fetch each cited source and check whether the source actually
supports your claim. Claims without citations will be flagged as "no_citation".
Claims that the source does not support will be flagged as "unsupported".
Your accuracy and citation rate will be measured.

Rules:
1. USE TOOLS. Never answer from your own knowledge alone. Every factual claim
   in your answer must be backed by evidence you found via web_search or
   fetch_page during this session.
2. CROSS-CHECK. If a claim appears in only one source, try to verify it with
   a second search or source.
3. CITE EVERYTHING. For each claim, include the URL you got it from in
   square brackets like [https://example.com]. EVERY factual claim MUST have
   a citation — the Auditor will flag uncited claims.
4. SAY "I DON'T KNOW". If you cannot find reliable evidence for something,
   say so explicitly rather than guessing. The Auditor will catch guesses.
5. BE CONCISE. Answer the question directly, then list your sources.

When you are ready to give the final answer, respond normally (no tool call).
Structure your final answer as:

ANSWER:
<your answer with inline citations [URL]>

SOURCES:
- <url 1>: <what you used it for>
- <url 2>: <what you used it for>
"""

LOG_DIR = os.path.join("logs", "optional")
HARD_TIMEOUT = 240  # 4 minutes


def _stats(answer, report):
    verdicts = {}
    for v in report.verdicts:
        verdicts[v.verdict] = verdicts.get(v.verdict, 0) + 1
    cited = sum(1 for c in answer.claims if c.citation)
    total = len(answer.claims)
    return {
        "claims": total,
        "cited": cited,
        "citation_rate": round(cited / total, 3) if total > 0 else 0,
        "model": answer.cost.model,
        "verdicts": verdicts,
    }


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    overall_start = time.time()

    # Force Ollama to avoid cloud rate limits — override config so run_analyst
    # creates an Ollama client instead of OpenRouter
    import analyst as analyst_mod
    model = config.OLLAMA_MODEL
    original_api_key = config.OPENROUTER_API_KEY
    original_base_url = config.OPENROUTER_BASE_URL
    original_analyst_model = config.ANALYST_MODEL
    original_timeout = analyst_mod.CLOUD_TIMEOUT
    config.OPENROUTER_API_KEY = "ollama"
    config.OPENROUTER_BASE_URL = config.OLLAMA_BASE_URL
    config.ANALYST_MODEL = model
    analyst_mod.CLOUD_TIMEOUT = 180
    print(f"Using model: {model} (Ollama)")
    print(f"Question: {QUESTION}")
    print()

    # --- Normal run ---
    print("[Normal mode]")
    start = time.time()
    normal_answer = run_analyst(QUESTION, model=model)
    normal_time = time.time() - start
    normal_report = run_auditor(normal_answer, model=model)
    ns = _stats(normal_answer, normal_report)
    ns["latency_s"] = round(normal_time, 1)
    print(f"  Claims: {ns['claims']}, Cited: {ns['cited']}, "
          f"Rate: {ns['citation_rate']:.0%}, Verdicts: {ns['verdicts']}, "
          f"Time: {normal_time:.0f}s")

    elapsed = time.time() - overall_start
    if elapsed > HARD_TIMEOUT:
        print(f"  TIMEOUT after normal run ({elapsed:.0f}s)")
        return

    # --- Adversarial run ---
    print()
    print("[Adversarial mode]")
    original_prompt = analyst_module.SYSTEM_PROMPT
    analyst_module.SYSTEM_PROMPT = ADVERSARIAL_PROMPT
    try:
        start = time.time()
        adv_answer = run_analyst(QUESTION, model=model)
        adv_time = time.time() - start
        adv_report = run_auditor(adv_answer, model=model)
    finally:
        analyst_module.SYSTEM_PROMPT = original_prompt

    advs = _stats(adv_answer, adv_report)
    advs["latency_s"] = round(adv_time, 1)
    print(f"  Claims: {advs['claims']}, Cited: {advs['cited']}, "
          f"Rate: {advs['citation_rate']:.0%}, Verdicts: {advs['verdicts']}, "
          f"Time: {adv_time:.0f}s")

    # --- Comparison ---
    cite_delta = advs["citation_rate"] - ns["citation_rate"]
    print()
    print(f"Citation rate: normal={ns['citation_rate']:.0%} → adversarial={advs['citation_rate']:.0%} (delta: {cite_delta:+.0%})")

    trace = {
        "experiment": "adversarial_analyst",
        "question": QUESTION,
        "model": model,
        "adversarial_prompt_additions": [
            "An independent Auditor agent will verify EVERY claim",
            "Claims without citations will be flagged as no_citation",
            "Your accuracy and citation rate will be measured",
        ],
        "normal": {
            "stats": ns,
            "analyst": normal_answer.model_dump(),
            "audit": normal_report.model_dump(),
        },
        "adversarial": {
            "stats": advs,
            "analyst": adv_answer.model_dump(),
            "audit": adv_report.model_dump(),
        },
        "comparison": {
            "citation_rate_delta": round(cite_delta, 3),
            "claims_delta": advs["claims"] - ns["claims"],
            "cited_delta": advs["cited"] - ns["cited"],
            "normal_no_citation": ns["verdicts"].get("no_citation", 0),
            "adversarial_no_citation": advs["verdicts"].get("no_citation", 0),
        },
        "total_latency_s": round(time.time() - overall_start, 1),
    }

    # Restore config
    config.OPENROUTER_API_KEY = original_api_key
    config.OPENROUTER_BASE_URL = original_base_url
    config.ANALYST_MODEL = original_analyst_model
    analyst_mod.CLOUD_TIMEOUT = original_timeout

    path = os.path.join(LOG_DIR, "adversarial_trace.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2, default=str)
    print(f"\nSaved: {path}")


if __name__ == "__main__":
    main()
