"""Tests for the 8-question evaluation runner."""
from __future__ import annotations
import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from models import Claim, CostRecord, AnalystAnswer, AuditReport, AuditVerdict
from questions import QUESTIONS
from runner import _verdict_counts, _memory_snapshot, run_all


class TestQuestions(unittest.TestCase):

    def test_exactly_8_questions(self):
        self.assertEqual(len(QUESTIONS), 8)

    def test_ids_sequential(self):
        ids = [q["id"] for q in QUESTIONS]
        self.assertEqual(ids, list(range(1, 9)))

    def test_all_have_required_fields(self):
        for q in QUESTIONS:
            self.assertIn("id", q)
            self.assertIn("question", q)
            self.assertIn("introduces", q)
            self.assertIn("reuses", q)
            self.assertIn("difficulty", q)

    def test_entity_reuse_exists(self):
        reuse_qs = [q["id"] for q in QUESTIONS if q["reuses"]]
        self.assertGreaterEqual(len(reuse_qs), 2)

    def test_difficulty_progression(self):
        levels = ["easy", "easy-medium", "medium", "medium", "medium-hard",
                   "medium-hard", "hard", "hard"]
        actual = [q["difficulty"] for q in QUESTIONS]
        self.assertEqual(actual, levels)


class TestVerdictCounts(unittest.TestCase):

    def test_counts(self):
        report = AuditReport(
            analyst_question="test",
            verdicts=[
                AuditVerdict(claim=Claim(text="a"), verdict="supported"),
                AuditVerdict(claim=Claim(text="b"), verdict="supported"),
                AuditVerdict(claim=Claim(text="c"), verdict="no_citation"),
                AuditVerdict(claim=Claim(text="d"), verdict="contradicted"),
            ],
        )
        counts = _verdict_counts(report)
        self.assertEqual(counts["supported"], 2)
        self.assertEqual(counts["no_citation"], 1)
        self.assertEqual(counts["contradicted"], 1)
        self.assertEqual(counts["unsupported"], 0)


class TestMemorySnapshot(unittest.TestCase):

    def test_snapshot_structure(self):
        from memory import EntityMemory
        from models import Fact
        tmpf = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        tmpf.write("{}")
        tmpf.close()
        try:
            mem = EntityMemory(path=tmpf.name)
            mem.add_facts("Titan Company", [
                Fact(text="MD is Ajoy Chawla", source="https://example.com"),
            ], entity_type="company")

            snap = _memory_snapshot(mem)
            self.assertIn("titan_company", snap)
            self.assertEqual(snap["titan_company"]["fact_count"], 1)
            self.assertEqual(snap["titan_company"]["entity_type"], "company")
        finally:
            os.unlink(tmpf.name)


class TestRunAllErrorHandling(unittest.TestCase):

    @patch("runner.QUESTIONS", [
        {"id": 1, "question": "Test Q1?", "introduces": [], "reuses": [],
         "difficulty": "easy"},
        {"id": 2, "question": "Test Q2?", "introduces": [], "reuses": [],
         "difficulty": "medium"},
    ])
    @patch("runner.run_auditor")
    @patch("runner.run_analyst")
    def test_failed_question_does_not_stop_runner(self, mock_analyst, mock_auditor):
        """If Q1 fails, Q2 should still run."""
        mock_analyst.side_effect = [
            RuntimeError("All providers failed"),
            AnalystAnswer(
                question="Test Q2?",
                claims=[Claim(text="Fact.")],
                cost=CostRecord(model="test", input_tokens=100, output_tokens=20),
            ),
        ]
        mock_auditor.return_value = AuditReport(
            analyst_question="Test Q2?",
            verdicts=[AuditVerdict(claim=Claim(text="Fact."), verdict="no_citation")],
            cost=CostRecord(model="none"),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mem_path = os.path.join(tmpdir, "test_knowledge.json")
            log_dir = os.path.join(tmpdir, "logs")

            with patch("runner.MEMORY_PATH", mem_path), \
                 patch("runner.LOG_DIR", log_dir):
                run_all(fresh_memory=True)

            # Both trace files should exist
            self.assertTrue(os.path.exists(os.path.join(log_dir, "q1_trace.json")))
            self.assertTrue(os.path.exists(os.path.join(log_dir, "q2_trace.json")))

            # Q1 should be marked failed
            with open(os.path.join(log_dir, "q1_trace.json")) as f:
                q1 = json.load(f)
            self.assertEqual(q1["status"], "failed")
            self.assertIn("All providers failed", q1["error"])

            # Q2 should be marked success
            with open(os.path.join(log_dir, "q2_trace.json")) as f:
                q2 = json.load(f)
            self.assertEqual(q2["status"], "success")

            # Summary should exist
            self.assertTrue(os.path.exists(os.path.join(log_dir, "runner_summary.json")))
            with open(os.path.join(log_dir, "runner_summary.json")) as f:
                summary = json.load(f)
            self.assertEqual(summary["succeeded"], 1)
            self.assertEqual(summary["failed"], 1)


class TestRunAllSuccess(unittest.TestCase):

    @patch("runner.QUESTIONS", [
        {"id": 1, "question": "Who leads Titan Company?",
         "introduces": ["Titan Company"], "reuses": [], "difficulty": "easy"},
    ])
    @patch("runner.run_auditor")
    @patch("runner.run_analyst")
    def test_successful_run_saves_traces(self, mock_analyst, mock_auditor):
        mock_analyst.return_value = AnalystAnswer(
            question="Who leads Titan Company?",
            claims=[
                Claim(text="Ajoy Chawla is MD.", citation="https://example.com"),
            ],
            sources_used=["https://example.com"],
            tool_trace=[],
            cost=CostRecord(model="test-model", input_tokens=500, output_tokens=100),
        )
        mock_auditor.return_value = AuditReport(
            analyst_question="Who leads Titan Company?",
            verdicts=[
                AuditVerdict(
                    claim=Claim(text="Ajoy Chawla is MD.", citation="https://example.com"),
                    verdict="supported",
                    evidence="Confirmed.",
                ),
            ],
            cost=CostRecord(model="test-model", input_tokens=200, output_tokens=30),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mem_path = os.path.join(tmpdir, "test_knowledge.json")
            log_dir = os.path.join(tmpdir, "logs")

            with patch("runner.MEMORY_PATH", mem_path), \
                 patch("runner.LOG_DIR", log_dir):
                run_all(fresh_memory=True)

            # Trace file
            trace_path = os.path.join(log_dir, "q1_trace.json")
            self.assertTrue(os.path.exists(trace_path))
            with open(trace_path) as f:
                trace = json.load(f)
            self.assertEqual(trace["status"], "success")
            self.assertEqual(trace["metrics"]["analyst_input_tokens"], 500)
            self.assertEqual(trace["metrics"]["auditor_input_tokens"], 200)
            self.assertEqual(trace["metrics"]["verdicts"]["supported"], 1)
            self.assertEqual(trace["analyst_model"], "test-model")

            # Summary
            with open(os.path.join(log_dir, "runner_summary.json")) as f:
                summary = json.load(f)
            self.assertEqual(summary["succeeded"], 1)
            self.assertEqual(summary["failed"], 0)
            self.assertEqual(summary["total_analyst_tokens"]["input"], 500)


class TestFreshMemoryOption(unittest.TestCase):

    @patch("runner.QUESTIONS", [])
    def test_fresh_memory_removes_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem_path = os.path.join(tmpdir, "knowledge.json")
            log_dir = os.path.join(tmpdir, "logs")

            # Create a pre-existing knowledge file
            with open(mem_path, "w") as f:
                json.dump({"entities": {"old": {"name": "old", "facts": []}}, "sources": {}}, f)

            with patch("runner.MEMORY_PATH", mem_path), \
                 patch("runner.LOG_DIR", log_dir):
                run_all(fresh_memory=True)

            # Old file should be deleted (with 0 questions, nothing recreates it)
            if os.path.exists(mem_path):
                with open(mem_path) as f:
                    data = json.load(f)
                self.assertNotIn("old", data.get("entities", {}))

    @patch("runner.QUESTIONS", [])
    def test_preserve_memory_keeps_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem_path = os.path.join(tmpdir, "knowledge.json")
            log_dir = os.path.join(tmpdir, "logs")

            with open(mem_path, "w") as f:
                json.dump({
                    "entities": {"old_entity": {
                        "name": "Old Entity", "entity_type": "test",
                        "facts": [], "related_entities": [],
                        "last_updated": "2024-01-01",
                    }},
                    "sources": {},
                }, f)

            with patch("runner.MEMORY_PATH", mem_path), \
                 patch("runner.LOG_DIR", log_dir):
                run_all(fresh_memory=False)

            with open(mem_path) as f:
                data = json.load(f)
            self.assertIn("old_entity", data.get("entities", {}))


if __name__ == "__main__":
    unittest.main()
