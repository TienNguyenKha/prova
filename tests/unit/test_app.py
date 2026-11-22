"""Tests for prova.app: the composition root."""

import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import anthropic
import pytest

from prova.app import build_app
from prova.config import Settings
from tests.fakes import FakeLLM, text_message


def test_importing_prova_needs_no_env_and_no_key(tmp_path: Path) -> None:
    # Fresh interpreter, empty environment, unrelated cwd: importing must not read env or fail.
    result = subprocess.run(  # noqa: S603  (fixed argv, no shell)
        [sys.executable, "-c", "import prova, prova.app"],
        env={},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_build_app_uses_the_injected_llm_and_makes_no_call(settings: Settings) -> None:
    fake = FakeLLM([text_message("hi")])
    app = build_app(settings, llm=fake)

    assert app.llm is fake
    assert app.settings is settings
    assert fake.messages.calls == []  # building the app must not call the API


def test_build_app_without_llm_builds_the_real_client_without_network(settings: Settings) -> None:
    # The network is blocked by conftest: constructing the SDK client must not connect.
    app = build_app(settings)
    assert isinstance(app.llm, anthropic.Anthropic)


def test_app_is_frozen(settings: Settings) -> None:
    app = build_app(settings, llm=FakeLLM())
    with pytest.raises(FrozenInstanceError):
        app.settings = settings  # pyright: ignore[reportAttributeAccessIssue]


# TODO(M4): when App gains a ToolRegistry, assert that build_app freezes it.
