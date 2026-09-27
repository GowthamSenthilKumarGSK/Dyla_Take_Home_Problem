"""Tests for provider timeout and fallback behavior in _call_llm."""
from __future__ import annotations
import unittest
from unittest.mock import patch, MagicMock, PropertyMock

from openai import RateLimitError, APITimeoutError, APIConnectionError

from analyst import _call_llm, _is_provider_error, CLOUD_TIMEOUT, OLLAMA_TIMEOUT


def _mock_response(content="hello", pt=10, ct=5):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = None
    resp.usage = MagicMock(prompt_tokens=pt, completion_tokens=ct)
    return resp


def _make_rate_limit_error():
    resp = MagicMock()
    resp.status_code = 429
    resp.headers = {}
    type(resp).text = PropertyMock(return_value="rate limited")
    return RateLimitError(
        message="rate limited", response=resp, body={"error": {"message": "rate limited"}},
    )


def _make_timeout_error():
    req = MagicMock()
    return APITimeoutError(request=req)


def _make_connection_error():
    req = MagicMock()
    return APIConnectionError(message="Connection refused", request=req)


class TestIsProviderError(unittest.TestCase):

    def test_rate_limit_is_provider_error(self):
        self.assertTrue(_is_provider_error(_make_rate_limit_error()))

    def test_timeout_is_provider_error(self):
        self.assertTrue(_is_provider_error(_make_timeout_error()))

    def test_connection_error_is_provider_error(self):
        self.assertTrue(_is_provider_error(_make_connection_error()))

    def test_value_error_is_not_provider_error(self):
        self.assertFalse(_is_provider_error(ValueError("bad")))


class TestFallbackNemotron429ToGemma(unittest.TestCase):

    @patch("analyst.config")
    def test_nemotron_429_falls_back_to_gemma(self, mock_config):
        mock_config.FALLBACK_MODEL = "google/gemma-4-31b-it:free"
        mock_config.OLLAMA_MODEL = "qwen2.5:7b"
        mock_config.OLLAMA_BASE_URL = "http://localhost:11434/v1"

        client = MagicMock()
        gemma_resp = _mock_response("gemma answer")
        client.chat.completions.create.side_effect = [
            _make_rate_limit_error(),
            gemma_resp,
        ]

        trace = []
        resp, model_used, client_used = _call_llm(
            client, "nvidia/nemotron-3-super-120b-a12b:free",
            [{"role": "user", "content": "test"}],
            trace, round_num=1, start_time=0.0,
        )

        self.assertEqual(model_used, "google/gemma-4-31b-it:free")
        self.assertEqual(resp.choices[0].message.content, "gemma answer")
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace[0]["event"], "model_fallback")


class TestFallbackNemotronTimeoutToGemma(unittest.TestCase):

    @patch("analyst.config")
    def test_nemotron_timeout_falls_back_to_gemma(self, mock_config):
        mock_config.FALLBACK_MODEL = "google/gemma-4-31b-it:free"
        mock_config.OLLAMA_MODEL = "qwen2.5:7b"
        mock_config.OLLAMA_BASE_URL = "http://localhost:11434/v1"

        client = MagicMock()
        gemma_resp = _mock_response("gemma answer")
        client.chat.completions.create.side_effect = [
            _make_timeout_error(),
            gemma_resp,
        ]

        trace = []
        resp, model_used, _ = _call_llm(
            client, "nvidia/nemotron-3-super-120b-a12b:free",
            [{"role": "user", "content": "test"}],
            trace, round_num=1, start_time=0.0,
        )

        self.assertEqual(model_used, "google/gemma-4-31b-it:free")
        self.assertEqual(trace[0]["event"], "model_fallback")
        self.assertIn("APITimeoutError", trace[0]["reason"])


class TestFallbackBothCloudToOllama(unittest.TestCase):

    @patch("analyst._make_ollama_client")
    @patch("analyst.config")
    def test_both_cloud_fail_falls_back_to_ollama(self, mock_config, mock_ollama):
        mock_config.FALLBACK_MODEL = "google/gemma-4-31b-it:free"
        mock_config.OLLAMA_MODEL = "qwen2.5:7b"
        mock_config.OLLAMA_BASE_URL = "http://localhost:11434/v1"

        cloud_client = MagicMock()
        cloud_client.chat.completions.create.side_effect = [
            _make_rate_limit_error(),
            _make_rate_limit_error(),
        ]

        ollama_client = MagicMock()
        ollama_resp = _mock_response("ollama answer")
        ollama_client.chat.completions.create.return_value = ollama_resp
        mock_ollama.return_value = ollama_client

        trace = []
        resp, model_used, client_used = _call_llm(
            cloud_client, "nvidia/nemotron-3-super-120b-a12b:free",
            [{"role": "user", "content": "test"}],
            trace, round_num=1, start_time=0.0,
        )

        self.assertEqual(model_used, "qwen2.5:7b")
        self.assertEqual(resp.choices[0].message.content, "ollama answer")
        self.assertEqual(len(trace), 2)
        self.assertEqual(trace[0]["event"], "model_fallback")
        self.assertEqual(trace[1]["event"], "ollama_fallback")


class TestTimeoutCascadesToOllama(unittest.TestCase):

    @patch("analyst._make_ollama_client")
    @patch("analyst.config")
    def test_both_cloud_timeout_reaches_ollama(self, mock_config, mock_ollama):
        mock_config.FALLBACK_MODEL = "google/gemma-4-31b-it:free"
        mock_config.OLLAMA_MODEL = "qwen2.5:7b"
        mock_config.OLLAMA_BASE_URL = "http://localhost:11434/v1"

        cloud_client = MagicMock()
        cloud_client.chat.completions.create.side_effect = [
            _make_timeout_error(),
            _make_timeout_error(),
        ]

        ollama_client = MagicMock()
        ollama_client.chat.completions.create.return_value = _mock_response("local answer")
        mock_ollama.return_value = ollama_client

        trace = []
        resp, model_used, _ = _call_llm(
            cloud_client, "nvidia/nemotron-3-super-120b-a12b:free",
            [{"role": "user", "content": "test"}],
            trace, round_num=1, start_time=0.0,
        )

        self.assertEqual(model_used, "qwen2.5:7b")
        self.assertEqual(resp.choices[0].message.content, "local answer")
        self.assertIn("APITimeoutError", trace[0]["reason"])
        self.assertIn("APITimeoutError", trace[1]["reason"])


class TestNoUnnecessaryFallback(unittest.TestCase):

    @patch("analyst.config")
    def test_successful_cloud_no_fallback(self, mock_config):
        mock_config.FALLBACK_MODEL = "google/gemma-4-31b-it:free"

        client = MagicMock()
        nemotron_resp = _mock_response("nemotron answer")
        client.chat.completions.create.return_value = nemotron_resp

        trace = []
        resp, model_used, _ = _call_llm(
            client, "nvidia/nemotron-3-super-120b-a12b:free",
            [{"role": "user", "content": "test"}],
            trace, round_num=1, start_time=0.0,
        )

        self.assertEqual(model_used, "nvidia/nemotron-3-super-120b-a12b:free")
        self.assertEqual(resp.choices[0].message.content, "nemotron answer")
        self.assertEqual(len(trace), 0)
        client.chat.completions.create.assert_called_once()


class TestClientTimeoutConfig(unittest.TestCase):

    def test_cloud_timeout_is_bounded(self):
        self.assertLessEqual(CLOUD_TIMEOUT, 60)
        self.assertGreaterEqual(CLOUD_TIMEOUT, 10)

    def test_ollama_timeout_is_bounded(self):
        self.assertLessEqual(OLLAMA_TIMEOUT, 180)
        self.assertGreaterEqual(OLLAMA_TIMEOUT, 30)


if __name__ == "__main__":
    unittest.main()
