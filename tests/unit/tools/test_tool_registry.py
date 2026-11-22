"""Tests for prova.tools.registry: list tools for the API, and dispatch the model's calls without raising."""

import logging
from pathlib import Path
from typing import Any, cast

import pytest

from prova.errors import ErrorCode, RegistryError
from prova.registry import Registry
from prova.tools.base import Permission, RunContext, Tool
from prova.tools.registry import ToolRegistry
from tests.fakes import BuggyTool, EchoTool, FailingTool, WriterTool, make_tool_registry

make = make_tool_registry


@pytest.fixture
def ctx(tmp_path: Path) -> RunContext:
    return RunContext(workspace=tmp_path)


# --- to_api_tools -----------------------------------------------------------


def test_api_tools_are_sorted_by_name_and_carry_description_and_schema() -> None:
    tools = make(WriterTool(), EchoTool(), FailingTool()).to_api_tools({Permission.READ, Permission.WRITE})

    assert [t["name"] for t in tools] == ["echo", "failing", "write_thing"]
    echo = cast(dict[str, Any], tools[0])
    assert echo["description"] == "Repeat a text."
    assert set(echo["input_schema"]["properties"]) == {"text", "times"}  # generated from args_model
    assert echo["input_schema"]["required"] == ["text"]


def test_api_tools_only_include_allowed_permissions() -> None:
    registry = make(EchoTool(), WriterTool())
    assert [t["name"] for t in registry.to_api_tools({Permission.READ})] == ["echo"]
    assert registry.to_api_tools(set()) == []


def test_api_tools_are_identical_between_calls() -> None:
    # A stable list keeps the prompt cache valid and benchmark runs reproducible.
    registry = make(WriterTool(), EchoTool())
    allowed = {Permission.READ, Permission.WRITE}
    assert registry.to_api_tools(allowed) == registry.to_api_tools(allowed)


# --- dispatch: success ------------------------------------------------------


def test_dispatch_runs_the_tool_with_validated_args(ctx: RunContext) -> None:
    result = make(EchoTool()).dispatch("echo", {"text": "ab", "times": 2}, ctx)
    assert result.success
    assert result.message == "abab"


# --- dispatch: the model's mistakes come back as data, never as exceptions --


def test_unknown_tool_is_not_found_and_lists_what_exists(ctx: RunContext) -> None:
    result = make(EchoTool(), FailingTool()).dispatch("web_search", {}, ctx)
    assert result.error_code is ErrorCode.NOT_FOUND
    assert "web_search" in result.message
    assert "echo" in result.message and "failing" in result.message


def test_unknown_tool_suggests_a_close_name(ctx: RunContext) -> None:
    result = make(EchoTool(), WriterTool()).dispatch("write_thin", {}, ctx)
    assert result.error_code is ErrorCode.NOT_FOUND
    assert "Did you mean: write_thing" in result.message  # not just listed among the available tools


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        ({}, "text"),  # missing
        ({"text": "a", "times": "many"}, "times"),  # wrong type
        ({"text": "a", "colour": "red"}, "colour"),  # unknown field (extra="forbid")
    ],
)
def test_bad_args_are_invalid_args_and_name_the_field(
    ctx: RunContext, raw: dict[str, object], field: str
) -> None:
    tool = EchoTool()
    result = make(tool).dispatch("echo", raw, ctx)
    assert result.error_code is ErrorCode.INVALID_ARGS
    assert field in result.message
    assert tool.calls == 0  # the tool never ran with bad input


def test_input_that_is_not_an_object_is_invalid_args(ctx: RunContext) -> None:
    result = make(EchoTool()).dispatch("echo", "just a string", ctx)
    assert result.error_code is ErrorCode.INVALID_ARGS


def test_a_tool_error_keeps_its_code_and_message(ctx: RunContext) -> None:
    result = make(FailingTool()).dispatch("failing", {"text": "x"}, ctx)
    assert result.error_code is ErrorCode.NOT_FOUND
    assert result.message == "no such thing: x"


# --- dispatch: the permission check is where the rule is enforced -----------


def test_a_run_may_only_read_unless_it_is_given_more(tmp_path: Path) -> None:
    assert RunContext(workspace=tmp_path).permissions == frozenset({Permission.READ})


def test_a_tool_outside_the_runs_permissions_is_denied_and_never_runs(ctx: RunContext) -> None:
    # The model was never shown this tool, but it can still ask for it by name (hallucination or injection).
    writer = WriterTool()
    result = make(writer).dispatch("write_thing", {"text": "x"}, ctx)
    assert result.error_code is ErrorCode.PERMISSION_DENIED
    assert "write_thing" in result.message
    assert writer.ran == 0


def test_the_permission_is_checked_before_the_arguments(ctx: RunContext) -> None:
    # A denied tool must not reveal anything about its arguments, so the model cannot probe it.
    result = make(WriterTool()).dispatch("write_thing", {}, ctx)  # the arguments are also wrong
    assert result.error_code is ErrorCode.PERMISSION_DENIED


def test_the_same_tool_runs_when_the_run_has_the_permission(tmp_path: Path) -> None:
    writer = WriterTool()
    allowed = RunContext(workspace=tmp_path, permissions=frozenset({Permission.READ, Permission.WRITE}))
    assert make(writer).dispatch("write_thing", {"text": "x"}, allowed).success
    assert writer.ran == 1


# --- dispatch: bugs in our own tools ----------------------------------------


def test_unexpected_exception_becomes_internal_and_is_logged_with_traceback(
    ctx: RunContext, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        result = make(BuggyTool()).dispatch("buggy", {"text": "x"}, ctx)

    assert result.error_code is ErrorCode.INTERNAL
    assert "buggy" in result.message
    assert "secret-detail" not in result.message  # the model never sees the exception text
    assert "secret-detail" in caplog.text  # but the log has the full traceback
    assert "Traceback" in caplog.text


def test_in_strict_mode_an_unexpected_exception_is_raised_again(tmp_path: Path) -> None:
    strict_ctx = RunContext(workspace=tmp_path, strict=True)
    with pytest.raises(RuntimeError, match="secret-detail"):
        make(BuggyTool()).dispatch("buggy", {"text": "x"}, strict_ctx)


# --- The registry must be frozen before it is used ---------------------------


def test_a_tool_registry_refuses_a_registry_that_is_not_frozen() -> None:
    registry = Registry[Tool]("tool")
    registry.register(EchoTool())
    with pytest.raises(RegistryError, match="frozen"):
        ToolRegistry(registry)  # forgetting to freeze would let tools appear after the app is built


# --- is_denied: the one definition of "this run may not use that tool" -------


def test_a_tool_outside_the_runs_permissions_is_denied(ctx: RunContext) -> None:
    tools = make(EchoTool(), WriterTool())
    assert tools.is_denied("write_thing", ctx) is True
    assert tools.is_denied("echo", ctx) is False


def test_a_tool_the_run_has_the_permission_for_is_not_denied(tmp_path: Path) -> None:
    allowed = RunContext(workspace=tmp_path, permissions=frozenset({Permission.READ, Permission.WRITE}))
    assert make(WriterTool()).is_denied("write_thing", allowed) is False


def test_an_unknown_tool_is_not_a_permission_matter(ctx: RunContext) -> None:
    # dispatch reports it as NOT_FOUND; "denied" would mislead whoever reads the log
    assert make(EchoTool()).is_denied("no_such_tool", ctx) is False
