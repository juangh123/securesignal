"""
Input-validation and schema tests for analysis/engine.py.

Runs fully offline (ANALYSIS_OFFLINE=1 -> dev fixture prices, no RPC):
    python -m unittest analysis.test_engine -v

The decrypted payload is untrusted browser input, so these tests pin the
boundary checks (holdings shape, symbol charset, numeric limits, risk-profile
length) that guard the price provider, the LLM prompt, and the math.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from analysis import engine


class _OfflineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.get("ANALYSIS_OFFLINE")
        os.environ["ANALYSIS_OFFLINE"] = "1"

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("ANALYSIS_OFFLINE", None)
        else:
            os.environ["ANALYSIS_OFFLINE"] = self._saved


class TestHappyPath(_OfflineTestCase):
    def test_fixture_prices_and_rule_engine(self) -> None:
        r = engine.analyze_portfolio({"holdings": {"BTC": 0.5, "ETH": 2, "FLR": 10000}})
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["price_source"], "offline-fixture")
        self.assertEqual(r["analysis_mode"], "rule-fallback")
        self.assertEqual(r["prices_used"]["BTC"], 65000.0)
        self.assertEqual(len(r["holdings"]), 3)
        self.assertIn(r["risk_level"], ("low", "medium", "high"))

    def test_case_and_whitespace_variants_are_merged(self) -> None:
        r = engine.analyze_portfolio({"holdings": {"btc": 1, " BTC ": 2}})
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["holdings"][0]["symbol"], "BTC")
        self.assertEqual(r["holdings"][0]["amount"], 3.0)


class TestHoldingsValidation(_OfflineTestCase):
    def test_rejects_non_object_holdings(self) -> None:
        self.assertEqual(engine.analyze_portfolio({"holdings": []})["status"], "error")

    def test_rejects_empty_holdings(self) -> None:
        self.assertEqual(engine.analyze_portfolio({"holdings": {}})["status"], "error")

    def test_rejects_too_many_holdings(self) -> None:
        holdings = {f"S{i}": 1.0 for i in range(engine.MAX_HOLDINGS + 1)}
        r = engine.analyze_portfolio({"holdings": holdings})
        self.assertEqual(r["status"], "error")
        self.assertIn("at most", r["error"])

    def test_rejects_invalid_symbol_charset(self) -> None:
        r = engine.analyze_portfolio({"holdings": {"BT C": 1.0}})
        self.assertEqual(r["status"], "error")
        self.assertIn("invalid", r["error"])

    def test_rejects_overlong_symbol(self) -> None:
        symbol = "A" * (engine.MAX_SYMBOL_LENGTH + 1)
        self.assertEqual(engine.analyze_portfolio({"holdings": {symbol: 1.0}})["status"], "error")

    def test_rejects_boolean_amount(self) -> None:
        self.assertEqual(engine.analyze_portfolio({"holdings": {"BTC": True}})["status"], "error")

    def test_rejects_non_positive_amount(self) -> None:
        for bad in (0, -1, "2"):
            with self.subTest(amount=bad):
                self.assertEqual(
                    engine.analyze_portfolio({"holdings": {"BTC": bad}})["status"], "error"
                )

    def test_rejects_non_finite_amount(self) -> None:
        for bad in (float("nan"), float("inf")):
            with self.subTest(amount=bad):
                self.assertEqual(
                    engine.analyze_portfolio({"holdings": {"BTC": bad}})["status"], "error"
                )

    def test_rejects_amount_over_limit(self) -> None:
        r = engine.analyze_portfolio({"holdings": {"BTC": engine.MAX_AMOUNT * 2}})
        self.assertEqual(r["status"], "error")
        self.assertIn("limit", r["error"])


class TestRiskProfileValidation(_OfflineTestCase):
    def test_rejects_overlong_profile(self) -> None:
        profile = "x" * (engine.MAX_RISK_PROFILE_LENGTH + 1)
        r = engine.analyze_portfolio({"holdings": {"BTC": 1.0}, "risk_profile": profile})
        self.assertEqual(r["status"], "error")
        self.assertIn("risk_profile", r["error"])

    def test_control_chars_are_stripped(self) -> None:
        r = engine.analyze_portfolio(
            {"holdings": {"BTC": 1.0}, "risk_profile": "con\nservative"}
        )
        self.assertEqual(r["status"], "success")


class TestRiskProfileSensitivity(_OfflineTestCase):
    """The rule engine must react to the profile, not just echo it."""

    def test_conservative_scores_higher_than_aggressive(self) -> None:
        holdings = {"BTC": 0.5, "ETH": 2, "FLR": 10000}
        conservative = engine.analyze_portfolio(
            {"holdings": holdings, "risk_profile": "conservative"}
        )
        aggressive = engine.analyze_portfolio(
            {"holdings": holdings, "risk_profile": "aggressive"}
        )

        self.assertGreater(conservative["risk_score"], aggressive["risk_score"])
        self.assertIn("conservative", conservative["summary"])
        self.assertIn("aggressive", aggressive["summary"])

    def test_trim_threshold_moves_with_profile(self) -> None:
        # 65% BTC / 35% FLR: moderate trims, aggressive (70% threshold) holds.
        holdings = {"BTC": 0.00001, "FLR": 17.5}
        moderate = engine.analyze_portfolio(
            {"holdings": holdings, "risk_profile": "moderate"}
        )
        aggressive = engine.analyze_portfolio(
            {"holdings": holdings, "risk_profile": "aggressive"}
        )

        moderate_actions = {r["symbol"]: r["action"] for r in moderate["rebalance"]}
        aggressive_actions = {
            r["symbol"]: r["action"] for r in aggressive["rebalance"]
        }
        self.assertEqual(moderate_actions["BTC"], "decrease")
        self.assertEqual(aggressive_actions["BTC"], "hold")

    def test_unknown_profile_falls_back_to_moderate(self) -> None:
        holdings = {"BTC": 0.5, "ETH": 2}
        unknown = engine.analyze_portfolio(
            {"holdings": holdings, "risk_profile": "yolo-999"}
        )
        moderate = engine.analyze_portfolio(
            {"holdings": holdings, "risk_profile": "moderate"}
        )

        self.assertEqual(unknown["risk_score"], moderate["risk_score"])
        self.assertEqual(unknown["rebalance"], moderate["rebalance"])


class TestPriceProviderFailure(_OfflineTestCase):
    def test_unknown_symbol_surfaces_as_error_not_fake_price(self) -> None:
        r = engine.analyze_portfolio({"holdings": {"NOTREAL": 1.0}})
        self.assertEqual(r["status"], "error")
        self.assertIn("price provider failed", r["error"])
        self.assertNotIn("prices_used", r)


class TestAnalysisModeMatrix(_OfflineTestCase):
    """offline/online x llm/rule: the reported mode must match reality."""

    LLM_JUDGEMENT = {
        "risk_score": 11,
        "risk_level": "low",
        "rebalance": [{"action": "hold", "symbol": "BTC", "reason": "ok"}],
        "summary": "llm summary",
    }

    def test_offline_rule_mode(self) -> None:
        r = engine.analyze_portfolio({"holdings": {"BTC": 1.0}})

        self.assertEqual(r["analysis_mode"], "rule-fallback")
        self.assertEqual(r["price_source"], "offline-fixture")

    def test_offline_llm_mode(self) -> None:
        with mock.patch.object(
            engine.llm, "is_configured", return_value=True
        ), mock.patch.object(
            engine.llm, "analyze", return_value=dict(self.LLM_JUDGEMENT)
        ) as llm_call:
            r = engine.analyze_portfolio(
                {"holdings": {"BTC": 1.0}, "risk_profile": "aggressive"}
            )

        self.assertEqual(r["analysis_mode"], "llm")
        self.assertEqual(r["risk_score"], 11)
        self.assertEqual(r["summary"], "llm summary")
        # The LLM only sees server-computed ground truth, never raw amounts.
        self.assertEqual(llm_call.call_args.kwargs["prices"]["BTC"], 65000.0)
        self.assertEqual(
            llm_call.call_args.kwargs["portfolio"]["price_source"],
            "offline-fixture",
        )

    def test_offline_llm_failure_falls_back_to_rule(self) -> None:
        with mock.patch.object(
            engine.llm, "is_configured", return_value=True
        ), mock.patch.object(
            engine.llm,
            "analyze",
            side_effect=engine.llm.LLMError("mock failure"),
        ):
            r = engine.analyze_portfolio({"holdings": {"BTC": 1.0}})

        self.assertEqual(r["analysis_mode"], "rule-fallback")
        self.assertIn("fell back", r["summary"])

    def test_online_rule_mode_uses_provider_prices(self) -> None:
        os.environ.pop("ANALYSIS_OFFLINE", None)
        with mock.patch.object(
            engine.price_provider, "get_prices", return_value={"BTC": 70000.0}
        ), mock.patch.object(
            engine.price_provider, "get_price_source", return_value="coston2-ftso"
        ):
            r = engine.analyze_portfolio({"holdings": {"BTC": 2.0}})

        self.assertEqual(r["analysis_mode"], "rule-fallback")
        self.assertEqual(r["price_source"], "coston2-ftso")
        self.assertEqual(r["total_value_usd"], 140000.0)

    def test_online_llm_mode(self) -> None:
        os.environ.pop("ANALYSIS_OFFLINE", None)
        with mock.patch.object(
            engine.price_provider, "get_prices", return_value={"BTC": 70000.0}
        ), mock.patch.object(
            engine.price_provider, "get_price_source", return_value="coston2-ftso"
        ), mock.patch.object(
            engine.llm, "is_configured", return_value=True
        ), mock.patch.object(
            engine.llm, "analyze", return_value=dict(self.LLM_JUDGEMENT)
        ):
            r = engine.analyze_portfolio({"holdings": {"BTC": 2.0}})

        self.assertEqual(r["analysis_mode"], "llm")
        self.assertEqual(r["price_source"], "coston2-ftso")
        self.assertEqual(r["total_value_usd"], 140000.0)


if __name__ == "__main__":
    unittest.main()
