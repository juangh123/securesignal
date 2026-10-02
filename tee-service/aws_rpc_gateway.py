"""Parent-side JSON-RPC gateway from Nitro Enclaves vsock to HTTPS RPC."""

import json
import os
import socket
import threading
import urllib.error
import urllib.request

LISTEN_PORT = int(os.environ.get("RPC_VSOCK_PORT", "8500"))
UPSTREAM_URL = os.environ.get(
    "RPC_UPSTREAM_URL",
    "https://coston2-api.flare.network/ext/C/rpc",
)
MAX_REQUEST_BYTES = 4 * 1024 * 1024


def forward(request_body: bytes) -> bytes:
    request = urllib.request.Request(
        UPSTREAM_URL,
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30.0) as response:
        return response.read()


def error_response(message: str, request_id=None) -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32000, "message": message},
        }
    ).encode("utf-8")


def handle(connection: socket.socket) -> None:
    print("[rpc-gateway] enclave connected", flush=True)
    try:
        reader = connection.makefile("rb")
        while True:
            try:
                line = reader.readline(MAX_REQUEST_BYTES + 1)
            except OSError:
                return
            if not line:
                return
            if len(line) > MAX_REQUEST_BYTES:
                connection.sendall(
                    error_response("JSON-RPC request is too large") + b"\n"
                )
                return
            request_id = None
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    request_id = parsed.get("id")
                response = forward(line.strip())
            except (ValueError, OSError, urllib.error.URLError) as exc:
                print(f"[rpc-gateway] upstream failed: {exc}", flush=True)
                response = error_response(f"upstream RPC failed: {exc}", request_id)
            connection.sendall(response.rstrip(b"\r\n") + b"\n")
    finally:
        connection.close()


def main() -> None:
    listener = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((socket.VMADDR_CID_ANY, LISTEN_PORT))
    listener.listen(64)
    print(
        f"[rpc-gateway] listening on vsock:{LISTEN_PORT} -> {UPSTREAM_URL}",
        flush=True,
    )
    while True:
        connection, _ = listener.accept()
        threading.Thread(
            target=handle,
            args=(connection,),
            daemon=True,
        ).start()


if __name__ == "__main__":
    main()
