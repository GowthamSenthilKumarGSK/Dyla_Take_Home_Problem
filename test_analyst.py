"""Run the analyst on one question and print the full trace."""
import sys
import io
import json

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from analyst import run_analyst


QUESTION = (
    "Who is the current CEO of Titan Company, when did they take over the role, "
    "and what was their previous position before becoming CEO?"
)


def main():
    print("=" * 70)
    print("ANALYST TEST — Single Question")
    print("=" * 70)
    print(f"\nQuestion: {QUESTION}\n")
    print("Running analyst...\n")

    try:
        answer = run_analyst(QUESTION)
    except RuntimeError as e:
        print(f"ABORTED: {e}")
        return
    except Exception as e:
        print(f"UNEXPECTED ERROR: {type(e).__name__}: {e}")
        raise

    # --- Trace ---
    print("=" * 70)
    print("TOOL TRACE")
    print("=" * 70)
    for entry in answer.tool_trace:
        if entry.get("event") == "plan":
            print(f"\n[Round {entry['round']}] PLAN  (t={entry['timestamp']:.1f}s)  model={entry.get('model', '?')}")
            print(f"  {entry.get('plan', '(empty)')}")
        elif entry.get("event") == "model_fallback":
            print(f"\n[Round {entry['round']}] FALLBACK  (t={entry['timestamp']:.1f}s)")
            print(f"  {entry['from_model']} -> {entry['to_model']}")
            reason = entry.get("reason", "")
            if len(reason) > 200:
                reason = reason[:200] + "..."
            print(f"  Reason: {reason}")
        elif "tool" in entry:
            print(f"\n[Round {entry['round']}] {entry['tool']}  (t={entry['timestamp']:.1f}s)  model={entry.get('model', '?')}")
            print(f"  Args: {json.dumps(entry['arguments'], ensure_ascii=False)}")
            preview = entry.get("result_preview", "")
            if len(preview) > 400:
                preview = preview[:400] + "..."
            print(f"  Result: {preview}")
        else:
            print(f"\n[Round {entry['round']}] {entry.get('event', '?')}  (t={entry['timestamp']:.1f}s)  model={entry.get('model', '?')}")

    # --- Plan ---
    print("\n" + "=" * 70)
    print("PLAN")
    print("=" * 70)
    print(answer.plan if answer.plan else "(no explicit plan captured)")

    # --- Answer ---
    print("\n" + "=" * 70)
    print("FINAL ANSWER")
    print("=" * 70)
    print(answer.summary)

    # --- Claims ---
    print("\n" + "=" * 70)
    print(f"CLAIMS ({len(answer.claims)})")
    print("=" * 70)
    for i, c in enumerate(answer.claims, 1):
        cited = c.citation or "NO CITATION"
        print(f"  {i}. {c.text}")
        print(f"     Citation: {cited}")

    # --- Sources ---
    print("\n" + "=" * 70)
    print(f"SOURCES ({len(answer.sources_used)})")
    print("=" * 70)
    for s in answer.sources_used:
        print(f"  - {s}")

    # --- Cost ---
    print("\n" + "=" * 70)
    print("COST")
    print("=" * 70)
    print(f"  Model: {answer.cost.model}")
    print(f"  Input tokens:  {answer.cost.input_tokens:,}")
    print(f"  Output tokens: {answer.cost.output_tokens:,}")
    print(f"  Cost (USD):    ${answer.cost.cost_usd:.4f}")
    print(f"  Cost (INR):    ₹{answer.cost.cost_inr():.2f}")

    # --- Save full trace to file ---
    trace_path = "logs/test_analyst_trace.json"
    with open(trace_path, "w", encoding="utf-8") as f:
        json.dump(answer.model_dump(), f, indent=2, ensure_ascii=False, default=str)
    print(f"\nFull trace saved to {trace_path}")


if __name__ == "__main__":
    main()
