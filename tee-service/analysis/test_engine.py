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


class TestPriceProviderFailure(_OfflineTestCase):
    def test_unknown_symbol_surfaces_as_error_not_fake_price(self) -> None:
        r = engine.analyze_portfolio({"holdings": {"NOTREAL": 1.0}})
        self.assertEqual(r["status"], "error")
        self.assertIn("price provider failed", r["error"])
        self.assertNotIn("prices_used", r)


if __name__ == "__main__":
    unittest.main()
