"""
Unit tests for analysis/llm.py — the OpenAI-compatible LLM client.

Fully offline: ``requests.post`` is replaced with a fake, so these tests never
touch the network. Run from tee-service/:
    python -m unittest analysis.test_llm -v

They pin the contract the engine relies on: JSON-mode request shape, the
HTTP-400 retry that drops ``response_format``, output normalisation, and the
"retry once then raise LLMError" failure policy that triggers rule fallback.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import requests

from analysis import llm

VALID_JUDGEMENT = {
    "risk_score": 62,
    "risk_level": "medium",
    "rebalance": [
        {"action": "decrease", "symbol": "btc", "reason": "Over-concentrated."},
        {"action": "hold", "symbol": "ETH", "reason": "Within range."},
    ],
    "summary": "Portfolio is concentrated in BTC; trim toward ETH.",
}


def _completion(content: str, model: str = "deepseek-flash") -> dict:
    """Minimal /chat/completions response envelope."""
    return {
        "id": "chatcmpl-test",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }


class _FakeResponse:
    def __init__(self, status_code: int, body) -> None:
        self.status_code = status_code
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("response body is not JSON")
        return self._body


class _EnvTestCase(unittest.TestCase):
    """Save/restore LLM env vars around every test."""

    _KEYS = (
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_TIMEOUT",
        "LLM_TOTAL_BUDGET",
    )

    def setUp(self) -> None:
        self._saved = {k: os.environ.get(k) for k in self._KEYS}
        os.environ["LLM_API_KEY"] = "sk-test"
        os.environ["LLM_BASE_URL"] = "https://api.example.com/v1"
        os.environ["LLM_MODEL"] = "deepseek-flash"
        os.environ.pop("LLM_TIMEOUT", None)
        os.environ.pop("LLM_TOTAL_BUDGET", None)

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @staticmethod
    def _analyze() -> dict:
        return llm.analyze(
            holdings={"BTC": 1.0, "ETH": 5.0},
            prices={"BTC": 80000.0, "ETH": 4000.0},
            portfolio={
                "total_value_usd": 100000.0,
                "price_source": "coston2-ftso",
                "holdings": [
                    {"symbol": "BTC", "weight_pct": 80.0},
                    {"symbol": "ETH", "weight_pct": 20.0},
                ],
            },
            risk_profile="moderate",
        )


class TestConfigHelpers(_EnvTestCase):
    def test_is_configured_requires_a_non_blank_key(self) -> None:
        self.assertTrue(llm.is_configured())
        os.environ["LLM_API_KEY"] = "   "
        self.assertFalse(llm.is_configured())

    def test_configured_model_falls_back_to_default(self) -> None:
        self.assertEqual(llm.configured_model(), "deepseek-flash")
        os.environ.pop("LLM_MODEL")
        self.assertEqual(llm.configured_model(), llm.DEFAULT_MODEL)


class TestHappyPath(_EnvTestCase):
    @mock.patch("analysis.llm.requests.post")
    def test_valid_json_is_normalised(self, post) -> None:
        post.return_value = _FakeResponse(200, _completion(json.dumps(VALID_JUDGEMENT)))
        out = self._analyze()
        self.assertEqual(out["risk_score"], 62)
        self.assertEqual(out["risk_level"], "medium")
        # symbols upper-cased, actions/summary trimmed and lower-cased
        self.assertEqual(out["rebalance"][0]["symbol"], "BTC")
        self.assertEqual(out["rebalance"][0]["action"], "decrease")
        self.assertEqual(post.call_count, 1)

    @mock.patch("analysis.llm.requests.post")
    def test_request_shape_uses_configured_endpoint_and_model(self, post) -> None:
        post.return_value = _FakeResponse(200, _completion(json.dumps(VALID_JUDGEMENT)))
        self._analyze()
        self.assertEqual(post.call_args.args[0], "https://api.example.com/v1/chat/completions")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer sk-test")

    @mock.patch("analysis.llm.requests.post")
    def test_markdown_fenced_json_is_accepted(self, post) -> None:
        fenced = "```json\n" + json.dumps(VALID_JUDGEMENT) + "\n```"
        post.return_value = _FakeResponse(200, _completion(fenced))
        self.assertEqual(self._analyze()["risk_level"], "medium")


class TestRetryAndFailurePolicy(_EnvTestCase):
    @mock.patch("analysis.llm.requests.post")
    def test_http_400_drops_response_format_on_retry(self, post) -> None:
        post.side_effect = [
            _FakeResponse(400, "response_format is not supported"),
            _FakeResponse(200, _completion(json.dumps(VALID_JUDGEMENT))),
        ]
        self.assertEqual(self._analyze()["risk_score"], 62)
        self.assertEqual(post.call_count, 2)
        self.assertIn("response_format", post.call_args_list[0].kwargs["json"])
        self.assertNotIn("response_format", post.call_args_list[1].kwargs["json"])

    @mock.patch("analysis.llm.requests.post")
    def test_malformed_output_raises_after_one_retry(self, post) -> None:
        post.return_value = _FakeResponse(200, _completion("sorry, no JSON here"))
        with self.assertRaises(llm.LLMError):
            self._analyze()
        self.assertEqual(post.call_count, 2)

    @mock.patch("analysis.llm.requests.post")
    def test_schema_invalid_output_raises(self, post) -> None:
        bad = dict(VALID_JUDGEMENT, risk_level="extreme")
        post.return_value = _FakeResponse(200, _completion(json.dumps(bad)))
        with self.assertRaises(llm.LLMError):
            self._analyze()

    @mock.patch("analysis.llm.requests.post")
    def test_network_error_raises_after_one_retry(self, post) -> None:
        post.side_effect = requests.RequestException("connection reset")
        with self.assertRaises(llm.LLMError):
            self._analyze()
        self.assertEqual(post.call_count, 2)

    def test_missing_key_raises(self) -> None:
        os.environ["LLM_API_KEY"] = ""
        with self.assertRaises(llm.LLMError):
            self._analyze()


class TestTimeBudget(_EnvTestCase):
    def test_defaults_are_exposed(self) -> None:
        self.assertEqual(
            llm.configured_timeout_seconds(), llm.DEFAULT_TIMEOUT_SECONDS
        )
        self.assertEqual(
            llm.configured_total_budget_seconds(),
            llm.DEFAULT_TOTAL_BUDGET_SECONDS,
        )

    def test_total_budget_is_clamped_to_the_cloudfront_safe_ceiling(self) -> None:
        os.environ["LLM_TOTAL_BUDGET"] = "999"
        self.assertEqual(
            llm.configured_total_budget_seconds(),
            llm.MAX_TOTAL_BUDGET_SECONDS,
        )

    @mock.patch("analysis.llm._request_completion")
    def test_attempt_timeout_is_capped_by_remaining_budget(self, request) -> None:
        os.environ["LLM_TIMEOUT"] = "30"
        os.environ["LLM_TOTAL_BUDGET"] = "5"
        request.return_value = json.dumps(VALID_JUDGEMENT)

        self._analyze()

        self.assertEqual(request.call_count, 1)
        self.assertGreater(request.call_args.kwargs["timeout"], 0.0)
        self.assertLessEqual(request.call_args.kwargs["timeout"], 5.0)

    @mock.patch("analysis.llm._request_completion")
    def test_retry_stays_within_per_attempt_timeout(self, request) -> None:
        os.environ["LLM_TIMEOUT"] = "20"
        os.environ["LLM_TOTAL_BUDGET"] = "30"
        request.side_effect = [llm.LLMError("first"), llm.LLMError("second")]

        with self.assertRaises(llm.LLMError):
            self._analyze()

        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            self.assertLessEqual(call.kwargs["timeout"], 20.0)

    @mock.patch("analysis.llm._request_completion")
    def test_exhausted_budget_skips_all_attempts(self, request) -> None:
        os.environ["LLM_TOTAL_BUDGET"] = "0"
        with self.assertRaises(llm.LLMError):
            self._analyze()
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
