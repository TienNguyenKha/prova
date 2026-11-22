"""Shared fixtures for every test."""

import socket
from pathlib import Path
from typing import NoReturn

import pytest

from prova.config import Settings, load_settings


@pytest.fixture(autouse=True)
def _block_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Block every network connection, except in tests marked `live`."""
    if request.node.get_closest_marker("live") is not None:
        return

    def guard(self: socket.socket, *args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("network blocked: unit tests must not use the network (use marker `live`)")

    monkeypatch.setattr(socket.socket, "connect", guard)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings built from a fake environ and a tmp .env path: never touches the real .env or os.environ."""
    return load_settings(tmp_path / ".env", environ={"ANTHROPIC_API_KEY": "sk-ant-test-fixture"})
