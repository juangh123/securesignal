"""
Portfolio analysis engine — unified output schema (the frontend contract).

Flow
----
1. Prices come from ``analysis.price_provider`` (FTSO on-chain reads on
   Coston2, or dev fixtures when ANALYSIS_OFFLINE=1). Any failure surfaces
   as an explicit ``{"status": "error", "error": ...}`` result — the service
   NEVER returns fake prices (see plan.md).
2. Portfolio math (per-asset USD value, weights, total value) is computed
   deterministically here — never delegated to the LLM.
3. Judgement (risk score / level, rebalance advice, English summary):
     * when the LLM env is complete (LLM_API_KEY set), ``analysis.llm`` is
       called -> ``analysis_mode = "llm"``;
     * if the LLM is not configured, or the LLM call/validation fails, the
       deterministic rule engine runs instead ->
       ``analysis_mode = "rule-fallback"``.
   Both paths emit the EXACT same result schema.

Standard result schema (success path — exact contract with the frontend)::

    {
      "status": "success",
      "analysis_mode": "llm" | "rule-fallback",
      "price_source": <str>,                 # "coston2-ftso" | "offline-fixture" | ...
      "prices_used": {symbol: price},        # the real prices used
      "total_value_usd": <float>,
      "holdings": [{"symbol", "amount", "value_usd", "weight_pct"}],
      "risk_score": <int 0-100>,
      "risk_level": "low" | "medium" | "high",
      "rebalance": [{"action": "increase" | "decrease" | "hold",
                     "symbol", "reason"}],
      "summary": <English analysis text>,
    }

Error path: ``{"status": "error", "error": <str>}`` — minimal and honest;
no price/valuation fields are ever fabricated on the error path.
"""

from __future__ import annotations

from typing import Any, Dict, List

from analysis import llm, price_provider

__all__ = ["analyze_portfolio"]

RISK_LEVELS = ("low", "medium", "high")


def _error(message: str) -> Dict[str, Any]:
    """Explicit error result. Never carries fabricated price/valuation data."""
    return {"status": "error", "error": message}


def _rule_based_analysis(
    holdings_detail: List[Dict[str, Any]],
    total_value_usd: float,
    risk_profile: str,
) -> Dict[str, Any]:
    """
    Deterministic heuristic analysis — the offline / LLM-failure fallback.

    Returns the same judgement fields the LLM path returns:
    risk_score / risk_level / rebalance / summary.
    """
    n = len(holdings_detail)
    weights = [h["weight_pct"] for h in holdings_detail]

    # Concentration risk via a normalized Herfindahl index:
    # 0.0 = perfectly diversified across n assets, 1.0 = single-asset portfolio.
    herfindahl = sum((w / 100.0) ** 2 for w in weights)
    if n > 1:
        concentration = (herfindahl - 1.0 / n) / (1.0 - 1.0 / n)
    else:
        concentration = 1.0
    concentration = max(0.0, min(1.0, concentration))

    score = 25.0 + 50.0 * concentration

    # Single-ecosystem bump: FLR is a smaller-cap, single-chain asset.
    flr_weight = next(
        (h["weight_pct"] for h in holdings_detail if h["symbol"] == "FLR"), 0.0
    )
    if flr_weight > 50.0:
        score += 10.0
    elif flr_weight > 30.0:
        score += 5.0

    risk_score = max(0, min(100, int(round(score))))
    if risk_score < 40:
        risk_level = "low"
    elif risk_score < 70:
        risk_level = "medium"
    else:
        risk_level = "high"

    top = max(holdings_detail, key=lambda h: h["weight_pct"])
    concentrated = top["weight_pct"] >= 60.0 and n > 1

    rebalance: List[Dict[str, str]] = []
    for h in holdings_detail:
        symbol, weight = h["symbol"], h["weight_pct"]
        if weight >= 60.0:
            rebalance.append(
                {
                    "action": "decrease",
                    "symbol": symbol,
                    "reason": (
                        f"{symbol} weight is {weight:.1f}% — a concentrated position raises single-asset risk; "
                        "consider trimming gradually to diversify."
                    ),
                }
            )
        elif concentrated and symbol in ("BTC", "ETH") and symbol != top["symbol"]:
            rebalance.append(
                {
                    "action": "increase",
                    "symbol": symbol,
                    "reason": (
                        f"The portfolio is over-concentrated in {top['symbol']}; adding {symbol} "
                        "helps lower overall volatility."
                    ),
                }
            )
        else:
            rebalance.append(
                {
                    "action": "hold",
                    "symbol": symbol,
                    "reason": f"{symbol} weight at {weight:.1f}% is within a reasonable range — hold and monitor.",
                }
            )

    level_en = {"low": "low risk", "medium": "medium risk", "high": "high risk"}[risk_level]
    summary = (
        f"Portfolio total value is about ${total_value_usd:,.2f} across {n} assets. "
        f"The largest position is {top['symbol']} at {top['weight_pct']:.1f}%. "
        f"Under the concentration heuristic, the risk score is {risk_score}/100 ({level_en})."
    )
    if concentrated:
        summary += " Portfolio concentration is high; consider trimming single-asset exposure."
    else:
        summary += " Diversification looks reasonable; rebalance periodically per your risk profile."
    summary += " (Generated by the deterministic rule engine.)"

    return {
        "risk_score": risk_score,
        "risk_level": risk_level,
        "rebalance": rebalance,
        "summary": summary,
    }


def analyze_portfolio(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze a portfolio from the decrypted request payload.

    Expected payload keys:
      holdings:     {"BTC": 2.0, "ETH": 10.0, "FLR": 10000.0, ...}
      risk_profile: optional free-form string

    Returns the standard result dict (see module docstring). On any price
    failure returns an explicit error dict — never fake prices.
    """
    # --- 0. Validate holdings input ------------------------------------------
    holdings_raw = payload.get("holdings")
    if not isinstance(holdings_raw, dict) or not holdings_raw:
        return _error("payload.holdings must be a non-empty object")

    amounts: Dict[str, float] = {}
    for key, value in holdings_raw.items():
        symbol = str(key).upper()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return _error(f"holdings[{key!r}] must be a positive number, got {value!r}")
        amounts[symbol] = float(value)
    symbols = list(amounts.keys())

    # --- 1. Real prices from the FTSO price provider (never fabricated) ------
    try:
        prices_raw = price_provider.get_prices(symbols)
    except Exception as e:
        # Explicit error surfacing — NO silent fallback to fake prices.
        return _error(f"price provider failed: {e}")

    if not isinstance(prices_raw, dict):
        return _error("price provider returned invalid data (not an object)")
    prices: Dict[str, float] = {}
    for symbol in symbols:
        price = prices_raw.get(symbol)
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
            return _error(
                f"price provider returned invalid price for {symbol}: {price!r}"
            )
        prices[symbol] = float(price)

    try:
        price_source = str(price_provider.get_price_source())
    except Exception as e:
        return _error(f"price source unavailable: {e}")

    # --- 2. Deterministic portfolio math --------------------------------------
    total_value = sum(amounts[s] * prices[s] for s in symbols)
    holdings_detail: List[Dict[str, Any]] = []
    for symbol in symbols:
        value_usd = amounts[symbol] * prices[symbol]
        holdings_detail.append(
            {
                "symbol": symbol,
                "amount": amounts[symbol],
                "value_usd": round(value_usd, 2),
                "weight_pct": round(value_usd / total_value * 100.0, 2)
                if total_value > 0
                else 0.0,
            }
        )
    holdings_detail.sort(key=lambda h: h["value_usd"], reverse=True)
    total_value_usd = round(total_value, 2)

    # --- 3. Judgement: LLM when configured, deterministic rules otherwise -----
    risk_profile = str(payload.get("risk_profile") or "unspecified")
    llm_fallback_note = ""
    judgement: Dict[str, Any] | None = None
    analysis_mode = "rule-fallback"

    if llm.is_configured():
        try:
            judgement = llm.analyze(
                holdings=amounts,
                prices=prices,
                portfolio={
                    "total_value_usd": total_value_usd,
                    "price_source": price_source,
                    "holdings": [
                        {**h, "price_usd": prices[h["symbol"]]}
                        for h in holdings_detail
                    ],
                },
                risk_profile=risk_profile,
            )
            analysis_mode = "llm"
        except llm.LLMError:
            # LLM unreachable / misbehaving -> deterministic rule fallback.
            judgement = None

    if judgement is None:
        judgement = _rule_based_analysis(holdings_detail, total_value_usd, risk_profile)
        analysis_mode = "rule-fallback"
        if llm.is_configured():
            llm_fallback_note = " (LLM unavailable — fell back to the rule engine.)"

    return {
        "status": "success",
        "analysis_mode": analysis_mode,
        "price_source": price_source,
        "prices_used": prices,
        "total_value_usd": total_value_usd,
        "holdings": holdings_detail,
        "risk_score": judgement["risk_score"],
        "risk_level": judgement["risk_level"],
        "rebalance": judgement["rebalance"],
        "summary": judgement["summary"] + llm_fallback_note,
    }
