"""Standalone Groq benchmark -- runs the Analyst flow against Groq's API.
Does NOT modify any production code or configuration."""
from __future__ import annotations
import json
import os
import sys
import time
import traceback
from dotenv import load_dotenv
from openai import OpenAI

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tools import web_search, fetch_page, TOOL_DEFINITIONS
from analyst import (
    SYSTEM_PROMPT, PLANNING_PROMPT, _parse_answer, _track_cost,
    _execute_tool_call,
)
from models import CostRecord

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "qwen/qwen3.8-27b"

QUESTION = (
    "Who is the current CEO of Titan Company, when did they take over "
    "the role, and what was their previous position before becoming CEO?"
)

MAX_ROUNDS = 15

# Groq free-tier pricing (free for now)
GROQ_PRICING = {"input": 0, "output": 0}


def main():
    print("=" * 70)
    print(f"GROQ BENCHMARK -- {GROQ_MODEL}")
    print("=" * 70)
    print(f"\nQuestion: {QUESTION}\n")

    client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)
    tools = [t for t in TOOL_DEFINITIONS if t["function"]["name"] != "memory_lookup"]
    trace = []
    total_cost = CostRecord(model=GROQ_MODEL)
    start_time = time.time()

    search_count = 0
    fetch_count = 0

    # --- Step 1: Planning ---
    print("Generating plan...")
    plan_start = time.time()
    try:
        plan_resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": PLANNING_PROMPT.format(
                question=QUESTION, memory_section="",
            )}],
            temperature=0.2,
        )
    except Exception:
        print("PLANNING FAILED:")
        traceback.print_exc()
        return
    plan_time = time.time() - plan_start

    plan_text = plan_resp.choices[0].message.content or ""
    if plan_resp.usage:
        rc = _track_cost(plan_resp.usage, GROQ_MODEL)
        total_cost.input_tokens += rc.input_tokens
        total_cost.output_tokens += rc.output_tokens

    trace.append({
        "round": 0, "event": "plan", "model": GROQ_MODEL,
        "plan": plan_text, "latency_s": plan_time,
        "timestamp": time.time() - start_time,
    })

    print(f"\nPlan (latency: {plan_time:.1f}s):\n{plan_text}\n")

    # --- Step 2: Research loop ---
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Research question: {QUESTION}\n\n"
            f"Your research plan:\n{plan_text}\n\n"
            "Now execute the plan using the available tools. "
            "When you have enough evidence, give your final answer."
        )},
    ]

    for round_num in range(1, MAX_ROUNDS + 1):
        round_start = time.time()
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                tools=tools,
                temperature=0.2,
            )
        except Exception as exc:
            print(f"\nRound {round_num} FAILED: {exc}")
            traceback.print_exc()
            trace.append({
                "round": round_num, "event": "error",
                "error": str(exc), "timestamp": time.time() - start_time,
            })
            break
        round_time = time.time() - round_start

        if response.usage:
            rc = _track_cost(response.usage, GROQ_MODEL)
            total_cost.input_tokens += rc.input_tokens
            total_cost.output_tokens += rc.output_tokens

        msg = response.choices[0].message

        if msg.tool_calls:
            messages.append(msg)
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments)

                if fn_name == "web_search":
                    search_count += 1
                elif fn_name == "fetch_page":
                    fetch_count += 1

                print(f"  [Round {round_num}] {fn_name}({json.dumps(fn_args)})  latency={round_time:.1f}s")

                result_str = _execute_tool_call(fn_name, fn_args)
                trace.append({
                    "round": round_num, "tool": fn_name,
                    "arguments": fn_args, "model": GROQ_MODEL,
                    "latency_s": round_time,
                    "result_preview": result_str[:500],
                    "timestamp": time.time() - start_time,
                })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

        elif msg.content:
            total_time = time.time() - start_time
            trace.append({
                "round": round_num, "event": "final_answer",
                "model": GROQ_MODEL, "latency_s": round_time,
                "timestamp": total_time,
            })

            answer_text = msg.content
            claims, sources, summary = _parse_answer(answer_text)

            # --- Report ---
            print(f"\n{'=' * 70}")
            print("RESULTS")
            print(f"{'=' * 70}")

            print(f"\nFinal Answer:\n{answer_text[:800]}\n")

            print(f"Claims ({len(claims)}):")
            for i, c in enumerate(claims, 1):
                cite = c.citation or "NO CITATION"
                print(f"  {i}. {c.text[:80]}")
                print(f"     Citation: {cite}")

            print(f"\nSources ({len(sources)}):")
            for s in sources:
                print(f"  - {s}")

            print(f"\n{'=' * 70}")
            print("METRICS")
            print(f"{'=' * 70}")
            print(f"  Model:          {GROQ_MODEL}")
            print(f"  Web searches:   {search_count}")
            print(f"  Page fetches:   {fetch_count}")
            print(f"  Total rounds:   {round_num}")
            print(f"  Input tokens:   {total_cost.input_tokens:,}")
            print(f"  Output tokens:  {total_cost.output_tokens:,}")
            print(f"  Total latency:  {total_time:.1f}s")
            print(f"  Cost (USD):     $0.0000 (Groq free tier)")

            cited_claims = sum(1 for c in claims if c.citation)
            print(f"\n  Claims cited:   {cited_claims}/{len(claims)}")
            print(f"  Unique sources: {len(sources)}")
            cross_check = "YES" if search_count >= 2 or fetch_count >= 1 else "NO"
            print(f"  Cross-checked:  {cross_check}")

            # Save trace
            os.makedirs("logs", exist_ok=True)
            trace_out = {
                "model": GROQ_MODEL,
                "question": QUESTION,
                "plan": plan_text,
                "answer": answer_text,
                "claims": [c.model_dump() for c in claims],
                "sources": sources,
                "trace": trace,
                "metrics": {
                    "searches": search_count,
                    "fetches": fetch_count,
                    "rounds": round_num,
                    "input_tokens": total_cost.input_tokens,
                    "output_tokens": total_cost.output_tokens,
                    "total_latency_s": total_time,
                },
            }
            with open("logs/groq_benchmark.json", "w", encoding="utf-8") as f:
                json.dump(trace_out, f, indent=2, default=str)
            print(f"\nTrace saved to logs/groq_benchmark.json")
            return

        else:
            trace.append({"round": round_num, "event": "empty_response"})
            print(f"  [Round {round_num}] Empty response")
            break

    print("\nReached max rounds without final answer.")


if __name__ == "__main__":
    main()
