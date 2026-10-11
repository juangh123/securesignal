"""Relay AF_VSOCK connections from the parent to the local FastAPI port."""

import os
import socket
import threading

from vsock_relay import relay

ENCLAVE_PORT = int(os.environ.get("ENCLAVE_PORT", "8000"))
APP_SOCKET = os.environ.get("APP_SOCKET", "/tmp/securesignal.sock")


def handle(connection: socket.socket) -> None:
    upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        upstream.settimeout(5.0)
        upstream.connect(APP_SOCKET)
    except OSError as exc:
        print(f"[vsock-proxy] upstream Unix socket failed: {exc}", flush=True)
        upstream.close()
        connection.close()
        return
    relay(connection, upstream)


def main() -> None:
    listener = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((socket.VMADDR_CID_ANY, ENCLAVE_PORT))
    listener.listen(64)
    print(
        f"[vsock-proxy] listening on vsock:{ENCLAVE_PORT} -> "
        f"{APP_SOCKET}",
        flush=True,
    )
    while True:
        connection, _ = listener.accept()
        threading.Thread(target=handle, args=(connection,), daemon=True).start()


if __name__ == "__main__":
    main()
