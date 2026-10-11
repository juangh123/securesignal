"""Unit tests for the shared vsock relay helper."""

import socket
import threading

import pytest

from vsock_relay import relay


def _read_exactly(sock: socket.socket, size: int) -> bytes:
    chunks = b""
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            break
        chunks += chunk
    return chunks


def test_relay_forwards_both_directions_and_closes_peers():
    # socketpair gives us two independent streams: one pair plays the relay
    # endpoints, the other plays both peers.
    left, left_peer = socket.socketpair()
    right, right_peer = socket.socketpair()
    left_peer.settimeout(5.0)
    right_peer.settimeout(5.0)

    worker = threading.Thread(target=relay, args=(left, right), daemon=True)
    worker.start()
    try:
        left_peer.sendall(b"ping")
        assert _read_exactly(right_peer, 4) == b"ping"

        right_peer.sendall(b"pong")
        assert _read_exactly(left_peer, 4) == b"pong"

        # Closing one peer must end the relay and close both endpoints.
        left_peer.close()
        worker.join(timeout=5.0)
        assert not worker.is_alive()
        assert right_peer.recv(64) == b""
    finally:
        for sock in (left_peer, right_peer):
            try:
                sock.close()
            except OSError:
                pass


def test_relay_with_closed_endpoint_raises_and_closes():
    left, left_peer = socket.socketpair()
    right, right_peer = socket.socketpair()
    # A closed endpoint must surface as an error instead of spinning forever.
    right.close()

    # Windows raises ValueError from select() for a closed socket, POSIX an
    # OSError; both must surface instead of spinning forever.
    with pytest.raises((OSError, ValueError)):
        relay(left, right)

    left_peer.close()
    right_peer.close()
