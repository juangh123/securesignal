"""Build web3 providers for HTTP or enclave-local Unix-socket RPC."""

from web3 import Web3


def make_provider(rpc_url: str, timeout: float):
    if rpc_url.startswith("ipc://"):
        return Web3.IPCProvider(rpc_url[len("ipc://") :])
    if rpc_url.startswith("http://") or rpc_url.startswith("https://"):
        return Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout})
    raise ValueError(
        "RPC_URL must use http://, https://, or ipc:// for an enclave-local "
        "Unix socket"
    )
