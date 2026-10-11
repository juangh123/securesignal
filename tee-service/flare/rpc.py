"""Build web3 providers for HTTP or enclave-local Unix-socket RPC."""

import urllib.parse

from web3 import Web3


def safe_rpc_label(rpc_url: str) -> str:
    """RPC URL for error messages: scheme + host[:port] only.

    Provider URLs can carry API keys in the path or query, and these messages
    end up in logs and in the signed result JSON, so never echo the full URL.
    """
    try:
        parsed = urllib.parse.urlsplit(rpc_url)
    except ValueError:
        return "the configured RPC endpoint"
    if not parsed.scheme or not parsed.hostname:
        return "the configured RPC endpoint"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def redact_rpc_url(text: str, rpc_url: str) -> str:
    """Strip an RPC URL (and its path/query fragments) from ``text``.

    HTTP clients often echo only the path/query part in their exception
    messages (``... url: /ext/C/rpc?apikey=SECRET``), so replacing the whole
    URL is not enough on its own.
    """
    safe = safe_rpc_label(rpc_url)
    cleaned = str(text).replace(rpc_url, safe)
    try:
        parsed = urllib.parse.urlsplit(rpc_url)
    except ValueError:
        return cleaned
    for fragment in (parsed.path, parsed.query, parsed.fragment):
        if fragment:
            cleaned = cleaned.replace(fragment, "")
    return cleaned


def make_provider(rpc_url: str, timeout: float):
    if rpc_url.startswith("ipc://"):
        return Web3.IPCProvider(rpc_url[len("ipc://") :])
    if rpc_url.startswith("http://") or rpc_url.startswith("https://"):
        return Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout})
    raise ValueError(
        "RPC_URL must use http://, https://, or ipc:// for an enclave-local "
        "Unix socket"
    )
