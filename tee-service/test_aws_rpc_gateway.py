"""Unit tests for the parent-side JSON-RPC gateway (no vsock involved)."""

import json

import aws_rpc_gateway


class _FakeSocket:
    def __init__(self, lines):
        self._lines = list(lines)
        self.sent = b""

    def makefile(self, _mode):
        lines = self._lines

        class _Reader:
            def readline(self, _limit=-1):
                return lines.pop(0) if lines else b""

        return _Reader()

    def sendall(self, data):
        self.sent += data

    def close(self):
        pass


def _line(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8") + b"\n"


def test_forwards_json_rpc_and_returns_upstream_bytes(monkeypatch):
    captured = {}

    def fake_forward(body):
        captured["body"] = body
        return b'{"jsonrpc":"2.0","id":7,"result":"0x72"}'

    monkeypatch.setattr(aws_rpc_gateway, "forward", fake_forward)
    connection = _FakeSocket(
        [_line({"jsonrpc": "2.0", "id": 7, "method": "eth_chainId", "params": []})]
    )

    aws_rpc_gateway.handle(connection)

    assert captured["body"].startswith(b'{"jsonrpc"')
    assert connection.sent.endswith(b"\n")
    assert json.loads(connection.sent.decode())["result"] == "0x72"


def test_upstream_failure_returns_json_rpc_error_with_id(monkeypatch):
    def boom(_body):
        raise OSError("connection refused")

    monkeypatch.setattr(aws_rpc_gateway, "forward", boom)
    connection = _FakeSocket(
        [_line({"jsonrpc": "2.0", "id": 9, "method": "eth_blockNumber", "params": []})]
    )

    aws_rpc_gateway.handle(connection)

    payload = json.loads(connection.sent.decode())
    assert payload["id"] == 9
    assert "upstream RPC failed" in payload["error"]["message"]


def test_error_response_shape():
    payload = json.loads(aws_rpc_gateway.error_response("boom", 3).decode())
    assert payload == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32000, "message": "boom"},
    }


def test_upstream_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv("RPC_UPSTREAM_TIMEOUT", "12.5")
    assert aws_rpc_gateway._upstream_timeout() == 12.5
    monkeypatch.setenv("RPC_UPSTREAM_TIMEOUT", "not-a-number")
    assert (
        aws_rpc_gateway._upstream_timeout()
        == aws_rpc_gateway.DEFAULT_UPSTREAM_TIMEOUT_SECONDS
    )
