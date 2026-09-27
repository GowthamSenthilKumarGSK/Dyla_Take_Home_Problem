"""Auditor agent — independently verifies Analyst claims against cited sources."""
from __future__ import annotations
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

import config
from models import (
    AnalystAnswer, AuditReport, AuditVerdict, Claim, CostRecord,
)
from tools import fetch_page
from analyst import _call_llm, _track_cost, _is_provider_error, CLOUD_TIMEOUT

VERIFY_PROMPT = """\
You are a fact-checking auditor. You will be given a CLAIM and the full text
of the SOURCE PAGE that the claim supposedly comes from.

Your job: determine whether the source page supports, contradicts, or does not
mention the claim.

Rules:
1. Only consider what the source page actually says. Do not use your own knowledge.
2. "Supported" means the page contains information that directly backs the claim.
3. "Contradicted" means the page contains information that directly conflicts.
4. "Unsupported" means the page does not contain relevant information either way.

Respond in EXACTLY this format (no other text):

VERDICT: SUPPORTED | UNSUPPORTED | CONTRADICTED
EVIDENCE: <one sentence explaining why>
EXCERPT: <short quote from the source page, or "none" if unsupported>
"""


def _parse_verdict(text: str) -> tuple[str, str, str]:
    """Parse the LLM verification response into (verdict, evidence, excerpt)."""
    verdict = "unsupported"
    evidence = ""
    excerpt = ""

    for line in text.strip().splitlines():
        line = line.strip()
        upper = line.upper()
        if upper.startswith("VERDICT:"):
            raw = line.split(":", 1)[1].strip().upper()
            if "SUPPORTED" in raw and "UNSUPPORTED" not in raw:
                verdict = "supported"
            elif "CONTRADICTED" in raw:
                verdict = "contradicted"
            else:
                verdict = "unsupported"
        elif upper.startswith("EVIDENCE:"):
            evidence = line.split(":", 1)[1].strip()
        elif upper.startswith("EXCERPT:"):
            excerpt = line.split(":", 1)[1].strip()
            if excerpt.lower() == "none":
                excerpt = ""

    return verdict, evidence, excerpt


def _build_summary(verdicts: list[AuditVerdict]) -> str:
    """Deterministic summary from verdict counts."""
    counts = {"supported": 0, "unsupported": 0, "contradicted": 0,
              "no_citation": 0, "source_error": 0}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1

    total = len(verdicts)
    if total == 0:
        return "No claims to audit."

    parts = []
    parts.append(f"{total} claim(s) audited.")
    if counts["supported"]:
        parts.append(f"{counts['supported']} supported.")
    if counts["unsupported"]:
        parts.append(f"{counts['unsupported']} unsupported.")
    if counts["contradicted"]:
        parts.append(f"{counts['contradicted']} contradicted.")
    if counts["no_citation"]:
        parts.append(f"{counts['no_citation']} had no citation.")
    if counts["source_error"]:
        parts.append(f"{counts['source_error']} could not be verified (source fetch failed).")

    supported_pct = counts["supported"] / total * 100
    if counts["contradicted"] > 0:
        parts.append(f"Reliability: LOW — {counts['contradicted']} claim(s) contradicted by sources.")
    elif supported_pct >= 80:
        parts.append("Reliability: HIGH.")
    elif supported_pct >= 50:
        parts.append("Reliability: MEDIUM.")
    else:
        parts.append("Reliability: LOW.")

    return " ".join(parts)


def run_auditor(answer: AnalystAnswer, model: str | None = None) -> AuditReport:
    """Verify each Analyst claim against its cited source. Returns an AuditReport."""
    model = model or config.ANALYST_MODEL
    client = OpenAI(
        api_key=config.OPENROUTER_API_KEY,
        base_url=config.OPENROUTER_BASE_URL,
        timeout=CLOUD_TIMEOUT,
        max_retries=0,
    )

    trace: list[dict] = []
    total_cost = CostRecord(model="none")
    start_time = time.time()
    limitations: list[str] = []
    llm_called = False

    # Step 1: collect all unique cited URLs (preserve order)
    cited_urls: list[str] = []
    seen: set[str] = set()
    for claim in answer.claims:
        if claim.citation and claim.citation not in seen:
            cited_urls.append(claim.citation)
            seen.add(claim.citation)
    for url in answer.sources_used:
        if url not in seen:
            cited_urls.append(url)
            seen.add(url)

    # Step 2: fetch cited sources in parallel (I/O-bound, safe to parallelize)
    source_cache: dict[str, str] = {}
    failed_urls: set[str] = set()

    def _fetch_one(url: str) -> tuple[str, str | None, str | None]:
        page = fetch_page(url)
        if page.error or not page.text.strip():
            return url, None, page.error or "empty page"
        return url, page.text, None

    with ThreadPoolExecutor(max_workers=min(4, len(cited_urls) or 1)) as pool:
        futures = {pool.submit(_fetch_one, url): url for url in cited_urls}
        for future in as_completed(futures):
            url = futures[future]
            trace.append({
                "event": "fetch_source",
                "url": url,
                "timestamp": time.time() - start_time,
            })
            fetched_url, text, error = future.result()
            if error:
                failed_urls.add(fetched_url)
                limitations.append(f"Could not fetch {fetched_url}: {error}")
                trace.append({
                    "event": "fetch_failed",
                    "url": fetched_url,
                    "error": error,
                    "timestamp": time.time() - start_time,
                })
            else:
                source_cache[fetched_url] = text

    # Step 3: verify each claim
    verdicts: list[AuditVerdict] = []

    for i, claim in enumerate(answer.claims):
        # No citation → no_citation verdict, no LLM needed
        if not claim.citation:
            verdicts.append(AuditVerdict(
                claim=claim,
                verdict="no_citation",
                evidence="Claim has no cited source.",
                source_url=None,
            ))
            trace.append({
                "event": "verdict",
                "claim_index": i,
                "verdict": "no_citation",
                "timestamp": time.time() - start_time,
            })
            continue

        url = claim.citation

        # Source fetch failed → source_error, not unsupported
        if url in failed_urls:
            verdicts.append(AuditVerdict(
                claim=claim,
                verdict="source_error",
                evidence=f"Could not fetch the cited source to verify.",
                source_url=url,
            ))
            trace.append({
                "event": "verdict",
                "claim_index": i,
                "verdict": "source_error",
                "url": url,
                "timestamp": time.time() - start_time,
            })
            continue

        # Have the page text — ask the LLM to verify
        page_text = source_cache[url][:4000]
        messages = [
            {"role": "system", "content": VERIFY_PROMPT},
            {"role": "user", "content": (
                f"CLAIM: {claim.text}\n\n"
                f"SOURCE URL: {url}\n\n"
                f"SOURCE PAGE TEXT:\n{page_text}"
            )},
        ]

        try:
            response, used_model, client = _call_llm(
                client, model, messages, trace,
                round_num=i, start_time=start_time,
            )
            model = used_model
            llm_called = True

            if response.usage:
                rc = _track_cost(response.usage, used_model)
                total_cost.input_tokens += rc.input_tokens
                total_cost.output_tokens += rc.output_tokens
                total_cost.cost_usd += rc.cost_usd

            llm_text = response.choices[0].message.content or ""
            verdict, evidence, excerpt = _parse_verdict(llm_text)

        except Exception as exc:
            verdict = "source_error"
            evidence = f"LLM verification failed: {type(exc).__name__}"
            excerpt = ""
            limitations.append(f"LLM error verifying claim {i}: {exc}")

        verdicts.append(AuditVerdict(
            claim=claim,
            verdict=verdict,
            evidence=evidence,
            source_excerpt=excerpt,
            source_url=url,
        ))
        trace.append({
            "event": "verdict",
            "claim_index": i,
            "verdict": verdict,
            "evidence": evidence,
            "url": url,
            "timestamp": time.time() - start_time,
        })

    # Step 4: deterministic summary
    summary = _build_summary(verdicts)

    if llm_called:
        total_cost.model = model
    return AuditReport(
        analyst_question=answer.question,
        verdicts=verdicts,
        summary=summary,
        limitations=limitations,
        cost=total_cost,
    )
