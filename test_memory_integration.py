"""Tests for EntityMemory integration with the Analyst."""
from __future__ import annotations
import os
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from models import Claim, CostRecord, AnalystAnswer, Fact
from memory import EntityMemory
from analyst import (
    _extract_entities, _store_entities, _execute_tool_call, run_analyst,
    _is_substantive, _is_degraded_answer,
)


class TestExtractEntities(unittest.TestCase):

    def test_multi_word_names(self):
        entities = _extract_entities("Who is the CEO of Titan Company?")
        self.assertIn("Titan Company", entities)

    def test_person_names(self):
        entities = _extract_entities("Ajoy Chawla became the Managing Director.")
        self.assertIn("Ajoy Chawla", entities)
        self.assertIn("Managing Director", entities)

    def test_initialed_names(self):
        entities = _extract_entities("C.K. Venkataraman retired from Titan.")
        found = any("Venkataraman" in e for e in entities)
        self.assertTrue(found, f"Expected Venkataraman in {entities}")

    def test_no_entities(self):
        entities = _extract_entities("what is 2 + 2?")
        self.assertEqual(entities, [])

    def test_deduplication(self):
        entities = _extract_entities("Titan Company is great. Titan Company is big.")
        self.assertEqual(entities.count("Titan Company"), 1)


class TestStoreEntities(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w",
        )
        self.tmpfile.write("{}")
        self.tmpfile.close()
        self.memory = EntityMemory(path=self.tmpfile.name)

    def tearDown(self):
        os.unlink(self.tmpfile.name)

    def test_stores_cited_claims_only(self):
        answer = AnalystAnswer(
            question="test",
            claims=[
                Claim(text="Ajoy Chawla is MD of Titan Company.",
                      citation="https://example.com/article"),
                Claim(text="He joined in 1995.", citation=None),
            ],
            sources_used=["https://example.com/article"],
            cost=CostRecord(model="test"),
        )
        _store_entities(answer, self.memory)

        record = self.memory.get("Ajoy Chawla")
        self.assertIsNotNone(record)
        self.assertEqual(len(record.facts), 1)
        self.assertEqual(record.facts[0].source, "https://example.com/article")

    def test_uncited_claims_not_stored(self):
        answer = AnalystAnswer(
            question="test",
            claims=[
                Claim(text="Some uncited fact about Titan Company.", citation=None),
            ],
            cost=CostRecord(model="test"),
        )
        _store_entities(answer, self.memory)
        self.assertIsNone(self.memory.get("Titan Company"))

    def test_related_entities_linked(self):
        answer = AnalystAnswer(
            question="test",
            claims=[
                Claim(text="Ajoy Chawla leads Titan Company.",
                      citation="https://src.com"),
            ],
            cost=CostRecord(model="test"),
        )
        _store_entities(answer, self.memory)

        record = self.memory.get("Ajoy Chawla")
        self.assertIsNotNone(record)
        self.assertIn("titan_company", record.related_entities)

    def test_claims_without_entities_skipped(self):
        answer = AnalystAnswer(
            question="test",
            claims=[
                Claim(text="revenue grew 20% last year.",
                      citation="https://src.com"),
            ],
            cost=CostRecord(model="test"),
        )
        _store_entities(answer, self.memory)
        self.assertEqual(len(self.memory.entities), 0)


class TestMemoryLookupTool(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w",
        )
        self.tmpfile.write("{}")
        self.tmpfile.close()
        self.memory = EntityMemory(path=self.tmpfile.name)
        self.memory.add_facts(
            "Titan Company",
            [Fact(text="MD is Ajoy Chawla", source="https://example.com")],
            entity_type="company",
        )

    def tearDown(self):
        os.unlink(self.tmpfile.name)

    def test_lookup_existing_entity(self):
        result = _execute_tool_call(
            "memory_lookup", {"entity_name": "Titan"}, memory=self.memory,
        )
        self.assertIn("Titan Company", result)
        self.assertIn("Ajoy Chawla", result)

    def test_lookup_missing_entity(self):
        result = _execute_tool_call(
            "memory_lookup", {"entity_name": "Apple Inc"}, memory=self.memory,
        )
        self.assertIn("No prior knowledge", result)

    def test_lookup_without_memory(self):
        result = _execute_tool_call(
            "memory_lookup", {"entity_name": "Titan"}, memory=None,
        )
        self.assertIn("not available", result)


class TestMemoryRecallBeforePlanning(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w",
        )
        self.tmpfile.write("{}")
        self.tmpfile.close()
        self.memory = EntityMemory(path=self.tmpfile.name)
        self.memory.add_facts(
            "Titan Company",
            [Fact(text="Ajoy Chawla is MD since Jan 2026", source="https://et.com")],
            entity_type="company",
        )

    def tearDown(self):
        os.unlink(self.tmpfile.name)

    @patch("analyst._call_llm")
    def test_memory_context_injected_into_plan(self, mock_llm):
        """When memory has relevant entities, context should appear in planning."""
        plan_response = MagicMock()
        plan_response.choices = [MagicMock()]
        plan_response.choices[0].message.content = "- Search for revenue"
        plan_response.usage = MagicMock(prompt_tokens=100, completion_tokens=20)

        answer_response = MagicMock()
        answer_response.choices = [MagicMock()]
        answer_response.choices[0].message.content = (
            "ANSWER:\nRevenue was 40000 crore. [https://example.com/rev]\n\n"
            "SOURCES:\n- https://example.com/rev: revenue data"
        )
        answer_response.choices[0].message.tool_calls = None
        answer_response.usage = MagicMock(prompt_tokens=200, completion_tokens=50)

        mock_llm.side_effect = [
            (plan_response, "test-model", MagicMock()),
            (answer_response, "test-model", MagicMock()),
        ]

        answer = run_analyst(
            "What was Titan Company's revenue?", memory=self.memory,
        )

        # Verify planning call included memory context
        plan_call_messages = mock_llm.call_args_list[0][0][2]
        plan_text = plan_call_messages[0]["content"]
        self.assertIn("Previously known", plan_text)
        self.assertIn("Ajoy Chawla", plan_text)

    @patch("analyst._call_llm")
    def test_no_memory_context_when_no_entities(self, mock_llm):
        """Questions without recognizable entities should not inject memory."""
        plan_response = MagicMock()
        plan_response.choices = [MagicMock()]
        plan_response.choices[0].message.content = "- Search for answer"
        plan_response.usage = MagicMock(prompt_tokens=100, completion_tokens=20)

        answer_response = MagicMock()
        answer_response.choices = [MagicMock()]
        answer_response.choices[0].message.content = "ANSWER:\n42."
        answer_response.choices[0].message.tool_calls = None
        answer_response.usage = MagicMock(prompt_tokens=200, completion_tokens=10)

        mock_llm.side_effect = [
            (plan_response, "test-model", MagicMock()),
            (answer_response, "test-model", MagicMock()),
        ]

        answer = run_analyst("what is 2 + 2?", memory=self.memory)

        plan_call_messages = mock_llm.call_args_list[0][0][2]
        plan_text = plan_call_messages[0]["content"]
        self.assertNotIn("Previously known", plan_text)


class TestFromMemoryFlag(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w",
        )
        self.tmpfile.write("{}")
        self.tmpfile.close()
        self.memory = EntityMemory(path=self.tmpfile.name)
        self.memory.add_facts(
            "Titan Company",
            [Fact(text="MD is Ajoy Chawla", source="https://example.com")],
        )

    def tearDown(self):
        os.unlink(self.tmpfile.name)

    @patch("analyst._call_llm")
    def test_from_memory_true_when_memory_used(self, mock_llm):
        """Claims should have from_memory=True when memory contributed."""
        plan_response = MagicMock()
        plan_response.choices = [MagicMock()]
        plan_response.choices[0].message.content = "- Check revenue"
        plan_response.usage = MagicMock(prompt_tokens=100, completion_tokens=20)

        answer_response = MagicMock()
        answer_response.choices = [MagicMock()]
        answer_response.choices[0].message.content = (
            "ANSWER:\nRevenue grew. [https://example.com/rev]\n\n"
            "SOURCES:\n- https://example.com/rev: data"
        )
        answer_response.choices[0].message.tool_calls = None
        answer_response.usage = MagicMock(prompt_tokens=200, completion_tokens=30)

        mock_llm.side_effect = [
            (plan_response, "test-model", MagicMock()),
            (answer_response, "test-model", MagicMock()),
        ]

        answer = run_analyst(
            "What was Titan Company's revenue?", memory=self.memory,
        )
        for claim in answer.claims:
            self.assertTrue(claim.from_memory)

    @patch("analyst._call_llm")
    def test_from_memory_false_without_memory(self, mock_llm):
        """Claims should have from_memory=False when no memory is used."""
        plan_response = MagicMock()
        plan_response.choices = [MagicMock()]
        plan_response.choices[0].message.content = "- Search"
        plan_response.usage = MagicMock(prompt_tokens=100, completion_tokens=10)

        answer_response = MagicMock()
        answer_response.choices = [MagicMock()]
        answer_response.choices[0].message.content = "ANSWER:\nSomething."
        answer_response.choices[0].message.tool_calls = None
        answer_response.usage = MagicMock(prompt_tokens=200, completion_tokens=10)

        mock_llm.side_effect = [
            (plan_response, "test-model", MagicMock()),
            (answer_response, "test-model", MagicMock()),
        ]

        answer = run_analyst("What was Titan Company's revenue?", memory=None)
        for claim in answer.claims:
            self.assertFalse(claim.from_memory)


class TestEmptyMemory(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w",
        )
        self.tmpfile.write("{}")
        self.tmpfile.close()
        self.memory = EntityMemory(path=self.tmpfile.name)

    def tearDown(self):
        os.unlink(self.tmpfile.name)

    @patch("analyst._call_llm")
    def test_empty_memory_no_recall(self, mock_llm):
        """Empty memory should not inject context or set from_memory."""
        plan_response = MagicMock()
        plan_response.choices = [MagicMock()]
        plan_response.choices[0].message.content = "- Search"
        plan_response.usage = MagicMock(prompt_tokens=100, completion_tokens=10)

        answer_response = MagicMock()
        answer_response.choices = [MagicMock()]
        answer_response.choices[0].message.content = "ANSWER:\nData."
        answer_response.choices[0].message.tool_calls = None
        answer_response.usage = MagicMock(prompt_tokens=200, completion_tokens=10)

        mock_llm.side_effect = [
            (plan_response, "test-model", MagicMock()),
            (answer_response, "test-model", MagicMock()),
        ]

        answer = run_analyst(
            "What is Titan Company's revenue?", memory=self.memory,
        )

        plan_call_messages = mock_llm.call_args_list[0][0][2]
        plan_text = plan_call_messages[0]["content"]
        self.assertNotIn("Previously known", plan_text)

        for claim in answer.claims:
            self.assertFalse(claim.from_memory)

        # Trace should not have memory_recall event
        events = [e.get("event") for e in answer.tool_trace]
        self.assertNotIn("memory_recall", events)


class TestIsSubstantive(unittest.TestCase):

    def test_rejects_lone_numbers(self):
        self.assertFalse(_is_substantive("1."))
        self.assertFalse(_is_substantive("2."))
        self.assertFalse(_is_substantive("3."))

    def test_rejects_short_fragments(self):
        self.assertFalse(_is_substantive("Yes."))
        self.assertFalse(_is_substantive("## ---"))
        self.assertFalse(_is_substantive("* *"))

    def test_rejects_number_sequences(self):
        self.assertFalse(_is_substantive("12, 34, 56"))

    def test_accepts_real_claims(self):
        self.assertTrue(_is_substantive("Ajoy Chawla is the MD of Titan Company."))
        self.assertTrue(_is_substantive("Revenue grew 33% in FY 2024."))

    def test_accepts_short_but_real(self):
        self.assertTrue(_is_substantive("The CEO resigned in March 2024."))

    def test_rejects_markdown_heading_only(self):
        self.assertFalse(_is_substantive("### ---"))
        self.assertFalse(_is_substantive("**"))


class TestIsDegradedAnswer(unittest.TestCase):

    def test_detects_base64_nonsense(self):
        text = ("It appears that the response is a string of characters. "
                "Try a Base64 decode to interpret it.")
        result = _is_degraded_answer(text, "What is revenue?")
        self.assertIsNotNone(result)
        self.assertIn("base64 decode", result)

    def test_detects_too_short(self):
        result = _is_degraded_answer("Yes.", "What is revenue?")
        self.assertIsNotNone(result)
        self.assertIn("too short", result)

    def test_accepts_normal_answer(self):
        text = ("Titan Company reported total revenue of Rs 55,335 crore "
                "in FY 2024-25, up from Rs 51,084 crore in FY 2023-24. "
                "This represents growth of approximately 8.3 percent.")
        result = _is_degraded_answer(text, "What was Titan's revenue?")
        self.assertIsNone(result)

    def test_accepts_i_dont_know(self):
        text = ("I could not find reliable information about the exact "
                "date when the CEO took over. Multiple sources gave "
                "conflicting dates and I cannot verify which is correct.")
        result = _is_degraded_answer(text, "When did the CEO start?")
        self.assertIsNone(result)


class TestDegradedAnswerSkipsMemoryStorage(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w",
        )
        self.tmpfile.write("{}")
        self.tmpfile.close()
        self.memory = EntityMemory(path=self.tmpfile.name)

    def tearDown(self):
        os.unlink(self.tmpfile.name)

    @patch("analyst._call_llm")
    def test_degraded_answer_not_stored_in_memory(self, mock_llm):
        plan_response = MagicMock()
        plan_response.choices = [MagicMock()]
        plan_response.choices[0].message.content = "- Search"
        plan_response.usage = MagicMock(prompt_tokens=100, completion_tokens=10)

        answer_response = MagicMock()
        answer_response.choices = [MagicMock()]
        answer_response.choices[0].message.content = (
            "It appears that the response is a string of characters. "
            "Try a Base64 decode. [https://example.com/data]\n\n"
            "SOURCES:\n- https://example.com/data: encoded content"
        )
        answer_response.choices[0].message.tool_calls = None
        answer_response.usage = MagicMock(prompt_tokens=200, completion_tokens=50)

        mock_llm.side_effect = [
            (plan_response, "test-model", MagicMock()),
            (answer_response, "test-model", MagicMock()),
        ]

        answer = run_analyst(
            "What was Titan Company's revenue?", memory=self.memory,
        )
        self.assertEqual(len(self.memory.entities), 0)

        events = [e.get("event") for e in answer.tool_trace]
        self.assertIn("degraded_answer", events)


if __name__ == "__main__":
    unittest.main()
