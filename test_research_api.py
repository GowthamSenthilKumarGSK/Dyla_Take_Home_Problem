"""Tests for research_api wrapper functions."""
from __future__ import annotations
import json
import os
import tempfile
import unittest

from research_api import (
    extract_trace_timeline,
    extract_sources,
    extract_provider_info,
    load_evaluation_summary,
    load_question_trace,
    _memory_snapshot,
)
from memory import EntityMemory
from models import Fact


class TestExtractTraceTimeline(unittest.TestCase):

    def test_plan_event(self):
        trace = [{"event": "plan", "round": 0, "timestamp": 1.0,
                  "model": "nemotron", "plan": "find MD of Titan"}]
        result = extract_trace_timeline(trace)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["event"], "plan")
        self.assertEqual(result[0]["label"], "Research plan created")
        self.assertIn("find MD", result[0]["detail"])

    def test_search_event(self):
        trace = [{"event": "search", "round": 1, "timestamp": 2.0,
                  "query": "Titan MD", "result_count": 5,
                  "urls": ["https://example.com"]}]
        result = extract_trace_timeline(trace)
        self.assertEqual(result[0]["event"], "search")
        self.assertIn("Titan MD", result[0]["label"])
        self.assertEqual(result[0]["result_count"], 5)

    def test_evidence_evaluation(self):
        trace = [{"event": "evidence_evaluation", "round": 1,
                  "timestamp": 5.0, "sufficient": True,
                  "conflicts": "none", "missing": "nothing critical"}]
        result = extract_trace_timeline(trace)
        self.assertIn("Sufficient", result[0]["label"])

    def test_insufficient_evaluation(self):
        trace = [{"event": "evidence_evaluation", "round": 1,
                  "timestamp": 5.0, "sufficient": False,
                  "conflicts": "none", "missing": "revenue data",
                  "follow_up_query": "Titan revenue FY2025"}]
        result = extract_trace_timeline(trace)
        self.assertIn("Insufficient", result[0]["label"])
        self.assertEqual(result[0]["follow_up"], "Titan revenue FY2025")

    def test_fallback_event(self):
        trace = [{"event": "model_fallback", "round": 0,
                  "timestamp": 3.0, "from_model": "nemotron",
                  "to_model": "gemma", "reason": "429"}]
        result = extract_trace_timeline(trace)
        self.assertIn("Fallback", result[0]["label"])

    def test_empty_trace(self):
        self.assertEqual(extract_trace_timeline([]), [])

    def test_full_pipeline_sequence(self):
        trace = [
            {"event": "plan", "round": 0, "timestamp": 1.0},
            {"event": "parsed_queries", "round": 0, "timestamp": 1.1,
             "queries": ["q1", "q2"]},
            {"event": "search", "round": 1, "timestamp": 2.0,
             "query": "q1", "result_count": 3, "urls": []},
            {"event": "source_selection", "round": 1, "timestamp": 2.5,
             "selected_urls": ["u1"], "total_candidates": 3},
            {"event": "fetch_page", "round": 1, "timestamp": 3.0,
             "url": "u1", "title": "Page1", "text_length": 2000},
            {"event": "evidence_evaluation", "round": 1, "timestamp": 4.0,
             "sufficient": True},
            {"event": "research_complete", "round": 1, "timestamp": 4.5,
             "reason": "sufficient", "total_evidence_pieces": 1},
            {"event": "final_answer", "round": 1, "timestamp": 5.0},
        ]
        result = extract_trace_timeline(trace)
        self.assertEqual(len(result), 8)
        events = [r["event"] for r in result]
        self.assertEqual(events, [
            "plan", "parsed_queries", "search", "source_selection",
            "fetch_page", "evidence_evaluation", "research_complete",
            "final_answer",
        ])


class TestExtractSources(unittest.TestCase):

    def test_fetched_sources_come_first(self):
        trace = [
            {"event": "search", "urls": ["https://a.com", "https://b.com"]},
            {"event": "source_selection", "selected_urls": ["https://a.com"]},
            {"event": "fetch_page", "url": "https://a.com", "title": "A"},
        ]
        sources = extract_sources(trace)
        self.assertTrue(sources[0]["was_fetched"])
        self.assertEqual(sources[0]["url"], "https://a.com")
        self.assertFalse(sources[1]["was_fetched"])

    def test_search_only_sources(self):
        trace = [
            {"event": "search", "urls": ["https://x.com", "https://y.com"]},
        ]
        sources = extract_sources(trace)
        self.assertEqual(len(sources), 2)
        for s in sources:
            self.assertTrue(s["was_search_result"])
            self.assertFalse(s["was_fetched"])

    def test_empty_trace(self):
        self.assertEqual(extract_sources([]), [])


class TestExtractProviderInfo(unittest.TestCase):

    def test_no_fallback(self):
        trace = [
            {"event": "plan", "model": "nemotron"},
            {"event": "final_answer", "model": "nemotron"},
        ]
        info = extract_provider_info(trace)
        self.assertFalse(info["had_fallback"])
        self.assertEqual(info["primary"], "nemotron")
        self.assertEqual(info["final"], "nemotron")

    def test_with_fallback(self):
        trace = [
            {"event": "plan", "model": "nemotron"},
            {"event": "model_fallback", "from_model": "nemotron",
             "to_model": "gemma", "reason": "429"},
            {"event": "final_answer", "model": "gemma"},
        ]
        info = extract_provider_info(trace)
        self.assertTrue(info["had_fallback"])
        self.assertEqual(info["final"], "gemma")
        self.assertEqual(len(info["fallbacks"]), 1)


class TestMemorySnapshot(unittest.TestCase):

    def test_snapshot_structure(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        tmp.write("{}")
        tmp.close()
        try:
            mem = EntityMemory(path=tmp.name)
            mem.add_facts("Titan Company",
                          [Fact(text="MD is X", source="https://s.com")],
                          entity_type="company")
            snap = _memory_snapshot(mem)
            self.assertIn("titan_company", snap)
            self.assertEqual(snap["titan_company"]["entity_type"], "company")
            self.assertEqual(len(snap["titan_company"]["facts"]), 1)
            self.assertEqual(snap["titan_company"]["facts"][0]["text"], "MD is X")
        finally:
            os.unlink(tmp.name)


class TestLoadFunctions(unittest.TestCase):

    def test_load_missing_summary(self):
        import research_api
        old = research_api.LOG_DIR
        research_api.LOG_DIR = "/nonexistent"
        try:
            self.assertIsNone(load_evaluation_summary())
        finally:
            research_api.LOG_DIR = old

    def test_load_missing_trace(self):
        import research_api
        old = research_api.LOG_DIR
        research_api.LOG_DIR = "/nonexistent"
        try:
            self.assertIsNone(load_question_trace(99))
        finally:
            research_api.LOG_DIR = old


if __name__ == "__main__":
    unittest.main()
