"""Relay enclave Unix-socket JSON-RPC to the parent over vsock."""

import os
import select
import socket
import threading

PARENT_CID = int(os.environ.get("AWS_NITRO_PARENT_CID", "3"))
PARENT_PORT = int(os.environ.get("RPC_VSOCK_PORT", "8500"))
RPC_SOCKET = os.environ.get("RPC_SOCKET", "/tmp/coston2-rpc.sock")
BUFFER_SIZE = 64 * 1024


def relay(left: socket.socket, right: socket.socket) -> None:
    sockets = [left, right]
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [], 30.0)
            if not readable:
                continue
            for source in readable:
                data = source.recv(BUFFER_SIZE)
                if not data:
                    return
                target = right if source is left else left
                target.sendall(data)
    finally:
        left.close()
        right.close()


def handle(connection: socket.socket) -> None:
    print("[rpc-bridge] client connected", flush=True)
    upstream = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    try:
        upstream.settimeout(5.0)
        upstream.connect((PARENT_CID, PARENT_PORT))
        print("[rpc-bridge] parent connected", flush=True)
    except OSError as exc:
        print(f"[rpc-bridge] parent connection failed: {exc}", flush=True)
        upstream.close()
        connection.close()
        return
    relay(connection, upstream)


def main() -> None:
    try:
        os.unlink(RPC_SOCKET)
    except FileNotFoundError:
        pass
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(RPC_SOCKET)
    listener.listen(64)
    print(
        f"[rpc-bridge] listening on {RPC_SOCKET} -> "
        f"vsock:{PARENT_CID}:{PARENT_PORT}",
        flush=True,
    )
    while True:
        connection, _ = listener.accept()
        threading.Thread(target=handle, args=(connection,), daemon=True).start()


if __name__ == "__main__":
    main()
