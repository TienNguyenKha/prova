"""Tests for prova.tools.fs.ListDirTool: stable order, cuts long listings out loud, stays in the workspace."""

import os
from pathlib import Path
from typing import Any, cast

import pytest

from prova.errors import ErrorCode
from prova.registry import Registry
from prova.tools.base import Permission, RunContext, Tool, ToolResult
from prova.tools.fs import ListDirTool
from prova.tools.registry import ToolRegistry


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A workspace, plus things OUTSIDE it that must never be reachable."""
    work = tmp_path / "work"
    (work / "sub").mkdir(parents=True)
    (work / "a.py").write_text("x=1\n", encoding="utf-8")  # 4 bytes
    (work / "b.txt").write_text("hello", encoding="utf-8")  # 5 bytes
    (work / ".hidden").write_text("secret", encoding="utf-8")  # 6 bytes
    (work / "sub" / "inner.txt").write_text("inner", encoding="utf-8")
    (tmp_path / "outside_dir").mkdir()
    (tmp_path / "outside_dir" / "private.txt").write_text("PRIVATE", encoding="utf-8")
    (tmp_path / "work_evil").mkdir()  # a sibling whose name starts with "work"
    return work


def make_registry(tool: Tool) -> ToolRegistry:
    registry = Registry[Tool]("tool")
    registry.register(tool)
    registry.freeze()
    return ToolRegistry(registry)


def run(root: Path, args: dict[str, object] | None = None, *, max_entries: int = 200) -> ToolResult:
    """Call the tool the way the agent loop does: through the registry, with bugs raised (strict)."""
    ctx = RunContext(workspace=root, strict=True)
    return make_registry(ListDirTool(max_entries=max_entries)).dispatch("list_dir", args or {}, ctx)


# --- Success ----------------------------------------------------------------


def test_lists_the_workspace_root_by_default_sorted_by_name(root: Path) -> None:
    result = run(root)
    assert result.success
    assert result.snapshot == ".hidden (6 bytes)\na.py (4 bytes)\nb.txt (5 bytes)\nsub/"
    assert "4 entries" in result.message
    assert result.data == {"path": ".", "entries": 4, "truncated": False}


def test_lists_a_subdirectory(root: Path) -> None:
    result = run(root, {"path": "sub"})
    assert result.snapshot == "inner.txt (5 bytes)"
    assert "1 entry" in result.message  # singular, not "1 entries"


def test_the_order_does_not_depend_on_creation_order(root: Path) -> None:
    for name in ("z.txt", "m.txt", "c.txt"):  # created in an order that is not alphabetical
        (root / "sub" / name).write_text("", encoding="utf-8")
    lines = (run(root, {"path": "sub"}).snapshot or "").splitlines()
    assert [line.split(" ")[0] for line in lines] == ["c.txt", "inner.txt", "m.txt", "z.txt"]


def test_a_one_byte_file_says_byte_not_bytes(root: Path) -> None:
    (root / "sub" / "z").write_text("z", encoding="utf-8")
    assert "z (1 byte)" in (run(root, {"path": "sub"}).snapshot or "")


def test_a_symlink_is_shown_as_a_symlink_and_its_target_is_not_revealed(root: Path) -> None:
    (root / "sub" / "out").symlink_to(root.parent / "outside_dir")
    snapshot = run(root, {"path": "sub"}).snapshot or ""
    assert "out (symlink)" in snapshot
    assert "outside_dir" not in snapshot and "private.txt" not in snapshot


def test_an_empty_directory_is_a_success_with_no_snapshot(root: Path) -> None:
    (root / "empty").mkdir()
    result = run(root, {"path": "empty"})
    assert result.success
    assert result.snapshot is None
    assert "empty" in result.message


# --- Long listings are cut, and the cut is stated ---------------------------


def test_a_long_listing_is_cut_and_the_result_says_so(root: Path) -> None:
    for i in range(5):
        (root / "sub" / f"f{i}.txt").write_text("", encoding="utf-8")  # plus inner.txt: 6 entries in total
    result = run(root, {"path": "sub"}, max_entries=3)

    assert result.success
    assert result.snapshot is not None
    assert result.snapshot.splitlines()[:3] == ["f0.txt (0 bytes)", "f1.txt (0 bytes)", "f2.txt (0 bytes)"]
    assert "f3.txt" not in result.snapshot  # nothing beyond the limit leaks through
    assert "cut: showing the first 3 of 6 entries" in result.snapshot
    assert "cut" in result.message
    assert result.data == {"path": "sub", "entries": 6, "truncated": True}


def test_a_listing_exactly_at_the_limit_is_not_cut(root: Path) -> None:
    result = run(root, max_entries=4)  # the root has exactly 4 entries
    assert cast(dict[str, Any], result.data)["truncated"] is False
    assert "cut" not in (result.snapshot or "")


# --- The workspace is a hard boundary ---------------------------------------


@pytest.mark.parametrize("path", ["..", "../outside_dir", "../work_evil", "sub/../.."])
def test_directories_outside_the_workspace_are_denied(root: Path, path: str) -> None:
    result = run(root, {"path": path})
    assert result.error_code is ErrorCode.PERMISSION_DENIED
    assert "private.txt" not in result.message


def test_an_absolute_path_outside_the_workspace_is_denied(root: Path) -> None:
    result = run(root, {"path": str(root.parent / "outside_dir")})
    assert result.error_code is ErrorCode.PERMISSION_DENIED


def test_a_symlinked_directory_pointing_outside_is_denied(root: Path) -> None:
    (root / "linked").symlink_to(root.parent / "outside_dir")
    assert run(root, {"path": "linked"}).error_code is ErrorCode.PERMISSION_DENIED


# --- Failures the model can understand and fix ------------------------------


def test_a_missing_directory_is_not_found_and_names_the_path(root: Path) -> None:
    result = run(root, {"path": "nope"})
    assert result.error_code is ErrorCode.NOT_FOUND
    assert "nope" in result.message


def test_a_file_is_invalid_args_and_points_to_read_file(root: Path) -> None:
    result = run(root, {"path": "a.py"})
    assert result.error_code is ErrorCode.INVALID_ARGS
    assert "file" in result.message and "read_file" in result.message  # tells the model what to do instead


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root ignores file permissions")
def test_an_unreadable_directory_is_permission_denied_not_a_crash(root: Path) -> None:
    locked = root / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        result = run(root, {"path": "locked"})
    finally:
        locked.chmod(0o700)  # let pytest clean up the temp directory
    assert result.error_code is ErrorCode.PERMISSION_DENIED


# --- How the model sees the tool --------------------------------------------


def test_the_tool_is_read_only_and_path_is_optional(root: Path) -> None:
    tool = ListDirTool()
    assert tool.permission is Permission.READ

    (listed,) = make_registry(tool).to_api_tools({Permission.READ})
    schema = cast(dict[str, Any], listed["input_schema"])
    assert "path" not in schema.get("required", [])
    assert "workspace" in schema["properties"]["path"]["description"]


def test_unknown_arguments_are_rejected(root: Path) -> None:
    result = run(root, {"path": ".", "recursive": True})
    assert result.error_code is ErrorCode.INVALID_ARGS
    assert "recursive" in result.message
