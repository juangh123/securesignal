"""Shared bidirectional relay used by the Nitro Enclave vsock bridges.

``aws_vsock_proxy`` (parent -> enclave HTTP) and ``aws_vsock_rpc_bridge``
(enclave -> parent JSON-RPC) both need the same byte pump between two sockets,
so it lives here instead of being duplicated in both entry points.
"""

import select
import socket

BUFFER_SIZE = 64 * 1024
# Idle timeout keeps the loop able to observe a half-closed peer even when no
# data flows for a while.
SELECT_TIMEOUT_SECONDS = 30.0


def relay(left: socket.socket, right: socket.socket) -> None:
    """Pump bytes in both directions until either side closes.

    Both sockets are always closed on the way out, including when the relay
    exits because of an error raised by ``sendall``/``recv``.
    """
    sockets = [left, right]
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [], SELECT_TIMEOUT_SECONDS)
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
