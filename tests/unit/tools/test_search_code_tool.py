"""Tests for prova.tools.search.SearchCodeTool: literal search, stable order, every limit stated."""

import os
from pathlib import Path
from typing import Any, cast

import pytest

from prova.errors import ErrorCode
from prova.registry import Registry
from prova.tools.base import Permission, RunContext, Tool, ToolResult
from prova.tools.registry import ToolRegistry
from prova.tools.search import SearchCodeTool


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A small repo: 4 searchable files, folders that must be skipped, and a secret OUTSIDE the workspace."""
    work = tmp_path / "work"
    (work / "sub" / "deep").mkdir(parents=True)
    (work / ".git").mkdir()
    (work / "node_modules" / "pkg").mkdir(parents=True)
    (work / "a.py").write_text("import os\n\ndef find_me():\n    return 1\n", encoding="utf-8")
    (work / "b.txt").write_text("nothing here\nFIND_ME in caps\n", encoding="utf-8")
    (work / "sub" / "c.py").write_text("x = find_me()\n", encoding="utf-8")
    (work / "sub" / "deep" / "d.md").write_text("find_me docs\n", encoding="utf-8")
    (work / ".git" / "config").write_text("find_me in git", encoding="utf-8")
    (work / "node_modules" / "pkg" / "index.js").write_text("find_me()", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("find_me outside the workspace", encoding="utf-8")
    (tmp_path / "work_evil").mkdir()
    return work


def make_registry(tool: Tool) -> ToolRegistry:
    registry = Registry[Tool]("tool")
    registry.register(tool)
    registry.freeze()
    return ToolRegistry(registry)


def run(root: Path, args: dict[str, object], **limits: int) -> ToolResult:
    """Call the tool the way the agent loop does: through the registry, with bugs raised (strict)."""
    ctx = RunContext(workspace=root, strict=True)
    return make_registry(SearchCodeTool(**limits)).dispatch("search_code", args, ctx)


def data_of(result: ToolResult) -> dict[str, Any]:
    return cast(dict[str, Any], result.data)


# --- Finding things ---------------------------------------------------------


def test_finds_literal_matches_as_path_line_text_sorted_by_path(root: Path) -> None:
    result = run(root, {"pattern": "find_me"})
    assert result.success
    expected = ["a.py:3: def find_me():", "sub/c.py:1: x = find_me()", "sub/deep/d.md:1: find_me docs"]
    assert (result.snapshot or "").splitlines() == expected
    assert "found 3 matches for 'find_me' in 3 files" in result.message
    assert data_of(result)["matches"] == 3


def test_matching_is_case_sensitive_unless_asked_otherwise(root: Path) -> None:
    result = run(root, {"pattern": "find_me", "ignore_case": True})
    assert (result.snapshot or "").splitlines() == [
        "a.py:3: def find_me():",
        "b.txt:2: FIND_ME in caps",
        "sub/c.py:1: x = find_me()",
        "sub/deep/d.md:1: find_me docs",
    ]


def test_the_pattern_is_plain_text_not_a_regular_expression(root: Path) -> None:
    (root / "re.txt").write_text("axb\na.b\n(open\n", encoding="utf-8")
    assert run(root, {"pattern": "a.b"}).snapshot == "re.txt:2: a.b"  # `.` is a dot, not "any character"
    assert run(root, {"pattern": "(open"}).snapshot == "re.txt:3: (open"  # no regex syntax error possible


def test_a_glob_limits_which_files_are_searched(root: Path) -> None:
    result = run(root, {"pattern": "find_me", "glob": "*.py"})
    assert (result.snapshot or "").splitlines() == ["a.py:3: def find_me():", "sub/c.py:1: x = find_me()"]


def test_a_subdirectory_limits_the_search_but_paths_stay_workspace_relative(root: Path) -> None:
    result = run(root, {"pattern": "find_me", "path": "sub"})
    expected = ["sub/c.py:1: x = find_me()", "sub/deep/d.md:1: find_me docs"]
    assert (result.snapshot or "").splitlines() == expected


def test_a_single_file_can_be_searched(root: Path) -> None:
    assert run(root, {"pattern": "find_me", "path": "a.py"}).snapshot == "a.py:3: def find_me():"


def test_no_match_is_a_success_that_says_how_many_files_were_searched(root: Path) -> None:
    result = run(root, {"pattern": "zzz-not-there"})
    assert result.success
    assert result.snapshot is None
    assert "no matches for 'zzz-not-there'" in result.message
    assert "4 files searched" in result.message  # a.py, b.txt, sub/c.py, sub/deep/d.md


# --- What is never searched --------------------------------------------------


def test_folders_of_tooling_noise_are_skipped(root: Path) -> None:
    snapshot = run(root, {"pattern": "find_me"}).snapshot or ""
    assert ".git" not in snapshot and "node_modules" not in snapshot


def test_a_symlink_is_never_followed_and_is_counted_as_skipped(root: Path) -> None:
    (root / "link.txt").symlink_to(root.parent / "secret.txt")
    result = run(root, {"pattern": "find_me"})
    assert "outside" not in (result.snapshot or "")
    assert data_of(result)["files_skipped"] == 1
    assert "skipped 1 file" in result.message


def test_a_binary_file_is_skipped_and_counted_not_ignored_silently(root: Path) -> None:
    (root / "blob.bin").write_bytes(b"\xff\xfe find_me \x00")
    result = run(root, {"pattern": "find_me"})
    assert "blob.bin" not in (result.snapshot or "")
    assert data_of(result)["files_skipped"] == 1
    assert "skipped 1 file" in result.message


def test_a_file_over_the_size_limit_is_skipped_and_counted(root: Path) -> None:
    (root / "big.txt").write_text("find_me " + "x" * 100, encoding="utf-8")
    result = run(root, {"pattern": "find_me"}, max_file_bytes=50)
    assert "big.txt" not in (result.snapshot or "")
    assert data_of(result)["files_skipped"] == 1


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root ignores file permissions")
def test_an_unreadable_file_is_skipped_not_a_crash(root: Path) -> None:
    locked = root / "locked.txt"
    locked.write_text("find_me", encoding="utf-8")
    locked.chmod(0)
    try:
        result = run(root, {"pattern": "find_me"})
    finally:
        locked.chmod(0o600)  # let pytest clean up the temp directory
    assert result.success
    assert data_of(result)["files_skipped"] == 1


# --- Every limit is stated out loud -----------------------------------------


def test_a_long_matching_line_is_cut_and_says_so(root: Path) -> None:
    (root / "long.txt").write_text("find_me " + "x" * 500 + "\n", encoding="utf-8")
    line = next(
        entry for entry in (run(root, {"pattern": "find_me", "path": "long.txt"}).snapshot or "").splitlines()
    )
    assert line.startswith("long.txt:1: find_me xxxx")
    assert "line cut" in line and "508 chars" in line
    assert len(line) < 300  # the whole line was not passed through


def test_too_many_matches_are_cut_and_the_result_says_so(root: Path) -> None:
    result = run(root, {"pattern": "find_me"}, max_matches=2)  # 3 matches exist
    assert result.snapshot is not None
    assert result.snapshot.splitlines()[:2] == ["a.py:3: def find_me():", "sub/c.py:1: x = find_me()"]
    assert "sub/deep/d.md" not in result.snapshot  # the third match does not leak through
    assert "cut: showing the first 2 matches" in result.snapshot
    assert "cut" in result.message
    assert data_of(result)["truncated"] is True


def test_exactly_as_many_matches_as_the_limit_is_not_cut(root: Path) -> None:
    result = run(root, {"pattern": "find_me"}, max_matches=3)
    assert data_of(result)["truncated"] is False
    assert "cut" not in (result.snapshot or "")


def test_stopping_after_the_file_limit_is_stated(root: Path) -> None:
    result = run(root, {"pattern": "find_me"}, max_files=2)  # only a.py and b.txt are reached
    assert data_of(result)["files_searched"] == 2
    assert data_of(result)["truncated"] is True
    assert "cut: searched only the first 2 files" in (result.snapshot or "") + result.message


# --- The workspace is a hard boundary ---------------------------------------


@pytest.mark.parametrize("path", ["..", "../work_evil", "sub/../.."])
def test_searching_outside_the_workspace_is_denied(root: Path, path: str) -> None:
    result = run(root, {"pattern": "find_me", "path": path})
    assert result.error_code is ErrorCode.PERMISSION_DENIED


def test_an_absolute_path_outside_and_a_symlinked_directory_are_denied(root: Path) -> None:
    (root / "linked").symlink_to(root.parent / "work_evil")
    assert run(root, {"pattern": "x", "path": str(root.parent)}).error_code is ErrorCode.PERMISSION_DENIED
    assert run(root, {"pattern": "x", "path": "linked"}).error_code is ErrorCode.PERMISSION_DENIED


# --- Failures the model can understand and fix ------------------------------


def test_a_missing_path_is_not_found_and_names_it(root: Path) -> None:
    result = run(root, {"pattern": "find_me", "path": "nope"})
    assert result.error_code is ErrorCode.NOT_FOUND
    assert "nope" in result.message


def test_an_empty_pattern_is_invalid_args_because_it_would_match_everything(root: Path) -> None:
    result = run(root, {"pattern": ""})
    assert result.error_code is ErrorCode.INVALID_ARGS
    assert "pattern" in result.message


# --- How the model sees the tool --------------------------------------------


def test_the_tool_is_read_only_and_only_the_pattern_is_required(root: Path) -> None:
    tool = SearchCodeTool()
    assert tool.permission is Permission.READ

    (listed,) = make_registry(tool).to_api_tools({Permission.READ})
    schema = cast(dict[str, Any], listed["input_schema"])
    assert schema["required"] == ["pattern"]
    assert {"pattern", "path", "glob", "ignore_case"} <= set(schema["properties"])


def test_regex_is_not_an_argument_and_unknown_arguments_are_rejected(root: Path) -> None:
    result = run(root, {"pattern": "a", "regex": True})
    assert result.error_code is ErrorCode.INVALID_ARGS
    assert "regex" in result.message
