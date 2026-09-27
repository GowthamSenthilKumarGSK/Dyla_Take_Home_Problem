"""Thin wrapper around Analyst + Auditor for the frontend UI."""
from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, field

from analyst import run_analyst
from auditor import run_auditor
from memory import EntityMemory
from models import AnalystAnswer, AuditReport

MEMORY_PATH = "knowledge.json"
LOG_DIR = "logs"
INR_RATE = 83.0


@dataclass
class ResearchResult:
    question: str
    answer: AnalystAnswer
    audit: AuditReport
    memory_before: dict
    memory_after: dict
    analyst_latency: float
    auditor_latency: float
    total_latency: float
    error: str | None = None


def _memory_snapshot(memory: EntityMemory) -> dict:
    return {
        name: {
            "entity_type": rec.entity_type,
            "facts": [{"text": f.text, "source": f.source} for f in rec.facts],
            "related": rec.related_entities,
        }
        for name, rec in memory.entities.items()
    }


def research(question: str, fresh_memory: bool = False) -> ResearchResult:
    """Run Analyst + Auditor on a single question and return structured results."""
    if fresh_memory and os.path.exists(MEMORY_PATH):
        os.remove(MEMORY_PATH)

    memory = EntityMemory(path=MEMORY_PATH)
    memory_before = _memory_snapshot(memory)

    t0 = time.time()
    answer = run_analyst(question, memory=memory)
    analyst_latency = time.time() - t0

    t1 = time.time()
    audit = run_auditor(answer)
    auditor_latency = time.time() - t1

    total_latency = time.time() - t0
    memory_after = _memory_snapshot(memory)

    return ResearchResult(
        question=question,
        answer=answer,
        audit=audit,
        memory_before=memory_before,
        memory_after=memory_after,
        analyst_latency=analyst_latency,
        auditor_latency=auditor_latency,
        total_latency=total_latency,
    )


def extract_trace_timeline(trace: list[dict]) -> list[dict]:
    """Convert raw tool_trace into a simplified timeline for the UI."""
    timeline = []
    for ev in trace:
        event_type = ev.get("event", ev.get("tool", "unknown"))
        entry = {
            "event": event_type,
            "round": ev.get("round", 0),
            "timestamp": ev.get("timestamp", 0),
        }

        if event_type == "plan":
            entry["detail"] = ev.get("plan", "")[:500]
            entry["model"] = ev.get("model", "")
            entry["label"] = "Research plan created"

        elif event_type == "parsed_queries":
            entry["queries"] = ev.get("queries", [])
            entry["label"] = f"Parsed {len(entry['queries'])} search queries"

        elif event_type == "search":
            entry["query"] = ev.get("query", "")
            entry["result_count"] = ev.get("result_count", 0)
            entry["urls"] = ev.get("urls", [])
            entry["label"] = f"Web search: {ev.get('query', '')[:60]}"

        elif event_type == "source_selection":
            entry["selected"] = ev.get("selected_urls", [])
            entry["total_candidates"] = ev.get("total_candidates", 0)
            entry["already_fetched"] = ev.get("already_fetched", 0)
            entry["label"] = f"Selected {len(entry['selected'])} sources"

        elif event_type == "fetch_page":
            entry["url"] = ev.get("url", "")
            entry["title"] = ev.get("title", "")
            entry["text_length"] = ev.get("text_length", 0)
            entry["error"] = ev.get("error")
            entry["label"] = f"Fetched: {ev.get('title', ev.get('url', ''))[:50]}"

        elif event_type == "evidence_evaluation":
            entry["sufficient"] = ev.get("sufficient", False)
            entry["conflicts"] = ev.get("conflicts", "none")
            entry["missing"] = ev.get("missing", "")
            entry["follow_up"] = ev.get("follow_up_query", "")
            entry["model"] = ev.get("model", "")
            suff = "Sufficient" if entry["sufficient"] else "Insufficient"
            entry["label"] = f"Evidence evaluated: {suff}"

        elif event_type == "follow_up_search":
            entry["query"] = ev.get("query", "")
            entry["label"] = f"Follow-up search: {ev.get('query', '')[:60]}"

        elif event_type == "research_complete":
            entry["reason"] = ev.get("reason", "")
            entry["total_evidence"] = ev.get("total_evidence_pieces", 0)
            entry["label"] = f"Research complete ({ev.get('reason', '')})"

        elif event_type == "final_answer":
            entry["model"] = ev.get("model", "")
            entry["label"] = "Final answer generated"

        elif event_type == "memory_recall":
            entry["entities"] = ev.get("entities_found", [])
            entry["label"] = f"Memory recall: {', '.join(ev.get('entities_found', []))}"

        elif event_type == "model_fallback":
            entry["from_model"] = ev.get("from_model", "")
            entry["to_model"] = ev.get("to_model", "")
            entry["reason"] = ev.get("reason", "")[:200]
            entry["label"] = f"Fallback: {ev.get('from_model', '')[:20]} → {ev.get('to_model', '')[:20]}"

        elif event_type == "ollama_fallback":
            entry["from_model"] = ev.get("from_model", "")
            entry["to_model"] = ev.get("to_model", "")
            entry["label"] = f"Local fallback: {ev.get('to_model', '')}"

        elif event_type == "degraded_answer":
            entry["reason"] = ev.get("reason", "")
            entry["label"] = f"Degraded answer: {ev.get('reason', '')[:80]}"

        elif event_type == "conflicts_detected":
            entry["conflicts"] = ev.get("conflicts", "")
            entry["label"] = "Conflicts detected between sources"

        else:
            entry["label"] = event_type.replace("_", " ").title()

        timeline.append(entry)

    return timeline


def extract_sources(trace: list[dict]) -> list[dict]:
    """Extract source information from trace for the sources panel."""
    search_results: dict[str, dict] = {}
    fetched_urls: set[str] = set()
    selected_urls: set[str] = set()

    for ev in trace:
        event_type = ev.get("event", "")

        if event_type == "search":
            for url in ev.get("urls", []):
                if url not in search_results:
                    search_results[url] = {
                        "url": url,
                        "query": ev.get("query", ""),
                        "was_search_result": True,
                        "was_selected": False,
                        "was_fetched": False,
                        "title": "",
                        "score": None,
                    }

        elif event_type == "source_selection":
            for url in ev.get("selected_urls", []):
                selected_urls.add(url)
                if url in search_results:
                    search_results[url]["was_selected"] = True
                for scored in ev.get("scored_candidates", []):
                    if scored.get("url") == url:
                        search_results[url]["score"] = scored.get("adjusted_score")

        elif event_type == "fetch_page":
            url = ev.get("url", "")
            fetched_urls.add(url)
            if url in search_results:
                search_results[url]["was_fetched"] = True
                search_results[url]["title"] = ev.get("title", "")
            else:
                search_results[url] = {
                    "url": url,
                    "query": "",
                    "was_search_result": False,
                    "was_selected": True,
                    "was_fetched": True,
                    "title": ev.get("title", ""),
                    "score": None,
                }

    sources = sorted(
        search_results.values(),
        key=lambda s: (not s["was_fetched"], not s["was_selected"], -(s.get("score") or 0)),
    )
    return sources


def extract_provider_info(trace: list[dict]) -> dict:
    """Extract which providers/models were used and any fallbacks."""
    models_used: list[str] = []
    fallbacks: list[dict] = []

    for ev in trace:
        model = ev.get("model", "")
        if model and model not in models_used:
            models_used.append(model)

        if ev.get("event") == "model_fallback":
            fallbacks.append({
                "from": ev.get("from_model", ""),
                "to": ev.get("to_model", ""),
                "reason": ev.get("reason", "")[:100],
            })
        elif ev.get("event") == "ollama_fallback":
            fallbacks.append({
                "from": ev.get("from_model", ""),
                "to": ev.get("to_model", ""),
                "reason": ev.get("reason", "")[:100],
            })

    primary = models_used[0] if models_used else "unknown"
    final = models_used[-1] if models_used else "unknown"

    return {
        "primary": primary,
        "final": final,
        "all_models": models_used,
        "fallbacks": fallbacks,
        "had_fallback": len(fallbacks) > 0,
    }


def load_evaluation_summary() -> dict | None:
    """Load runner_summary.json if it exists."""
    path = os.path.join(LOG_DIR, "runner_summary.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_question_trace(qid: int) -> dict | None:
    """Load a single question trace from logs/."""
    path = os.path.join(LOG_DIR, f"q{qid}_trace.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
