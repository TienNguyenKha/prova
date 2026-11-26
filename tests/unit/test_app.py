"""Tests for prova.app: the composition root."""

import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from prova.app import build_app
from prova.config import Settings
from prova.errors import DuplicateNameError
from prova.llm.client import AnthropicClient
from prova.tools.base import Permission, RunContext
from tests.fakes import EchoTool, FakeLLM, text_message


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
    assert isinstance(app.llm, AnthropicClient)


def test_app_is_frozen(settings: Settings) -> None:
    app = build_app(settings, llm=FakeLLM())
    with pytest.raises(FrozenInstanceError):
        app.settings = settings  # pyright: ignore[reportAttributeAccessIssue]


# --- The tools ----------------------------------------------------------------


def test_build_app_registers_the_read_tools_in_name_order(settings: Settings) -> None:
    app = build_app(settings, llm=FakeLLM())
    assert [t["name"] for t in app.tools.to_api_tools({Permission.READ})] == [
        "list_dir",
        "read_file",
        "search_code",
        "submit_result",
    ]


def test_the_only_tools_that_write_are_create_file_and_edit_file(settings: Settings) -> None:
    app = build_app(settings, llm=FakeLLM())
    assert [t["name"] for t in app.tools.to_api_tools({Permission.WRITE})] == ["create_file", "edit_file"]


def test_the_only_tool_that_runs_commands_is_run_tests_and_none_uses_the_network(settings: Settings) -> None:
    app = build_app(settings, llm=FakeLLM())
    assert [t["name"] for t in app.tools.to_api_tools({Permission.EXEC})] == ["run_tests"]
    assert app.tools.to_api_tools({Permission.NETWORK}) == []


def test_run_tests_is_not_offered_to_a_run_without_the_exec_permission(settings: Settings) -> None:
    app = build_app(settings, llm=FakeLLM())
    offered = [t["name"] for t in app.tools.to_api_tools({Permission.READ, Permission.WRITE})]
    assert "run_tests" not in offered


def test_a_run_with_exec_but_no_sandbox_cannot_run_the_tests(settings: Settings, tmp_path: Path) -> None:
    app = build_app(settings, llm=FakeLLM())
    ctx = RunContext(workspace=tmp_path, permissions=frozenset({Permission.EXEC}))  # no sandbox on purpose
    result = app.tools.dispatch("run_tests", {}, ctx)
    assert not result.success and "sandbox" in result.message


def test_a_read_only_run_is_not_offered_a_writing_tool_and_cannot_call_one(
    settings: Settings, tmp_path: Path
) -> None:
    app = build_app(settings, llm=FakeLLM())
    ctx = RunContext(workspace=tmp_path, permissions=frozenset({Permission.READ}))

    offered = [t["name"] for t in app.tools.to_api_tools(ctx.permissions)]
    result = app.tools.dispatch("create_file", {"path": "x.txt", "content": "hi"}, ctx)

    assert "create_file" not in offered and "edit_file" not in offered
    assert not result.success and not (tmp_path / "x.txt").exists()


def test_the_tool_list_is_identical_every_time(settings: Settings) -> None:
    # A stable list keeps the prompt cache valid and benchmark runs reproducible.
    first = build_app(settings, llm=FakeLLM()).tools.to_api_tools({Permission.READ})
    second = build_app(settings, llm=FakeLLM()).tools.to_api_tools({Permission.READ})
    assert first == second


def test_every_app_gets_its_own_tools_and_nothing_is_shared_globally(settings: Settings) -> None:
    assert build_app(settings, llm=FakeLLM()).tools is not build_app(settings, llm=FakeLLM()).tools


def test_the_registered_tools_work_through_the_app(settings: Settings, tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    app = build_app(settings, llm=FakeLLM())

    result = app.tools.dispatch("list_dir", {}, RunContext(workspace=tmp_path))

    assert result.success
    assert result.snapshot == "notes.txt (5 bytes)"


# --- Tools that come from outside (MCP): registered before the registry is frozen -------------------


def test_extra_tools_are_registered_next_to_the_built_in_ones(settings: Settings) -> None:
    app = build_app(settings, llm=FakeLLM(), extra_tools=[EchoTool()])
    offered = [t["name"] for t in app.tools.to_api_tools({Permission.READ})]
    assert "echo" in offered and "read_file" in offered
    assert offered == sorted(offered)  # still in name order, so the tool list stays the same every time


def test_without_extra_tools_the_app_is_what_it_was(settings: Settings) -> None:
    plain = [t["name"] for t in build_app(settings, llm=FakeLLM()).tools.to_api_tools({Permission.READ})]
    explicit = build_app(settings, llm=FakeLLM(), extra_tools=[])
    same = [t["name"] for t in explicit.tools.to_api_tools({Permission.READ})]
    assert plain == same


def test_an_extra_tool_cannot_take_the_name_of_a_built_in_one(settings: Settings) -> None:
    class Impostor(EchoTool):
        name = "read_file"

    with pytest.raises(DuplicateNameError):
        build_app(settings, llm=FakeLLM(), extra_tools=[Impostor()])
