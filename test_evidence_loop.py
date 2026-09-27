"""Tests for the evidence-aware research loop."""
from __future__ import annotations
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from models import (
    SearchResult, SearchResponse, PageContent, Claim, CostRecord, AnalystAnswer,
    Fact,
)
from memory import EntityMemory
from analyst import (
    _parse_plan_queries, _select_sources, _parse_evaluation,
    run_analyst, MAX_RESEARCH_ROUNDS,
)
from auditor import run_auditor


# ── Helpers ───────────────────────────────────────────────────────────

def _mock_llm_response(content: str, pt=100, ct=20):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = None
    resp.usage = MagicMock(prompt_tokens=pt, completion_tokens=ct)
    return resp


def _plan_response(queries=None):
    queries = queries or ['"Titan Company MD"', '"Titan leadership 2025"']
    q_lines = "\n".join(f"- {q}" for q in queries)
    return _mock_llm_response(
        f"QUERIES:\n{q_lines}\n\nPLAN:\n- Find MD name\n- Cross-check"
    )


def _eval_response(sufficient=True, conflicts="none", missing="nothing critical",
                   follow_up="none"):
    return _mock_llm_response(
        f"SUFFICIENT: {'YES' if sufficient else 'NO'}\n"
        f"CONFLICTS: {conflicts}\n"
        f"MISSING: {missing}\n"
        f"FOLLOW_UP: {follow_up}\n"
        f"STOP_REASON: {'Evidence adequate' if sufficient else 'Need more data'}"
    )


def _answer_response(text=None):
    text = text or (
        "ANSWER:\nAjoy Chawla is the Managing Director of Titan Company "
        "since October 2024 [https://example.com/titan].\n\n"
        "SOURCES:\n- https://example.com/titan: leadership page"
    )
    return _mock_llm_response(text, pt=600, ct=50)


def _search_results(urls=None):
    urls = urls or [("https://example.com/titan", "Titan Leadership", 0.9)]
    results = [
        SearchResult(title=t, url=u, snippet=f"Info about {t}", score=s)
        for u, t, s in urls
    ]
    return SearchResponse(query="test", results=results)


def _page_content(url="https://example.com/titan", title="Titan Leadership",
                  text="Ajoy Chawla was appointed Managing Director of Titan."):
    return PageContent(url=url, title=title, text=text)


# ── Unit tests: _parse_plan_queries ──────────────────────────────────

class TestParsePlanQueries(unittest.TestCase):

    def test_structured_format(self):
        plan = (
            'QUERIES:\n- "Titan Company revenue"\n- "Titan FY 2025"\n\n'
            'PLAN:\n- Find revenue\n- Cross-check sources'
        )
        queries, body = _parse_plan_queries(plan)
        self.assertEqual(queries, ["Titan Company revenue", "Titan FY 2025"])
        self.assertIn("Find revenue", body)

    def test_fallback_quoted_strings(self):
        plan = 'Search for "Titan Company CEO" and "Titan annual report 2025".'
        queries, body = _parse_plan_queries(plan)
        self.assertIn("Titan Company CEO", queries)
        self.assertIn("Titan annual report 2025", queries)

    def test_unquoted_bullets(self):
        plan = "QUERIES:\n- Titan Company revenue FY 2025\n- Titan annual report"
        queries, _ = _parse_plan_queries(plan)
        self.assertTrue(len(queries) >= 2)

    def test_caps_at_six(self):
        lines = "\n".join(f'- "query {i}"' for i in range(10))
        plan = f"QUERIES:\n{lines}\n\nPLAN:\n- stuff"
        queries, _ = _parse_plan_queries(plan)
        self.assertLessEqual(len(queries), 6)

    def test_empty_plan_returns_empty(self):
        queries, _ = _parse_plan_queries("")
        self.assertEqual(queries, [])


# ── Unit tests: _select_sources ──────────────────────────────────────

class TestSelectSources(unittest.TestCase):

    def test_prefers_gov_sources(self):
        results = [
            SearchResult(title="A", url="https://blog.com/a", snippet="x", score=0.8),
            SearchResult(title="B", url="https://data.gov.in/b", snippet="y", score=0.8),
        ]
        selected = _select_sources(results, set())
        self.assertEqual(selected[0]["url"], "https://data.gov.in/b")

    def test_skips_already_fetched(self):
        results = [
            SearchResult(title="A", url="https://a.com", snippet="x", score=0.9),
            SearchResult(title="B", url="https://b.com", snippet="y", score=0.8),
        ]
        selected = _select_sources(results, {"https://a.com"})
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["url"], "https://b.com")

    def test_deduplicates_urls(self):
        results = [
            SearchResult(title="A", url="https://a.com", snippet="x", score=0.9),
            SearchResult(title="A dup", url="https://a.com", snippet="y", score=0.85),
        ]
        selected = _select_sources(results, set())
        self.assertEqual(len(selected), 1)

    def test_respects_max_sources(self):
        results = [
            SearchResult(title=f"R{i}", url=f"https://r{i}.com", snippet="x", score=0.5)
            for i in range(10)
        ]
        selected = _select_sources(results, set(), max_sources=2)
        self.assertEqual(len(selected), 2)

    def test_penalizes_low_quality(self):
        results = [
            SearchResult(title="Q", url="https://quora.com/q", snippet="x", score=0.8),
            SearchResult(title="B", url="https://bbc.com/article", snippet="y", score=0.7),
        ]
        selected = _select_sources(results, set())
        self.assertEqual(selected[0]["url"], "https://bbc.com/article")


# ── Unit tests: _parse_evaluation ────────────────────────────────────

class TestParseEvaluation(unittest.TestCase):

    def test_sufficient_yes(self):
        text = (
            "SUFFICIENT: YES\nCONFLICTS: none\nMISSING: nothing critical\n"
            "FOLLOW_UP: none\nSTOP_REASON: Have enough data"
        )
        result = _parse_evaluation(text)
        self.assertTrue(result['sufficient'])
        self.assertEqual(result['conflicts'], 'none')
        self.assertIsNone(result['follow_up_query'])

    def test_insufficient_with_follow_up(self):
        text = (
            "SUFFICIENT: NO\nCONFLICTS: none\nMISSING: revenue data\n"
            'FOLLOW_UP: "Titan revenue FY 2025"\nSTOP_REASON: Need revenue'
        )
        result = _parse_evaluation(text)
        self.assertFalse(result['sufficient'])
        self.assertEqual(result['follow_up_query'], "Titan revenue FY 2025")

    def test_conflicts_detected(self):
        text = (
            "SUFFICIENT: YES\nCONFLICTS: Source A says 2023, source B says 2024\n"
            "MISSING: nothing\nFOLLOW_UP: none\nSTOP_REASON: Resolved"
        )
        result = _parse_evaluation(text)
        self.assertIn("Source A", result['conflicts'])

    def test_follow_up_none_not_treated_as_query(self):
        text = "SUFFICIENT: NO\nCONFLICTS: none\nMISSING: x\nFOLLOW_UP: none"
        result = _parse_evaluation(text)
        self.assertIsNone(result['follow_up_query'])

    def test_follow_up_hyphenated(self):
        text = "SUFFICIENT: NO\nFOLLOW-UP: Titan share price 2025"
        result = _parse_evaluation(text)
        self.assertEqual(result['follow_up_query'], "Titan share price 2025")


# ── Integration tests: run_analyst with evidence loop ────────────────

class TestSufficientEvidenceStops(unittest.TestCase):

    @patch("analyst.fetch_page")
    @patch("analyst.web_search")
    @patch("analyst._call_llm")
    def test_stops_after_sufficient_evidence(self, mock_llm, mock_search, mock_fetch):
        mock_llm.side_effect = [
            (_plan_response(), "test-model", MagicMock()),
            (_eval_response(sufficient=True), "test-model", MagicMock()),
            (_answer_response(), "test-model", MagicMock()),
        ]
        mock_search.return_value = _search_results()
        mock_fetch.return_value = _page_content()

        answer = run_analyst("Who is the MD of Titan?")

        events = [e.get("event") for e in answer.tool_trace]
        self.assertIn("research_complete", events)
        self.assertNotIn("follow_up_search", events)
        self.assertIn("evidence_evaluation", events)
        self.assertEqual(len(answer.claims), 1)
        self.assertIsNotNone(answer.claims[0].citation)


class TestInsufficientEvidenceFollowUp(unittest.TestCase):

    @patch("analyst.fetch_page")
    @patch("analyst.web_search")
    @patch("analyst._call_llm")
    def test_follow_up_on_insufficient(self, mock_llm, mock_search, mock_fetch):
        single_query_plan = _mock_llm_response(
            'QUERIES:\n- "Titan revenue"\n\nPLAN:\n- Find revenue data'
        )
        mock_llm.side_effect = [
            (single_query_plan, "test-model", MagicMock()),
            # Round 1: insufficient
            (_eval_response(sufficient=False,
                           follow_up="Titan Company revenue FY 2025",
                           missing="revenue data"),
             "test-model", MagicMock()),
            # Round 2: sufficient
            (_eval_response(sufficient=True), "test-model", MagicMock()),
            # Final answer
            (_answer_response(), "test-model", MagicMock()),
        ]
        mock_search.side_effect = [
            _search_results([("https://a.com", "Page A", 0.9)]),
            _search_results([("https://b.com", "Page B", 0.85)]),
        ]
        mock_fetch.side_effect = [
            _page_content("https://a.com", "Page A", "Some info"),
            _page_content("https://b.com", "Page B", "Revenue data"),
        ]

        answer = run_analyst("What was Titan's revenue?")

        events = [e.get("event") for e in answer.tool_trace]
        self.assertIn("follow_up_search", events)
        eval_events = [e for e in answer.tool_trace
                       if e.get("event") == "evidence_evaluation"]
        self.assertEqual(len(eval_events), 2)


class TestConflictingSourcesDetected(unittest.TestCase):

    @patch("analyst.fetch_page")
    @patch("analyst.web_search")
    @patch("analyst._call_llm")
    def test_conflict_in_trace(self, mock_llm, mock_search, mock_fetch):
        mock_llm.side_effect = [
            (_plan_response(), "test-model", MagicMock()),
            (_eval_response(sufficient=True,
                           conflicts="Source A says 2023, source B says 2024"),
             "test-model", MagicMock()),
            (_answer_response(
                "ANSWER:\nSources disagree on the date. Source A reports 2023 "
                "[https://a.com] while source B reports 2024. [https://b.com]\n\n"
                "SOURCES:\n- https://a.com: date info\n- https://b.com: date info"
            ), "test-model", MagicMock()),
        ]
        mock_search.return_value = _search_results([
            ("https://a.com", "Source A", 0.9),
            ("https://b.com", "Source B", 0.85),
        ])
        mock_fetch.side_effect = [
            _page_content("https://a.com", "A", "Event happened in 2023."),
            _page_content("https://b.com", "B", "Event happened in 2024."),
        ]

        answer = run_analyst("When did the event happen?")

        events = [e.get("event") for e in answer.tool_trace]
        self.assertIn("conflicts_detected", events)
        conflict_entry = next(e for e in answer.tool_trace
                             if e.get("event") == "conflicts_detected")
        self.assertIn("2023", conflict_entry["conflicts"])


class TestResearchLoopTerminates(unittest.TestCase):

    @patch("analyst.fetch_page")
    @patch("analyst.web_search")
    @patch("analyst._call_llm")
    def test_stops_at_max_rounds(self, mock_llm, mock_search, mock_fetch):
        eval_insufficient = _eval_response(
            sufficient=False, follow_up="more data", missing="still missing",
        )
        responses = [(_plan_response(), "test-model", MagicMock())]
        for _ in range(MAX_RESEARCH_ROUNDS):
            responses.append((eval_insufficient, "test-model", MagicMock()))
        responses.append((_answer_response(), "test-model", MagicMock()))

        mock_llm.side_effect = responses

        search_results = [
            _search_results([(f"https://s{i}.com", f"S{i}", 0.7)])
            for i in range(MAX_RESEARCH_ROUNDS + 5)
        ]
        mock_search.side_effect = search_results

        pages = [
            _page_content(f"https://s{i}.com", f"S{i}", f"Content {i}")
            for i in range(MAX_RESEARCH_ROUNDS * 3 + 5)
        ]
        mock_fetch.side_effect = pages

        answer = run_analyst("Complex question?")

        complete_events = [e for e in answer.tool_trace
                          if e.get("event") == "research_complete"]
        self.assertTrue(len(complete_events) >= 1)
        self.assertIn("final_answer",
                      [e.get("event") for e in answer.tool_trace])


class TestCitationPreservation(unittest.TestCase):

    @patch("analyst.fetch_page")
    @patch("analyst.web_search")
    @patch("analyst._call_llm")
    def test_answer_has_citations(self, mock_llm, mock_search, mock_fetch):
        mock_llm.side_effect = [
            (_plan_response(), "test-model", MagicMock()),
            (_eval_response(sufficient=True), "test-model", MagicMock()),
            (_answer_response(), "test-model", MagicMock()),
        ]
        mock_search.return_value = _search_results()
        mock_fetch.return_value = _page_content()

        answer = run_analyst("Who is the MD of Titan?")

        cited = [c for c in answer.claims if c.citation]
        self.assertTrue(len(cited) > 0)
        self.assertTrue(any("example.com" in c.citation for c in cited))


class TestMemoryDoesNotReplaceCitations(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w",
        )
        self.tmpfile.write("{}")
        self.tmpfile.close()
        self.memory = EntityMemory(path=self.tmpfile.name)
        self.memory.add_facts(
            "Titan Company",
            [Fact(text="Ajoy Chawla is MD", source="https://old.com")],
        )

    def tearDown(self):
        os.unlink(self.tmpfile.name)

    @patch("analyst.fetch_page")
    @patch("analyst.web_search")
    @patch("analyst._call_llm")
    def test_web_citations_required_despite_memory(self, mock_llm, mock_search,
                                                    mock_fetch):
        mock_llm.side_effect = [
            (_plan_response(), "test-model", MagicMock()),
            (_eval_response(sufficient=True), "test-model", MagicMock()),
            (_answer_response(), "test-model", MagicMock()),
        ]
        mock_search.return_value = _search_results()
        mock_fetch.return_value = _page_content()

        answer = run_analyst(
            "Who is the MD of Titan Company?", memory=self.memory,
        )

        self.assertTrue(answer.claims[0].from_memory)
        self.assertIsNotNone(answer.claims[0].citation)
        self.assertIn("example.com", answer.claims[0].citation)
        self.assertTrue(len(answer.sources_used) > 0)


class TestAnalystAuditorCompatible(unittest.TestCase):

    @patch("auditor.fetch_page")
    @patch("auditor._call_llm")
    @patch("analyst.fetch_page")
    @patch("analyst.web_search")
    @patch("analyst._call_llm")
    def test_auditor_accepts_analyst_output(self, mock_analyst_llm,
                                            mock_search, mock_analyst_fetch,
                                            mock_auditor_llm, mock_auditor_fetch):
        mock_analyst_llm.side_effect = [
            (_plan_response(), "test-model", MagicMock()),
            (_eval_response(sufficient=True), "test-model", MagicMock()),
            (_answer_response(), "test-model", MagicMock()),
        ]
        mock_search.return_value = _search_results()
        mock_analyst_fetch.return_value = _page_content()

        answer = run_analyst("Who is the MD of Titan?")

        mock_auditor_fetch.return_value = _page_content()
        audit_resp = MagicMock()
        audit_resp.choices = [MagicMock()]
        audit_resp.choices[0].message.content = (
            "VERDICT: SUPPORTED\nEVIDENCE: Confirmed.\nEXCERPT: \"Ajoy Chawla\""
        )
        audit_resp.usage = MagicMock(prompt_tokens=100, completion_tokens=20)
        mock_auditor_llm.return_value = (audit_resp, "test-model", MagicMock())

        report = run_auditor(answer)

        self.assertTrue(len(report.verdicts) > 0)
        self.assertIn("claim(s) audited", report.summary)


if __name__ == "__main__":
    unittest.main()
