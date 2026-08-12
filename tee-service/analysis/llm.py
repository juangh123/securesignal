"""
LLM analysis client for the SecureSignal TEE service (OpenAI-compatible API).

Direct HTTP integration via ``requests`` — no ``openai`` SDK dependency.

Env config
----------
  LLM_API_KEY   required to enable LLM analysis; when absent the engine
                silently uses the deterministic rule engine instead.
  LLM_BASE_URL  default ``https://api.openai.com/v1`` (any OpenAI-compatible
                endpoint works, e.g. DeepSeek / Moonshot / a local mock).
  LLM_MODEL     default ``gpt-4o-mini``.
  LLM_TIMEOUT   optional request timeout in seconds, default 30.

Division of labour
------------------
The LLM only produces the *judgement* fields of the analysis result —
``risk_score``, ``risk_level``, ``rebalance`` advice and the English
``summary``. All portfolio math (USD values, weights) is computed
deterministically in ``analysis/engine.py`` and injected into the prompt as
ground truth, together with the FTSO prices actually used.

Contract
--------
``analyze(...)`` returns a validated dict::

    {
      "risk_score": int (0-100),
      "risk_level": "low" | "medium" | "high",
      "rebalance": [{"action": "increase" | "decrease" | "hold",
                     "symbol": str, "reason": str}, ...],
      "summary": str  # English analysis text
    }

Any failure (network, HTTP error, malformed response envelope, non-JSON or
schema-invalid content) triggers ONE retry; if that also fails, ``LLMError``
is raised and the engine falls back to the rule engine.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

import requests

__all__ = ["LLMError", "is_configured", "analyze"]

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT_SECONDS = 30.0

RISK_LEVELS = ("low", "medium", "high")
REBALANCE_ACTIONS = ("increase", "decrease", "hold")


class LLMError(RuntimeError):
    """LLM call or LLM-output validation failed (after one retry)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def is_configured() -> bool:
    """LLM analysis is enabled iff LLM_API_KEY is set (base URL/model have defaults)."""
    return bool(os.environ.get("LLM_API_KEY", "").strip())


SYSTEM_PROMPT = """You are a senior cryptocurrency portfolio analyst — the analysis core of SecureSignal, a privacy-first advisory service running inside a Trusted Execution Environment (TEE).

You will receive: the user's holdings, live Flare FTSO on-chain prices, the server-computed USD values and weights for each asset, total portfolio value, and the user's stated risk profile.

Your task: assess the portfolio's risk and give rebalancing advice.

Strict requirements:
1. Output ONLY one JSON object. No markdown code fences, comments, chain-of-thought, or any text outside JSON.
2. The JSON must contain exactly these four fields:
{
  "risk_score": <integer 0-100, higher = riskier>,
  "risk_level": <only "low" | "medium" | "high">,
  "rebalance": [ {"action": <only "increase" | "decrease" | "hold">, "symbol": <asset code, e.g. "BTC">, "reason": <one English sentence>} ],
  "summary": <an 80-200 character English analysis covering valuation, concentration, key risks and suggested actions>
}
3. risk_level must be consistent with risk_score: 0-39 = low, 40-69 = medium, 70-100 = high.
4. rebalance must cover every asset in the input portfolio, exactly one entry per asset.
5. All conclusions must be based on the real prices, values and weights in the input — never fabricate or extrapolate numbers."""


def _build_messages(
    portfolio: Dict[str, Any], risk_profile: str
) -> List[Dict[str, str]]:
    """
    Build the chat messages. ``portfolio`` is the engine-computed ground
    truth: {"total_value_usd", "price_source", "holdings": [{symbol, amount,
    price_usd, value_usd, weight_pct}]}.
    """
    user_data = {
        "note": "Prices below come from Flare FTSO (see price_source); values and weights are server-computed — use them as ground truth.",
        "price_source": portfolio.get("price_source"),
        "total_value_usd": portfolio.get("total_value_usd"),
        "holdings": portfolio.get("holdings"),
        "risk_profile": risk_profile,
    }
    user_content = (
        "Analyze the following crypto portfolio and output ONLY one JSON object strictly following the system prompt format:\n"
        + json.dumps(user_data, ensure_ascii=False, indent=2)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _request_completion(messages: List[Dict[str, str]], use_response_format: bool) -> str:
    """POST /chat/completions and return the assistant message content string."""
    base_url = os.environ.get("LLM_BASE_URL", "").strip() or DEFAULT_BASE_URL
    base_url = base_url.rstrip("/")
    model = os.environ.get("LLM_MODEL", "").strip() or DEFAULT_MODEL
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if not api_key:
        raise LLMError("LLM_API_KEY is not configured")
    try:
        timeout = float(os.environ.get("LLM_TIMEOUT", "").strip() or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
    if use_response_format:
        # OpenAI JSON mode; ignored by most compatible gateways, rejected by
        # a few — in that case we retry without it (HTTP 400 path below).
        payload["response_format"] = {"type": "json_object"}

    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise LLMError(f"LLM request failed: {e}") from e

    if resp.status_code != 200:
        raise LLMError(
            f"LLM API HTTP {resp.status_code}: {resp.text[:300]}",
            status_code=resp.status_code,
        )

    try:
        data = resp.json()
    except ValueError as e:
        raise LLMError(f"LLM response is not valid JSON envelope: {e}") from e
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"LLM response envelope missing choices[0].message.content: {e}") from e
    if not isinstance(content, str) or not content.strip():
        raise LLMError("LLM response content is empty")
    return content


def _extract_json(content: str) -> Dict[str, Any]:
    """Parse the model output as a JSON object, tolerating markdown fences."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json|JSON)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise LLMError(f"LLM output contains no JSON object: {text[:200]!r}")
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            raise LLMError(f"LLM output JSON parse failed: {e}") from e
    if not isinstance(obj, dict):
        raise LLMError("LLM output JSON is not an object")
    return obj


def _validate(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + normalize the LLM analysis dict. Raises LLMError if invalid."""
    # risk_score: int in [0, 100] (a numeric value is rounded to int)
    score = obj.get("risk_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise LLMError(f"risk_score must be a number, got {score!r}")
    score = int(round(float(score)))
    if not 0 <= score <= 100:
        raise LLMError(f"risk_score out of range 0-100: {score}")

    # risk_level: enum
    level = obj.get("risk_level")
    if not isinstance(level, str) or level.strip().lower() not in RISK_LEVELS:
        raise LLMError(f"risk_level must be one of {RISK_LEVELS}, got {level!r}")
    level = level.strip().lower()

    # rebalance: non-empty list of {action, symbol, reason}
    rebalance = obj.get("rebalance")
    if not isinstance(rebalance, list) or not rebalance:
        raise LLMError("rebalance must be a non-empty list")
    norm_rebalance: List[Dict[str, str]] = []
    for i, item in enumerate(rebalance):
        if not isinstance(item, dict):
            raise LLMError(f"rebalance[{i}] must be an object, got {item!r}")
        action = item.get("action")
        if not isinstance(action, str) or action.strip().lower() not in REBALANCE_ACTIONS:
            raise LLMError(
                f"rebalance[{i}].action must be one of {REBALANCE_ACTIONS}, got {action!r}"
            )
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise LLMError(f"rebalance[{i}].symbol must be a non-empty string")
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise LLMError(f"rebalance[{i}].reason must be a non-empty string")
        norm_rebalance.append(
            {
                "action": action.strip().lower(),
                "symbol": symbol.strip().upper(),
                "reason": reason.strip(),
            }
        )

    # summary: non-empty English analysis text
    summary = obj.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise LLMError("summary must be a non-empty string")

    return {
        "risk_score": score,
        "risk_level": level,
        "rebalance": norm_rebalance,
        "summary": summary.strip(),
    }


def analyze(
    holdings: Dict[str, float],
    prices: Dict[str, float],
    portfolio: Dict[str, Any],
    risk_profile: str,
) -> Dict[str, Any]:
    """
    Run LLM portfolio analysis. Returns the validated judgement dict.

    ``holdings`` / ``prices`` are the raw inputs; ``portfolio`` carries the
    engine-computed ground truth injected into the prompt. On any failure
    the request is retried ONCE; persistent failure raises LLMError.
    """
    if not is_configured():
        raise LLMError("LLM_API_KEY is not configured")

    messages = _build_messages(portfolio, risk_profile)
    use_response_format = True
    last_error: LLMError | None = None

    for attempt in (1, 2):
        try:
            content = _request_completion(messages, use_response_format)
            return _validate(_extract_json(content))
        except LLMError as e:
            last_error = e
            # Some compatible gateways reject response_format with HTTP 400 —
            # drop it for the retry.
            if e.status_code == 400:
                use_response_format = False

    raise LLMError(f"LLM analysis failed after 1 retry: {last_error}")
