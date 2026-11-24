"""Tests for prova.tools.base: the result a tool hands back, and how the model sees it."""

from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from prova.errors import ErrorCode
from prova.tools.base import ToolResult


def block_of(result: ToolResult, tool_use_id: str = "toolu_1") -> dict[str, Any]:
    return cast(dict[str, Any], result.to_tool_result_block(tool_use_id))


# --- Success ----------------------------------------------------------------


def test_ok_result_has_no_error_code() -> None:
    result = ToolResult.ok("read 3 lines")
    assert result.success is True
    assert result.error_code is None


def test_ok_block_carries_the_id_and_the_message_and_no_error_flag() -> None:
    block = block_of(ToolResult.ok("read 3 lines"), "toolu_42")
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "toolu_42"
    assert block["content"] == "read 3 lines"
    assert "is_error" not in block


def test_snapshot_is_what_the_model_sees_after_the_message() -> None:
    result = ToolResult.ok("src/a.py (12 lines)", snapshot="def f():\n    pass")
    assert block_of(result)["content"] == "src/a.py (12 lines)\n\ndef f():\n    pass"


def test_data_is_for_code_and_logs_not_for_the_model() -> None:
    result = ToolResult.ok("found 1 match", data={"internal_path": "/abs/secret/location"})
    assert result.data == {"internal_path": "/abs/secret/location"}
    assert "/abs/secret/location" not in block_of(result)["content"]


# --- Failure ----------------------------------------------------------------


def test_error_result_has_a_code_and_is_not_a_success() -> None:
    result = ToolResult.error(ErrorCode.NOT_FOUND, "no such file: a.py")
    assert result.success is False
    assert result.error_code is ErrorCode.NOT_FOUND


def test_error_block_is_flagged_and_names_the_code() -> None:
    block = block_of(ToolResult.error(ErrorCode.NOT_FOUND, "no such file: a.py"), "toolu_7")
    assert block["tool_use_id"] == "toolu_7"
    assert block["is_error"] is True
    assert block["content"] == "not_found: no such file: a.py"


# --- Invariants: a result cannot contradict itself --------------------------


def test_a_success_cannot_carry_an_error_code() -> None:
    with pytest.raises(ValueError, match="success"):
        ToolResult(success=True, message="ok", error_code=ErrorCode.INTERNAL)


def test_a_failure_must_carry_an_error_code() -> None:
    with pytest.raises(ValueError, match="error_code"):
        ToolResult(success=False, message="it broke")


def test_a_result_is_immutable() -> None:
    result = ToolResult.ok("done")
    with pytest.raises(FrozenInstanceError):
        result.message = "changed"  # pyright: ignore[reportAttributeAccessIssue]


# --- Notes: what a hook adds after a tool ran ----------------------------------


def test_a_result_has_no_notes_unless_a_hook_adds_them() -> None:
    assert ToolResult.ok("done").notes == ()


def test_notes_are_shown_after_the_message_and_the_snapshot() -> None:
    result = ToolResult.ok("edited a.py", snapshot="1│ x = 1").with_notes("[lint] clean", "[tests] 1 failed")
    assert result.model_text() == "edited a.py\n\n1│ x = 1\n\n[lint] clean\n[tests] 1 failed"


def test_notes_are_also_shown_after_an_error() -> None:
    result = ToolResult.error(ErrorCode.NOT_FOUND, "no such file").with_notes("[audit] logged")
    assert result.model_text() == "not_found: no such file\n\n[audit] logged"


def test_adding_notes_gives_a_new_result_and_leaves_the_old_one_alone() -> None:
    original = ToolResult.ok("done", data={"n": 1})
    noted = original.with_notes("[lint] clean")
    assert original.notes == () and noted.notes == ("[lint] clean",)
    assert noted.data == {"n": 1} and noted.success is True  # a note never changes the outcome


def test_adding_no_notes_changes_nothing() -> None:
    result = ToolResult.ok("done")
    assert result.with_notes() == result
