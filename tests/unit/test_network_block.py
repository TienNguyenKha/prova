"""Check that conftest.py blocks the network for every test without the `live` marker."""

import socket

import pytest


def test_socket_connect_is_blocked() -> None:
    # Port 9 on localhost: if NOT blocked, the OS answers ConnectionRefusedError
    # (nothing listens there), so this test still fails fast instead of hanging.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(
        RuntimeError, match="network blocked"
    ):
        s.connect(("127.0.0.1", 9))
