"""Tests for the Auditor — uses mocked fetch_page and LLM to verify all verdict types."""
from __future__ import annotations
import unittest
from unittest.mock import patch, MagicMock
from models import Claim, CostRecord, AnalystAnswer, AuditVerdict, PageContent
from auditor import run_auditor, _parse_verdict, _build_summary


# ── Helper fixtures ──────────────────────────────────────────────────

def _make_analyst_answer(claims: list[Claim], sources: list[str] | None = None):
    return AnalystAnswer(
        question="Test question",
        plan="Test plan",
        claims=claims,
        summary="Test summary",
        sources_used=sources or [],
        cost=CostRecord(model="test-model"),
    )


def _mock_page(url: str, text: str, error: str | None = None) -> PageContent:
    return PageContent(url=url, title="Mock", text=text, error=error)


# ── Unit tests for _parse_verdict ────────────────────────────────────

class TestParseVerdict(unittest.TestCase):

    def test_supported(self):
        text = "VERDICT: SUPPORTED\nEVIDENCE: The page confirms this.\nEXCERPT: \"exact quote\""
        verdict, evidence, excerpt = _parse_verdict(text)
        self.assertEqual(verdict, "supported")
        self.assertIn("confirms", evidence)
        self.assertIn("exact quote", excerpt)

    def test_contradicted(self):
        text = "VERDICT: CONTRADICTED\nEVIDENCE: The page says otherwise.\nEXCERPT: \"wrong data\""
        verdict, evidence, excerpt = _parse_verdict(text)
        self.assertEqual(verdict, "contradicted")

    def test_unsupported(self):
        text = "VERDICT: UNSUPPORTED\nEVIDENCE: Page does not mention this.\nEXCERPT: none"
        verdict, evidence, excerpt = _parse_verdict(text)
        self.assertEqual(verdict, "unsupported")
        self.assertEqual(excerpt, "")

    def test_malformed_defaults_unsupported(self):
        verdict, evidence, excerpt = _parse_verdict("random garbage text")
        self.assertEqual(verdict, "unsupported")


# ── Unit tests for _build_summary ────────────────────────────────────

class TestBuildSummary(unittest.TestCase):

    def test_all_supported(self):
        verdicts = [
            AuditVerdict(claim=Claim(text="a"), verdict="supported"),
            AuditVerdict(claim=Claim(text="b"), verdict="supported"),
        ]
        summary = _build_summary(verdicts)
        self.assertIn("2 claim(s) audited", summary)
        self.assertIn("2 supported", summary)
        self.assertIn("HIGH", summary)

    def test_mixed(self):
        verdicts = [
            AuditVerdict(claim=Claim(text="a"), verdict="supported"),
            AuditVerdict(claim=Claim(text="b"), verdict="no_citation"),
            AuditVerdict(claim=Claim(text="c"), verdict="unsupported"),
        ]
        summary = _build_summary(verdicts)
        self.assertIn("3 claim(s) audited", summary)
        self.assertIn("LOW", summary)  # 33% supported -> LOW

    def test_contradicted_is_low(self):
        verdicts = [
            AuditVerdict(claim=Claim(text="a"), verdict="supported"),
            AuditVerdict(claim=Claim(text="b"), verdict="contradicted"),
        ]
        summary = _build_summary(verdicts)
        self.assertIn("LOW", summary)
        self.assertIn("contradicted", summary.lower())

    def test_empty(self):
        self.assertEqual(_build_summary([]), "No claims to audit.")


# ── Integration tests with mocked LLM and fetch ─────────────────────

class TestRunAuditor(unittest.TestCase):

    @patch("auditor.fetch_page")
    @patch("auditor._call_llm")
    def test_no_citation_claim(self, mock_llm, mock_fetch):
        """A claim with no citation should get no_citation without any LLM call."""
        answer = _make_analyst_answer([
            Claim(text="The sky is blue.", citation=None),
        ])
        report = run_auditor(answer)
        self.assertEqual(len(report.verdicts), 1)
        self.assertEqual(report.verdicts[0].verdict, "no_citation")
        mock_llm.assert_not_called()
        mock_fetch.assert_not_called()

    @patch("auditor.fetch_page")
    @patch("auditor._call_llm")
    def test_supported_claim(self, mock_llm, mock_fetch):
        """A claim whose source confirms it should be 'supported'."""
        mock_fetch.return_value = _mock_page(
            "https://example.com/ceo",
            "Ajoy Chawla was appointed Managing Director of Titan Company.",
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            "VERDICT: SUPPORTED\n"
            "EVIDENCE: The page states Ajoy Chawla was appointed MD.\n"
            "EXCERPT: \"Ajoy Chawla was appointed Managing Director\""
        )
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=30)
        mock_llm.return_value = (mock_response, "test-model", MagicMock())

        answer = _make_analyst_answer(
            [Claim(text="Ajoy Chawla is the MD of Titan.", citation="https://example.com/ceo")],
            sources=["https://example.com/ceo"],
        )
        report = run_auditor(answer)
        self.assertEqual(len(report.verdicts), 1)
        self.assertEqual(report.verdicts[0].verdict, "supported")
        self.assertIn("Ajoy Chawla", report.verdicts[0].evidence)

    @patch("auditor.fetch_page")
    @patch("auditor._call_llm")
    def test_contradicted_claim(self, mock_llm, mock_fetch):
        """A claim contradicted by its source."""
        mock_fetch.return_value = _mock_page(
            "https://example.com/ceo",
            "C.K. Venkataraman retired and Ajoy Chawla replaced him as MD.",
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            "VERDICT: CONTRADICTED\n"
            "EVIDENCE: The page says Venkataraman retired, not that he is current MD.\n"
            "EXCERPT: \"C.K. Venkataraman retired\""
        )
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=30)
        mock_llm.return_value = (mock_response, "test-model", MagicMock())

        answer = _make_analyst_answer(
            [Claim(text="C.K. Venkataraman is the current MD.", citation="https://example.com/ceo")],
            sources=["https://example.com/ceo"],
        )
        report = run_auditor(answer)
        self.assertEqual(report.verdicts[0].verdict, "contradicted")

    @patch("auditor.fetch_page")
    @patch("auditor._call_llm")
    def test_unsupported_claim(self, mock_llm, mock_fetch):
        """A claim not mentioned in the source."""
        mock_fetch.return_value = _mock_page(
            "https://example.com/page",
            "This page is about cooking recipes.",
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            "VERDICT: UNSUPPORTED\n"
            "EVIDENCE: The page is about cooking, not corporate leadership.\n"
            "EXCERPT: none"
        )
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=20)
        mock_llm.return_value = (mock_response, "test-model", MagicMock())

        answer = _make_analyst_answer(
            [Claim(text="Titan's CEO joined in 1990.", citation="https://example.com/page")],
            sources=["https://example.com/page"],
        )
        report = run_auditor(answer)
        self.assertEqual(report.verdicts[0].verdict, "unsupported")

    @patch("auditor.fetch_page")
    def test_source_error_on_fetch_failure(self, mock_fetch):
        """A failed source fetch should produce source_error, not unsupported."""
        mock_fetch.return_value = _mock_page(
            "https://example.com/broken",
            "",
            error="Connection timeout",
        )
        answer = _make_analyst_answer(
            [Claim(text="Revenue grew 20%.", citation="https://example.com/broken")],
            sources=["https://example.com/broken"],
        )
        report = run_auditor(answer)
        self.assertEqual(report.verdicts[0].verdict, "source_error")
        self.assertIn("Could not fetch", report.verdicts[0].evidence)
        self.assertTrue(len(report.limitations) > 0)

    @patch("auditor.fetch_page")
    @patch("auditor._call_llm")
    def test_mixed_claims(self, mock_llm, mock_fetch):
        """Multiple claims with different verdicts in one report."""
        mock_fetch.side_effect = [
            _mock_page("https://a.com", "Ajoy Chawla is MD of Titan."),
            _mock_page("https://b.com", "", error="404 Not Found"),
        ]

        def llm_side_effect(client, model, messages, trace, round_num, start_time, tools=None):
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = (
                "VERDICT: SUPPORTED\n"
                "EVIDENCE: Confirmed by page.\n"
                "EXCERPT: \"Ajoy Chawla is MD\""
            )
            resp.usage = MagicMock(prompt_tokens=100, completion_tokens=20)
            return resp, model, client

        mock_llm.side_effect = llm_side_effect

        claims = [
            Claim(text="Ajoy Chawla is MD.", citation="https://a.com"),
            Claim(text="Uncited fact."),
            Claim(text="Revenue data.", citation="https://b.com"),
        ]
        answer = _make_analyst_answer(claims, sources=["https://a.com", "https://b.com"])
        report = run_auditor(answer)

        self.assertEqual(len(report.verdicts), 3)
        self.assertEqual(report.verdicts[0].verdict, "supported")
        self.assertEqual(report.verdicts[1].verdict, "no_citation")
        self.assertEqual(report.verdicts[2].verdict, "source_error")
        self.assertIn("3 claim(s) audited", report.summary)

    @patch("auditor.fetch_page")
    @patch("auditor._call_llm")
    def test_cost_tracked(self, mock_llm, mock_fetch):
        """Auditor should track its own token costs."""
        mock_fetch.return_value = _mock_page("https://x.com", "Some text.")
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "VERDICT: SUPPORTED\nEVIDENCE: yes\nEXCERPT: text"
        mock_response.usage = MagicMock(prompt_tokens=200, completion_tokens=50)
        mock_llm.return_value = (mock_response, "test-model", MagicMock())

        answer = _make_analyst_answer(
            [Claim(text="Fact.", citation="https://x.com")],
            sources=["https://x.com"],
        )
        report = run_auditor(answer)
        self.assertEqual(report.cost.input_tokens, 200)
        self.assertEqual(report.cost.output_tokens, 50)

    @patch("auditor.fetch_page")
    def test_source_fetched_once_for_multiple_claims(self, mock_fetch):
        """Same URL cited by multiple claims should only be fetched once."""
        mock_fetch.return_value = _mock_page("https://same.com", "Page text.")

        with patch("auditor._call_llm") as mock_llm:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "VERDICT: SUPPORTED\nEVIDENCE: ok\nEXCERPT: text"
            mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=20)
            mock_llm.return_value = (mock_response, "test-model", MagicMock())

            claims = [
                Claim(text="Fact A.", citation="https://same.com"),
                Claim(text="Fact B.", citation="https://same.com"),
            ]
            answer = _make_analyst_answer(claims, sources=["https://same.com"])
            report = run_auditor(answer)

        mock_fetch.assert_called_once_with("https://same.com")
        self.assertEqual(len(report.verdicts), 2)


if __name__ == "__main__":
    unittest.main()
