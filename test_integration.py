"""End-to-end integration test: Analyst -> Auditor on one question."""
from __future__ import annotations
import json
import os
import traceback

from analyst import run_analyst
from auditor import run_auditor

QUESTION = (
    "Who is the current CEO of Titan Company, when did they take over "
    "the role, and what was their previous position before becoming CEO?"
)


def main():
    print("=" * 70)
    print("INTEGRATION TEST -- Analyst -> Auditor")
    print("=" * 70)
    print(f"\nQuestion: {QUESTION}\n")

    # ── Step 1: Run Analyst ──────────────────────────────────────────
    print("Running Analyst...")
    try:
        answer = run_analyst(QUESTION)
    except Exception:
        print("\nANALYST FAILED:")
        traceback.print_exc()
        return

    print("\n" + "=" * 70)
    print("ANALYST RESULT")
    print("=" * 70)

    print(f"\nPlan:\n{answer.plan}\n")

    print("Tool Trace:")
    for entry in answer.tool_trace:
        event = entry.get("event", entry.get("tool", "?"))
        model = entry.get("model", "")
        ts = entry.get("timestamp", 0)
        rnd = entry.get("round", "?")
        if event in ("model_fallback", "ollama_fallback"):
            print(f"  [Round {rnd}] {event}: {entry.get('from_model')} -> {entry.get('to_model')}  (t={ts:.1f}s)")
        elif "tool" in entry:
            print(f"  [Round {rnd}] {entry['tool']}({json.dumps(entry.get('arguments', {}))})  model={model}  (t={ts:.1f}s)")
        else:
            print(f"  [Round {rnd}] {event}  model={model}  (t={ts:.1f}s)")

    print(f"\nFinal Answer:\n{answer.summary[:500]}\n")

    print(f"Claims ({len(answer.claims)}):")
    for i, c in enumerate(answer.claims, 1):
        cite = c.citation or "NO CITATION"
        print(f"  {i}. {c.text[:80]}")
        print(f"     Citation: {cite}")

    print(f"\nSources ({len(answer.sources_used)}):")
    for s in answer.sources_used:
        print(f"  - {s}")

    print(f"\nAnalyst Cost:")
    print(f"  Model: {answer.cost.model}")
    print(f"  Input tokens:  {answer.cost.input_tokens:,}")
    print(f"  Output tokens: {answer.cost.output_tokens:,}")
    print(f"  Cost (USD):    ${answer.cost.cost_usd:.4f}")
    print(f"  Cost (INR):    Rs.{answer.cost.cost_inr():.2f}")

    # ── Step 2: Run Auditor ──────────────────────────────────────────
    print("\n" + "=" * 70)
    print("RUNNING AUDITOR")
    print("=" * 70)

    try:
        report = run_auditor(answer)
    except Exception:
        print("\nAUDITOR FAILED:")
        traceback.print_exc()
        return

    print(f"\nAuditor Verdicts ({len(report.verdicts)}):")
    for i, v in enumerate(report.verdicts, 1):
        print(f"\n  Claim {i}: {v.claim.text[:80]}")
        print(f"    Verdict:    {v.verdict}")
        print(f"    Source URL:  {v.source_url or 'N/A'}")
        print(f"    Evidence:   {v.evidence[:120]}")
        if v.source_excerpt:
            print(f"    Excerpt:    {v.source_excerpt[:120]}")

    print(f"\nAudit Summary: {report.summary}")

    if report.limitations:
        print(f"\nLimitations ({len(report.limitations)}):")
        for lim in report.limitations:
            print(f"  - {lim}")

    print(f"\nAuditor Cost:")
    print(f"  Model: {report.cost.model}")
    print(f"  Input tokens:  {report.cost.input_tokens:,}")
    print(f"  Output tokens: {report.cost.output_tokens:,}")
    print(f"  Cost (USD):    ${report.cost.cost_usd:.4f}")
    print(f"  Cost (INR):    Rs.{report.cost.cost_inr():.2f}")

    # ── Step 3: Save combined trace ──────────────────────────────────
    os.makedirs("logs", exist_ok=True)
    combined = {
        "question": QUESTION,
        "analyst": answer.model_dump(),
        "audit": report.model_dump(),
    }
    out_path = "logs/integration_trace.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, indent=2, default=str)

    print(f"\nCombined trace saved to {out_path}")


if __name__ == "__main__":
    main()
