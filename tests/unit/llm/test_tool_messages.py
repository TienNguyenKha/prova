"""Tests for prova.llm.tool_messages: turn the model's tool_use blocks into one tool_result message."""

from collections.abc import Mapping
from typing import Any, cast

import pytest
from anthropic.types import MessageParam

from prova.errors import ErrorCode, ToolError
from prova.llm.tool_messages import build_tool_results
from tests.fakes import text_message, tool_use_message


def echo(name: str, tool_input: Mapping[str, object]) -> str:
    """Fake tool runner: returns a string that shows exactly what it was called with."""
    return f"{name}({dict(tool_input)})"


def result_blocks(message: MessageParam | None) -> list[dict[str, Any]]:
    assert message is not None
    content = message["content"]
    assert isinstance(content, list)
    return cast(list[dict[str, Any]], content)


# --- Success paths ----------------------------------------------------------


def test_two_tool_uses_become_two_tool_results_in_one_user_message() -> None:
    response = tool_use_message(
        ("toolu_1", "read_file", {"path": "a.txt"}),
        ("toolu_2", "read_file", {"path": "b.txt"}),
    )

    message = build_tool_results(response.content, echo)

    assert message is not None
    assert message["role"] == "user"  # all results go in ONE user message
    blocks = result_blocks(message)
    assert [b["type"] for b in blocks] == ["tool_result", "tool_result"]
    assert [b["tool_use_id"] for b in blocks] == ["toolu_1", "toolu_2"]  # same ids, same order
    assert blocks[0]["content"] == "read_file({'path': 'a.txt'})"
    assert blocks[1]["content"] == "read_file({'path': 'b.txt'})"


def test_response_without_tool_use_returns_none() -> None:
    assert build_tool_results(text_message("just text").content, echo) is None


def test_text_blocks_next_to_tool_use_are_ignored() -> None:
    response = tool_use_message(("toolu_1", "read_file", {"path": "a.txt"}), text="Let me look.")
    blocks = result_blocks(build_tool_results(response.content, echo))
    assert [b["tool_use_id"] for b in blocks] == ["toolu_1"]


def test_successful_result_has_no_is_error_flag() -> None:
    response = tool_use_message(("toolu_1", "read_file", {"path": "a.txt"}))
    (block,) = result_blocks(build_tool_results(response.content, echo))
    assert "is_error" not in block


# --- Error paths ------------------------------------------------------------


def test_tool_error_becomes_an_error_result_not_an_exception() -> None:
    def failing(name: str, tool_input: Mapping[str, object]) -> str:
        raise ToolError(ErrorCode.NOT_FOUND, "no such file: missing.txt")

    response = tool_use_message(("toolu_1", "read_file", {"path": "missing.txt"}))
    (block,) = result_blocks(build_tool_results(response.content, failing))

    assert block["is_error"] is True
    assert block["tool_use_id"] == "toolu_1"
    assert "no such file: missing.txt" in block["content"]  # the model can read why it failed


def test_one_failing_tool_does_not_hide_the_others() -> None:
    def runner(name: str, tool_input: Mapping[str, object]) -> str:
        if tool_input["path"] == "bad.txt":
            raise ToolError(ErrorCode.NOT_FOUND, "no such file")
        return "ok"

    response = tool_use_message(
        ("toolu_1", "read_file", {"path": "bad.txt"}),
        ("toolu_2", "read_file", {"path": "good.txt"}),
    )
    blocks = result_blocks(build_tool_results(response.content, runner))

    assert blocks[0]["is_error"] is True
    assert blocks[1]["content"] == "ok"
    assert "is_error" not in blocks[1]


def test_unexpected_exception_is_a_bug_and_propagates() -> None:
    def buggy(name: str, tool_input: Mapping[str, object]) -> str:
        raise RuntimeError("bug in the tool")

    response = tool_use_message(("toolu_1", "read_file", {"path": "a.txt"}))
    with pytest.raises(RuntimeError, match="bug in the tool"):
        build_tool_results(response.content, buggy)
