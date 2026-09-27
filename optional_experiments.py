"""Optional 'Take it further' experiments — separate from the main 8-question evaluation.

Experiments:
1. Conflicting sources: a question where real sources disagree
2. Adversarial analyst: analyst told an auditor will verify every claim
3. Auditor feedback loop: unsupported claims re-researched and re-audited
"""
from __future__ import annotations
import json
import os
import sys
import time
import copy

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from analyst import run_analyst, SYSTEM_PROMPT
from auditor import run_auditor
from memory import EntityMemory
from models import AnalystAnswer, Claim

LOG_DIR = os.path.join("logs", "optional")
INR_RATE = 83.0


def _save_trace(name: str, data: dict):
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  Saved: {path}")
    return path


# ---------------------------------------------------------------------------
# Experiment 1: Conflicting Sources
# ---------------------------------------------------------------------------

CONFLICT_QUESTION = (
    "What is the population of Delhi? Report the figure from the 2011 Census "
    "and the most recent estimate, noting any differences between sources."
)


def experiment_conflict():
    """Run a question designed to surface conflicting population figures."""
    print("=" * 70)
    print("EXPERIMENT 1: CONFLICTING SOURCES")
    print("=" * 70)
    print(f"  Question: {CONFLICT_QUESTION}")
    print()

    start = time.time()
    answer = run_analyst(CONFLICT_QUESTION)
    analyst_time = time.time() - start

    report = run_auditor(answer)
    total_time = time.time() - start

    # Check for conflicts in trace
    conflict_events = [
        e for e in answer.tool_trace
        if isinstance(e, dict) and e.get("event") == "conflicts_detected"
    ]
    evidence_evals = [
        e for e in answer.tool_trace
        if isinstance(e, dict) and e.get("event") == "evidence_evaluation"
    ]

    trace = {
        "experiment": "conflicting_sources",
        "question": CONFLICT_QUESTION,
        "analyst": answer.model_dump(),
        "audit": report.model_dump(),
        "analysis": {
            "conflict_events": conflict_events,
            "evidence_evaluations": [
                {
                    "round": e.get("round"),
                    "sufficient": e.get("sufficient"),
                    "conflicts": e.get("conflicts"),
                    "missing": e.get("missing"),
                }
                for e in evidence_evals
            ],
            "total_claims": len(answer.claims),
            "cited_claims": sum(1 for c in answer.claims if c.citation),
            "fetch_events": sum(
                1 for e in answer.tool_trace
                if isinstance(e, dict) and e.get("event") == "fetch_page"
            ),
            "search_events": sum(
                1 for e in answer.tool_trace
                if isinstance(e, dict) and e.get("event") == "search"
            ),
            "conflicts_detected": len(conflict_events) > 0,
            "conflict_text": conflict_events[0]["conflicts"] if conflict_events else "none detected",
            "analyst_model": answer.cost.model,
            "analyst_latency_s": round(analyst_time, 1),
            "total_latency_s": round(total_time, 1),
        },
    }
    _save_trace("conflict_trace", trace)

    print(f"  Model: {answer.cost.model}")
    print(f"  Searches: {trace['analysis']['search_events']}, Fetches: {trace['analysis']['fetch_events']}")
    print(f"  Claims: {trace['analysis']['total_claims']}, Cited: {trace['analysis']['cited_claims']}")
    print(f"  Conflicts detected: {trace['analysis']['conflicts_detected']}")
    if conflict_events:
        print(f"  Conflict: {conflict_events[0]['conflicts'][:200]}")
    print(f"  Latency: {total_time:.0f}s")
    print()
    return trace


# ---------------------------------------------------------------------------
# Experiment 2: Adversarial Analyst
# ---------------------------------------------------------------------------

ADVERSARIAL_SYSTEM_PROMPT = """\
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

ADVERSARIAL_QUESTIONS = [
    "Who is the current CEO of Wipro, and when did they take the role?",
]


def experiment_adversarial():
    """Compare normal vs adversarial analyst on the same questions."""
    import analyst as analyst_module

    print("=" * 70)
    print("EXPERIMENT 2: ADVERSARIAL ANALYST")
    print("=" * 70)
    print()

    results = []

    for question in ADVERSARIAL_QUESTIONS:
        print(f"  Question: {question}")

        # Normal run
        print("  [Normal mode]")
        start = time.time()
        normal_answer = run_analyst(question)
        normal_time = time.time() - start
        normal_report = run_auditor(normal_answer)

        normal_stats = {
            "mode": "normal",
            "claims": len(normal_answer.claims),
            "cited": sum(1 for c in normal_answer.claims if c.citation),
            "model": normal_answer.cost.model,
            "latency_s": round(normal_time, 1),
        }
        normal_verdicts = {}
        for v in normal_report.verdicts:
            normal_verdicts[v.verdict] = normal_verdicts.get(v.verdict, 0) + 1
        normal_stats["verdicts"] = normal_verdicts
        normal_stats["citation_rate"] = (
            normal_stats["cited"] / normal_stats["claims"]
            if normal_stats["claims"] > 0 else 0
        )

        print(f"    Claims: {normal_stats['claims']}, Cited: {normal_stats['cited']}, "
              f"Rate: {normal_stats['citation_rate']:.0%}, Verdicts: {normal_verdicts}")

        # Adversarial run — swap system prompt
        print("  [Adversarial mode] (waiting 20s for rate limit cooldown...)")
        time.sleep(20)
        original_prompt = analyst_module.SYSTEM_PROMPT
        analyst_module.SYSTEM_PROMPT = ADVERSARIAL_SYSTEM_PROMPT
        try:
            start = time.time()
            adv_answer = run_analyst(question)
            adv_time = time.time() - start
            adv_report = run_auditor(adv_answer)
        finally:
            analyst_module.SYSTEM_PROMPT = original_prompt

        adv_stats = {
            "mode": "adversarial",
            "claims": len(adv_answer.claims),
            "cited": sum(1 for c in adv_answer.claims if c.citation),
            "model": adv_answer.cost.model,
            "latency_s": round(adv_time, 1),
        }
        adv_verdicts = {}
        for v in adv_report.verdicts:
            adv_verdicts[v.verdict] = adv_verdicts.get(v.verdict, 0) + 1
        adv_stats["verdicts"] = adv_verdicts
        adv_stats["citation_rate"] = (
            adv_stats["cited"] / adv_stats["claims"]
            if adv_stats["claims"] > 0 else 0
        )

        print(f"    Claims: {adv_stats['claims']}, Cited: {adv_stats['cited']}, "
              f"Rate: {adv_stats['citation_rate']:.0%}, Verdicts: {adv_verdicts}")

        # Comparison
        cite_delta = adv_stats["citation_rate"] - normal_stats["citation_rate"]
        print(f"    Citation rate delta: {cite_delta:+.0%}")
        print()

        results.append({
            "question": question,
            "normal": {
                "stats": normal_stats,
                "analyst": normal_answer.model_dump(),
                "audit": normal_report.model_dump(),
            },
            "adversarial": {
                "stats": adv_stats,
                "analyst": adv_answer.model_dump(),
                "audit": adv_report.model_dump(),
            },
            "comparison": {
                "citation_rate_delta": round(cite_delta, 3),
                "claims_delta": adv_stats["claims"] - normal_stats["claims"],
                "cited_delta": adv_stats["cited"] - normal_stats["cited"],
            },
        })

    trace = {
        "experiment": "adversarial_analyst",
        "adversarial_prompt_additions": [
            "An independent Auditor agent will verify EVERY claim",
            "Claims without citations will be flagged as no_citation",
            "Claims that the source does not support will be flagged as unsupported",
            "Your accuracy and citation rate will be measured",
        ],
        "questions": results,
        "summary": {
            "total_questions": len(results),
            "avg_normal_citation_rate": sum(
                r["normal"]["stats"]["citation_rate"] for r in results
            ) / max(len(results), 1),
            "avg_adversarial_citation_rate": sum(
                r["adversarial"]["stats"]["citation_rate"] for r in results
            ) / max(len(results), 1),
        },
    }
    _save_trace("adversarial_trace", trace)
    return trace


# ---------------------------------------------------------------------------
# Experiment 3: Auditor → Analyst Feedback Loop
# ---------------------------------------------------------------------------

FEEDBACK_QUESTION = (
    "What is the market capitalization of Reliance Industries, "
    "and who is the current chairman?"
)

FEEDBACK_PROMPT = """\
You are a research analyst. Your previous answer to the following question
was audited, and the Auditor found problems with some of your claims.

Question: {question}

Your previous answer:
{previous_summary}

Auditor findings that need correction:
{findings}

Instructions:
1. For each problematic claim, search for better evidence.
2. Replace unsupported or contradicted claims with properly cited ones.
3. Keep claims that were already supported or had no issues.
4. Produce a complete revised answer with inline [URL] citations.

Structure your response as:

ANSWER:
<your revised answer with inline citations [URL]>

SOURCES:
- <url 1>: <what you used it for>
"""


def _format_findings(report) -> str:
    """Format auditor findings for feedback to analyst."""
    lines = []
    for i, v in enumerate(report.verdicts):
        if v.verdict in ("unsupported", "contradicted", "no_citation"):
            lines.append(
                f"- Claim {i+1}: \"{v.claim.text[:120]}\" → "
                f"verdict={v.verdict}, reason={v.evidence}"
            )
    return "\n".join(lines) if lines else "No issues found."


def experiment_feedback_loop():
    """Demonstrate Auditor → Analyst feedback with re-research."""
    print("=" * 70)
    print("EXPERIMENT 3: AUDITOR → ANALYST FEEDBACK LOOP")
    print("=" * 70)
    print(f"  Question: {FEEDBACK_QUESTION}")
    print()

    # --- Pass 1: Initial Analyst + Auditor ---
    print("  [Pass 1: Initial research]")
    start = time.time()
    answer1 = run_analyst(FEEDBACK_QUESTION)
    report1 = run_auditor(answer1)
    pass1_time = time.time() - start

    v1_counts = {}
    for v in report1.verdicts:
        v1_counts[v.verdict] = v1_counts.get(v.verdict, 0) + 1

    problem_count = sum(
        1 for v in report1.verdicts
        if v.verdict in ("unsupported", "contradicted", "no_citation")
    )

    print(f"    Claims: {len(answer1.claims)}, Verdicts: {v1_counts}")
    print(f"    Problems: {problem_count}")
    print(f"    Latency: {pass1_time:.0f}s")

    if problem_count == 0:
        print("    No problems found — feedback loop not needed.")
        trace = {
            "experiment": "feedback_loop",
            "question": FEEDBACK_QUESTION,
            "pass1": {
                "analyst": answer1.model_dump(),
                "audit": report1.model_dump(),
                "verdict_counts": v1_counts,
                "problem_count": 0,
            },
            "feedback_triggered": False,
            "reason": "No unsupported/contradicted/no_citation claims to fix",
        }
        _save_trace("feedback_loop_trace", trace)
        return trace

    # --- Feedback: format findings for analyst ---
    findings_text = _format_findings(report1)
    print(f"    Findings sent to Analyst:\n{findings_text[:300]}")

    # --- Pass 2: Analyst re-researches with feedback ---
    print()
    print("  [Pass 2: Re-research with auditor feedback]")
    from analyst import _call_llm, _track_cost, _parse_answer, _is_degraded_answer
    from analyst import SYSTEM_PROMPT as SYS, CLOUD_TIMEOUT
    from openai import OpenAI
    import config
    from models import CostRecord

    feedback_messages = [
        {"role": "system", "content": SYS},
        {"role": "user", "content": FEEDBACK_PROMPT.format(
            question=FEEDBACK_QUESTION,
            previous_summary=answer1.summary,
            findings=findings_text,
        )},
    ]

    # Re-search for failed claims
    from tools import web_search
    feedback_trace = []
    feedback_start = time.time()
    feedback_cost = CostRecord(model="")

    failed_claims = [
        v.claim for v in report1.verdicts
        if v.verdict in ("unsupported", "contradicted", "no_citation")
    ]

    # Extract key terms from failed claims and search
    collected_evidence = {}
    for claim in failed_claims[:3]:
        words = claim.text.split()[:8]
        query = " ".join(words)
        results = web_search(query)
        feedback_trace.append({
            "event": "feedback_search",
            "query": query,
            "result_count": len(results.results),
            "timestamp": time.time() - feedback_start,
        })
        if not results.error:
            for r in results.results[:2]:
                from tools import fetch_page as fp
                page = fp(r.url)
                if not page.error and page.text.strip():
                    collected_evidence[r.url] = {
                        "title": page.title or r.title,
                        "text": page.text,
                    }
                    feedback_trace.append({
                        "event": "feedback_fetch",
                        "url": r.url,
                        "success": True,
                        "timestamp": time.time() - feedback_start,
                    })

    # Build revised answer with fresh evidence
    evidence_block = ""
    for url, info in collected_evidence.items():
        preview = info['text'][:3000]
        evidence_block += f"\n\nSource: {url}\nTitle: {info['title']}\n{preview}\n"

    if evidence_block:
        feedback_messages[1]["content"] += (
            f"\n\nAdditional evidence from re-research:"
            f"{evidence_block}"
        )

    client = OpenAI(
        api_key=config.OPENROUTER_API_KEY,
        base_url=config.OPENROUTER_BASE_URL,
        timeout=CLOUD_TIMEOUT,
        max_retries=0,
    )
    model = config.ANALYST_MODEL

    response, used_model, client = _call_llm(
        client, model, feedback_messages, feedback_trace,
        round_num=0, start_time=feedback_start,
    )

    if response.usage:
        rc = _track_cost(response.usage, used_model)
        feedback_cost.input_tokens += rc.input_tokens
        feedback_cost.output_tokens += rc.output_tokens
        feedback_cost.cost_usd += rc.cost_usd
    feedback_cost.model = used_model

    revised_text = response.choices[0].message.content or ""
    claims2, sources2, summary2 = _parse_answer(revised_text)

    answer2 = AnalystAnswer(
        question=FEEDBACK_QUESTION,
        plan=f"Re-research after auditor feedback ({problem_count} problems)",
        claims=claims2,
        summary=summary2,
        sources_used=sources2,
        tool_trace=feedback_trace,
        cost=feedback_cost,
    )
    pass2_time = time.time() - feedback_start

    print(f"    Revised claims: {len(answer2.claims)}, Cited: {sum(1 for c in answer2.claims if c.citation)}")

    # --- Pass 2 audit ---
    report2 = run_auditor(answer2)
    total_time = time.time() - start

    v2_counts = {}
    for v in report2.verdicts:
        v2_counts[v.verdict] = v2_counts.get(v.verdict, 0) + 1

    problem_count2 = sum(
        1 for v in report2.verdicts
        if v.verdict in ("unsupported", "contradicted", "no_citation")
    )

    print(f"    Verdicts: {v2_counts}")
    print(f"    Problems after feedback: {problem_count2} (was {problem_count})")
    print(f"    Total latency: {total_time:.0f}s")

    # Build trace
    trace = {
        "experiment": "feedback_loop",
        "question": FEEDBACK_QUESTION,
        "pass1": {
            "analyst": answer1.model_dump(),
            "audit": report1.model_dump(),
            "verdict_counts": v1_counts,
            "problem_count": problem_count,
            "latency_s": round(pass1_time, 1),
        },
        "feedback_triggered": True,
        "feedback": {
            "findings_sent": findings_text,
            "failed_claims": [c.model_dump() for c in failed_claims],
            "re_searches": sum(
                1 for e in feedback_trace
                if e.get("event") == "feedback_search"
            ),
            "re_fetches": sum(
                1 for e in feedback_trace
                if e.get("event") == "feedback_fetch"
            ),
            "new_evidence_sources": len(collected_evidence),
        },
        "pass2": {
            "analyst": answer2.model_dump(),
            "audit": report2.model_dump(),
            "verdict_counts": v2_counts,
            "problem_count": problem_count2,
            "latency_s": round(pass2_time, 1),
        },
        "improvement": {
            "problems_before": problem_count,
            "problems_after": problem_count2,
            "delta": problem_count - problem_count2,
            "cited_before": sum(1 for c in answer1.claims if c.citation),
            "cited_after": sum(1 for c in answer2.claims if c.citation),
        },
        "total_latency_s": round(total_time, 1),
    }
    _save_trace("feedback_loop_trace", trace)

    print()
    return trace


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print()
    print("OPTIONAL EXPERIMENTS — 'Take it Further'")
    print("These are separate from the main 8-question evaluation.")
    print()

    all_results = {}

    try:
        result = experiment_conflict()
        all_results["conflict"] = "completed"
    except Exception as e:
        print(f"  FAILED: {e}")
        all_results["conflict"] = f"failed: {e}"

    try:
        result = experiment_adversarial()
        all_results["adversarial"] = "completed"
    except Exception as e:
        print(f"  FAILED: {e}")
        all_results["adversarial"] = f"failed: {e}"

    try:
        result = experiment_feedback_loop()
        all_results["feedback_loop"] = "completed"
    except Exception as e:
        print(f"  FAILED: {e}")
        all_results["feedback_loop"] = f"failed: {e}"

    print("=" * 70)
    print("EXPERIMENT SUMMARY")
    print("=" * 70)
    for name, status in all_results.items():
        print(f"  {name}: {status}")
    print()

    _save_trace("experiments_summary", all_results)


if __name__ == "__main__":
    main()
