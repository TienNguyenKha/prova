"""Tests for prova.tools.fs.ReadFileTool: confined to the workspace, cuts long files out loud."""

import os
from pathlib import Path
from typing import Any, cast

import pytest

from prova.errors import ErrorCode
from prova.registry import Registry
from prova.tools.base import Permission, RunContext, Tool, ToolResult
from prova.tools.fs import ReadFileTool
from prova.tools.registry import ToolRegistry
from prova.tools.search import SearchCodeTool


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A workspace, plus files OUTSIDE it that must never be reachable."""
    work = tmp_path / "work"
    (work / "sub").mkdir(parents=True)
    (work / "hello.txt").write_text("hello\nworld\n", encoding="utf-8")
    (work / "sub" / "inner.txt").write_text("inner", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    (tmp_path / "work_evil").mkdir()  # a sibling whose name starts with "work": the classic prefix-check bug
    (tmp_path / "work_evil" / "loot.txt").write_text("LOOT", encoding="utf-8")
    return work


def make_registry(tool: Tool) -> ToolRegistry:
    registry = Registry[Tool]("tool")
    registry.register(tool)
    registry.freeze()
    return ToolRegistry(registry)


def run(root: Path, path: str, *, max_bytes: int = 20_000) -> ToolResult:
    """Call the tool the way the agent loop does: through the registry, with bugs raised (strict)."""
    ctx = RunContext(workspace=root, strict=True)
    return make_registry(ReadFileTool(max_bytes=max_bytes)).dispatch("read_file", {"path": path}, ctx)


# --- Success ----------------------------------------------------------------


def test_reads_a_file_and_says_how_big_it_is(root: Path) -> None:
    result = run(root, "hello.txt")
    assert result.success
    assert result.snapshot == "1│ hello\n2│ world"
    assert "hello.txt" in result.message
    assert "2 lines" in result.message
    assert result.data == {"path": "hello.txt", "bytes": 12, "truncated": False}


def test_reads_a_file_in_a_subdirectory(root: Path) -> None:
    assert run(root, "sub/inner.txt").snapshot == "1│ inner"


def test_absolute_path_inside_the_workspace_is_allowed(root: Path) -> None:
    assert run(root, str(root / "hello.txt")).snapshot == "1│ hello\n2│ world"


def test_an_empty_file_is_a_success_with_no_snapshot(root: Path) -> None:
    (root / "empty.txt").write_text("", encoding="utf-8")
    result = run(root, "empty.txt")
    assert result.success
    assert result.snapshot is None
    assert "empty" in result.message


# --- Long files are cut, and the cut is stated ------------------------------


def test_a_long_file_is_cut_and_the_result_says_so(root: Path) -> None:
    (root / "long.txt").write_text("x" * 250, encoding="utf-8")
    result = run(root, "long.txt", max_bytes=100)

    assert result.success
    assert result.snapshot is not None
    assert result.snapshot.startswith("1│ " + "x" * 100)
    assert "x" * 101 not in result.snapshot  # nothing beyond the limit leaks through
    assert "cut" in result.snapshot and "100 of 250" in result.snapshot  # the model is told
    assert "cut" in result.message
    assert result.data == {"path": "long.txt", "bytes": 250, "truncated": True}


def test_a_file_exactly_at_the_limit_is_not_cut(root: Path) -> None:
    (root / "exact.txt").write_text("y" * 100, encoding="utf-8")
    result = run(root, "exact.txt", max_bytes=100)
    assert result.snapshot == "1│ " + "y" * 100
    assert cast(dict[str, Any], result.data)["truncated"] is False


def test_cutting_inside_a_multibyte_character_is_not_an_encoding_error(root: Path) -> None:
    (root / "accents.txt").write_text("é" * 100, encoding="utf-8")  # 2 bytes per character, 200 bytes
    result = run(root, "accents.txt", max_bytes=101)  # the limit falls in the middle of a character

    assert result.success
    assert result.snapshot is not None
    assert result.snapshot.startswith("1│ " + "é" * 50)
    assert "100 of 200" in result.snapshot  # only the complete characters count as shown


# --- The workspace is a hard boundary ---------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../secret.txt",
        "sub/../../secret.txt",  # starts inside, climbs out
        "../work_evil/loot.txt",  # sibling directory sharing the "work" prefix
    ],
)
def test_paths_that_climb_out_of_the_workspace_are_denied(root: Path, path: str) -> None:
    result = run(root, path)
    assert result.error_code is ErrorCode.PERMISSION_DENIED
    assert "TOP SECRET" not in result.message and "LOOT" not in result.message


def test_absolute_path_outside_the_workspace_is_denied(root: Path) -> None:
    assert run(root, str(root.parent / "secret.txt")).error_code is ErrorCode.PERMISSION_DENIED


def test_symlink_pointing_outside_the_workspace_is_denied(root: Path) -> None:
    (root / "link.txt").symlink_to(root.parent / "secret.txt")
    assert run(root, "link.txt").error_code is ErrorCode.PERMISSION_DENIED


# --- Failures the model can understand and fix ------------------------------


def test_missing_file_is_not_found_and_names_the_path(root: Path) -> None:
    result = run(root, "missing.txt")
    assert result.error_code is ErrorCode.NOT_FOUND
    assert "missing.txt" in result.message


def test_a_directory_is_invalid_args(root: Path) -> None:
    result = run(root, "sub")
    assert result.error_code is ErrorCode.INVALID_ARGS
    assert "directory" in result.message


def test_a_file_that_is_not_utf8_is_invalid_args(root: Path) -> None:
    (root / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")
    result = run(root, "blob.bin")
    assert result.error_code is ErrorCode.INVALID_ARGS
    assert "UTF-8" in result.message


def test_a_binary_file_that_happens_to_be_valid_utf8_is_still_refused(root: Path) -> None:
    (root / "zeros.dat").write_bytes(b"\x00\x00\x00\x00")  # valid UTF-8, obviously not text
    result = run(root, "zeros.dat")
    assert result.error_code is ErrorCode.INVALID_ARGS
    assert "binary" in result.message


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX named pipes")
def test_a_file_that_is_not_a_regular_file_is_refused_without_opening_it(root: Path) -> None:
    os.mkfifo(root / "pipe")  # opening a pipe with no writer would block forever
    result = run(root, "pipe")
    assert result.error_code is ErrorCode.INVALID_ARGS
    assert "regular file" in result.message


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root ignores file permissions")
def test_an_unreadable_file_is_permission_denied_not_a_crash(root: Path) -> None:
    locked = root / "locked.txt"
    locked.write_text("hidden", encoding="utf-8")
    locked.chmod(0)
    try:
        result = run(root, "locked.txt")
    finally:
        locked.chmod(0o600)  # let pytest clean up the temp directory
    assert result.error_code is ErrorCode.PERMISSION_DENIED
    assert "hidden" not in result.message


# --- How the model sees the tool --------------------------------------------


def test_the_tool_is_read_only_and_its_schema_comes_from_the_args_model(root: Path) -> None:
    tool = ReadFileTool()
    assert tool.permission is Permission.READ

    (listed,) = make_registry(tool).to_api_tools({Permission.READ})
    schema = cast(dict[str, Any], listed["input_schema"])
    assert schema["required"] == ["path"]
    assert "workspace" in schema["properties"]["path"]["description"]  # tells the model what `path` means


def test_unknown_arguments_are_rejected(root: Path) -> None:
    ctx = RunContext(workspace=root, strict=True)
    result = make_registry(ReadFileTool()).dispatch("read_file", {"path": "hello.txt", "lines": 5}, ctx)
    assert result.error_code is ErrorCode.INVALID_ARGS
    assert "lines" in result.message


# --- Line numbers: the model needs them to quote a place and to edit it ------


def test_every_line_is_numbered_from_one(root: Path) -> None:
    (root / "three.txt").write_text("a\n\nc", encoding="utf-8")  # an empty line, no final newline
    assert run(root, "three.txt").snapshot == "1│ a\n2│ \n3│ c"


def test_numbers_are_right_aligned_to_the_widest_one(root: Path) -> None:
    (root / "ten.txt").write_text("".join(f"line{n}\n" for n in range(1, 11)), encoding="utf-8")
    snapshot = run(root, "ten.txt").snapshot
    assert snapshot is not None
    lines = snapshot.split("\n")
    assert lines[0] == " 1│ line1"
    assert lines[9] == "10│ line10"


def test_a_cut_file_keeps_its_numbers_and_the_cut_note(root: Path) -> None:
    (root / "rows.txt").write_text("".join(f"row{n}\n" for n in range(1, 101)), encoding="utf-8")
    snapshot = run(root, "rows.txt", max_bytes=30).snapshot
    assert snapshot is not None
    assert snapshot.startswith("1│ row1\n2│ row2")
    assert "cut" in snapshot and "30 of" in snapshot


def test_line_numbers_agree_with_search_code_even_with_odd_line_breaks(root: Path) -> None:
    # str.splitlines() also breaks on form feed; search_code counts that way, so read_file must too.
    (root / "odd.txt").write_text("first\x0csecond needle\nthird\n", encoding="utf-8")
    ctx = RunContext(workspace=root, strict=True)
    registry = Registry[Tool]("tool")
    registry.register(ReadFileTool())
    registry.register(SearchCodeTool())
    registry.freeze()
    tools = ToolRegistry(registry)

    found = tools.dispatch("search_code", {"pattern": "needle"}, ctx)
    assert found.snapshot is not None and "odd.txt:2:" in found.snapshot  # the line search_code reports

    read = tools.dispatch("read_file", {"path": "odd.txt"}, ctx).snapshot
    assert read is not None and "2│ second needle" in read


def test_the_description_tells_the_model_the_numbers_are_not_part_of_the_file() -> None:
    description = ReadFileTool().description
    assert "line number" in description and "not part of the file" in description
