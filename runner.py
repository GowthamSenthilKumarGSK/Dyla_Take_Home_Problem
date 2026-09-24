"""8-question evaluation runner: Analyst -> Auditor with persistent memory."""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from analyst import run_analyst
from auditor import run_auditor
from memory import EntityMemory
from questions import QUESTIONS

MEMORY_PATH = "knowledge.json"
LOG_DIR = "logs"
INR_RATE = 83.0


def _verdict_counts(report) -> dict[str, int]:
    counts = {"supported": 0, "unsupported": 0, "contradicted": 0,
              "no_citation": 0, "source_error": 0}
    for v in report.verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    return counts


def _memory_snapshot(memory: EntityMemory) -> dict:
    return {
        name: {
            "entity_type": rec.entity_type,
            "fact_count": len(rec.facts),
            "related": rec.related_entities,
        }
        for name, rec in memory.entities.items()
    }


def run_all(fresh_memory: bool = True):
    os.makedirs(LOG_DIR, exist_ok=True)

    if fresh_memory and os.path.exists(MEMORY_PATH):
        os.remove(MEMORY_PATH)

    memory = EntityMemory(path=MEMORY_PATH)

    per_question: list[dict] = []
    totals = {
        "analyst_input": 0, "analyst_output": 0,
        "auditor_input": 0, "auditor_output": 0,
        "cost_inr": 0.0, "latency_s": 0.0,
    }

    print("=" * 80)
    print("8-QUESTION EVALUATION RUNNER")
    print("=" * 80)
    print(f"Memory: {'fresh' if fresh_memory else 'preserved'} ({MEMORY_PATH})")
    print(f"Log dir: {LOG_DIR}/")
    print()

    for q_info in QUESTIONS:
        qid = q_info["id"]
        question = q_info["question"]
        difficulty = q_info["difficulty"]

        print(f"--- Q{qid} [{difficulty}] ---")
        print(f"  {question[:90]}...")

        entities_before = len(memory.entities)
        memory_snap_before = _memory_snapshot(memory)
        q_start = time.time()

        result: dict = {
            "question_number": qid,
            "question": question,
            "difficulty": difficulty,
            "introduces": q_info["introduces"],
            "reuses": q_info["reuses"],
            "status": "pending",
        }

        try:
            # --- Analyst ---
            analyst_start = time.time()
            answer = run_analyst(question, memory=memory)
            analyst_latency = time.time() - analyst_start

            result["analyst"] = answer.model_dump()
            result["analyst_model"] = answer.cost.model

            # --- Auditor ---
            auditor_start = time.time()
            report = run_auditor(answer)
            auditor_latency = time.time() - auditor_start

            result["audit"] = report.model_dump()
            result["auditor_model"] = report.cost.model

            # --- Metrics ---
            verdicts = _verdict_counts(report)
            a_cost_inr = answer.cost.cost_inr(INR_RATE)
            u_cost_inr = report.cost.cost_inr(INR_RATE)
            total_latency = time.time() - q_start

            metrics = {
                "analyst_input_tokens": answer.cost.input_tokens,
                "analyst_output_tokens": answer.cost.output_tokens,
                "analyst_cost_inr": a_cost_inr,
                "analyst_latency_s": round(analyst_latency, 1),
                "analyst_model": answer.cost.model,
                "auditor_input_tokens": report.cost.input_tokens,
                "auditor_output_tokens": report.cost.output_tokens,
                "auditor_cost_inr": u_cost_inr,
                "auditor_latency_s": round(auditor_latency, 1),
                "auditor_model": report.cost.model,
                "total_latency_s": round(total_latency, 1),
                "total_cost_inr": round(a_cost_inr + u_cost_inr, 4),
                "verdicts": verdicts,
                "memory_entities_before": entities_before,
                "memory_entities_after": len(memory.entities),
                "memory_used": any(
                    e.get("event") == "memory_recall"
                    for e in answer.tool_trace
                ),
            }
            result["metrics"] = metrics
            result["status"] = "success"

            # Update totals
            totals["analyst_input"] += answer.cost.input_tokens
            totals["analyst_output"] += answer.cost.output_tokens
            totals["auditor_input"] += report.cost.input_tokens
            totals["auditor_output"] += report.cost.output_tokens
            totals["cost_inr"] += a_cost_inr + u_cost_inr
            totals["latency_s"] += total_latency

            # Print one-liner
            v = verdicts
            mem_flag = "Yes" if metrics["memory_used"] else "No"
            a_tok = answer.cost.input_tokens + answer.cost.output_tokens
            u_tok = report.cost.input_tokens + report.cost.output_tokens
            print(
                f"  OK | A:{a_tok:,} tok | U:{u_tok:,} tok | "
                f"Rs.{metrics['total_cost_inr']:.2f} | "
                f"{metrics['total_latency_s']:.0f}s | "
                f"S:{v['supported']} U:{v['unsupported']} C:{v['contradicted']} "
                f"N:{v['no_citation']} E:{v['source_error']} | "
                f"Mem:{mem_flag}"
            )

        except Exception as exc:
            total_latency = time.time() - q_start
            result["status"] = "failed"
            result["error"] = str(exc)
            result["error_type"] = type(exc).__name__
            result["traceback"] = traceback.format_exc()
            totals["latency_s"] += total_latency

            per_q_metrics = {
                "total_latency_s": round(total_latency, 1),
                "memory_entities_before": entities_before,
                "memory_entities_after": len(memory.entities),
            }
            result["metrics"] = per_q_metrics

            print(f"  FAILED | {type(exc).__name__}: {str(exc)[:80]} | {total_latency:.0f}s")

        # Memory snapshot after
        result["memory_state"] = _memory_snapshot(memory)

        # Save per-question trace
        trace_path = os.path.join(LOG_DIR, f"q{qid}_trace.json")
        with open(trace_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"  Trace: {trace_path}")
        print()

        per_question.append({
            "q": qid,
            "status": result["status"],
            "difficulty": difficulty,
            "analyst_tokens": result.get("metrics", {}).get(
                "analyst_input_tokens", 0
            ) + result.get("metrics", {}).get("analyst_output_tokens", 0),
            "auditor_tokens": result.get("metrics", {}).get(
                "auditor_input_tokens", 0
            ) + result.get("metrics", {}).get("auditor_output_tokens", 0),
            "cost_inr": result.get("metrics", {}).get("total_cost_inr", 0),
            "latency_s": result.get("metrics", {}).get("total_latency_s", 0),
            "verdicts": result.get("metrics", {}).get("verdicts", {}),
            "memory_used": result.get("metrics", {}).get("memory_used", False),
            "analyst_model": result.get("analyst_model", ""),
            "auditor_model": result.get("auditor_model", ""),
        })

    # --- Aggregate summary ---
    print("=" * 80)
    print("AGGREGATE SUMMARY")
    print("=" * 80)

    header = (
        f"{'Q#':>3} | {'Diff':<11} | {'Status':<7} | "
        f"{'A.Tok':>7} | {'U.Tok':>7} | {'Cost':>9} | "
        f"{'Time':>6} | {'S':>2} {'U':>2} {'C':>2} {'N':>2} {'E':>2} | "
        f"{'Mem':>3} | Model"
    )
    print(header)
    print("-" * len(header))

    for pq in per_question:
        v = pq.get("verdicts", {})
        mem = "Yes" if pq.get("memory_used") else "No"
        model = pq.get("analyst_model", "")
        if len(model) > 20:
            model = model[:20] + "..."
        print(
            f"{pq['q']:>3} | {pq['difficulty']:<11} | {pq['status']:<7} | "
            f"{pq['analyst_tokens']:>7,} | {pq['auditor_tokens']:>7,} | "
            f"Rs.{pq['cost_inr']:>5.2f} | "
            f"{pq['latency_s']:>5.0f}s | "
            f"{v.get('supported',0):>2} {v.get('unsupported',0):>2} "
            f"{v.get('contradicted',0):>2} {v.get('no_citation',0):>2} "
            f"{v.get('source_error',0):>2} | "
            f"{mem:>3} | {model}"
        )

    succeeded = sum(1 for pq in per_question if pq["status"] == "success")
    failed = sum(1 for pq in per_question if pq["status"] == "failed")
    mem_reuse_qs = [pq["q"] for pq in per_question if pq.get("memory_used")]

    print()
    print(f"  Questions: {succeeded} succeeded, {failed} failed")
    print(f"  Total analyst tokens: {totals['analyst_input']:,} in + {totals['analyst_output']:,} out")
    print(f"  Total auditor tokens: {totals['auditor_input']:,} in + {totals['auditor_output']:,} out")
    print(f"  Total cost: Rs.{totals['cost_inr']:.2f}")
    print(f"  Total latency: {totals['latency_s']:.0f}s")
    print(f"  Final memory entities: {len(memory.entities)}")
    print(f"  Memory reuse in questions: {mem_reuse_qs or 'none'}")

    # Save aggregate
    summary = {
        "total_questions": len(QUESTIONS),
        "succeeded": succeeded,
        "failed": failed,
        "total_analyst_tokens": {
            "input": totals["analyst_input"],
            "output": totals["analyst_output"],
        },
        "total_auditor_tokens": {
            "input": totals["auditor_input"],
            "output": totals["auditor_output"],
        },
        "total_cost_inr": round(totals["cost_inr"], 4),
        "total_latency_s": round(totals["latency_s"], 1),
        "memory_entities_final": len(memory.entities),
        "memory_reuse_questions": mem_reuse_qs,
        "per_question": per_question,
    }
    summary_path = os.path.join(LOG_DIR, "runner_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n  Summary saved to {summary_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="8-question evaluation runner")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--fresh-memory", action="store_true", default=True,
        help="Start with clean memory (default)",
    )
    group.add_argument(
        "--preserve-memory", action="store_true",
        help="Keep existing knowledge.json instead of starting fresh",
    )
    args = parser.parse_args()
    run_all(fresh_memory=not args.preserve_memory)
