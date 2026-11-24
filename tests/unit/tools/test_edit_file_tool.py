"""Tests for prova.tools.edit.EditFileTool: exact, unique replacements, and errors written for the model."""

from collections.abc import Collection
from pathlib import Path

import pytest

from prova.errors import ErrorCode
from prova.harness.patterns import PathMatcher
from prova.registry import Registry
from prova.tools.base import Permission, RunContext, Tool, ToolResult
from prova.tools.edit import EditFileTool
from prova.tools.registry import ToolRegistry

SAMPLE = "alpha = 1\nbeta = 2\ngamma = 3\n"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    (work / "sample.py").write_text(SAMPLE, encoding="utf-8")
    (work / ".env").write_text("API_KEY=do-not-touch\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    return work


def make_ctx(
    root: Path,
    *,
    permissions: Collection[Permission] = (Permission.READ, Permission.WRITE),
    write: tuple[str, ...] = ("**",),
) -> RunContext:
    return RunContext(
        workspace=root, strict=True, permissions=frozenset(permissions), write=PathMatcher(write)
    )


def edit(
    root: Path,
    old: str,
    new: str,
    *,
    path: str = "sample.py",
    ctx: RunContext | None = None,
    max_bytes: int = 200_000,
) -> ToolResult:
    """Call the tool the way the agent loop does: through the registry."""
    registry = Registry[Tool]("tool")
    registry.register(EditFileTool(max_bytes=max_bytes))
    registry.freeze()
    args = {"path": path, "old_str": old, "new_str": new}
    return ToolRegistry(registry).dispatch("edit_file", args, ctx or make_ctx(root))


def content(root: Path, name: str = "sample.py") -> str:
    return (root / name).read_text(encoding="utf-8")


# --- Success -----------------------------------------------------------------


def test_replaces_a_unique_string_and_says_where(root: Path) -> None:
    result = edit(root, "beta = 2", "beta = 20")
    assert result.success
    assert content(root) == "alpha = 1\nbeta = 20\ngamma = 3\n"
    assert "sample.py" in result.message and "line 2" in result.message


def test_the_result_shows_the_edited_region_with_line_numbers(root: Path) -> None:
    result = edit(root, "beta = 2", "beta = 20\nbeta_extra = 21")
    assert result.snapshot is not None
    assert "2│ beta = 20" in result.snapshot and "3│ beta_extra = 21" in result.snapshot


def test_a_multi_line_old_str_is_replaced_whole(root: Path) -> None:
    assert edit(root, "beta = 2\ngamma = 3", "delta = 4").success
    assert content(root) == "alpha = 1\ndelta = 4\n"


def test_an_empty_new_str_deletes_the_text(root: Path) -> None:
    assert edit(root, "beta = 2\n", "").success
    assert content(root) == "alpha = 1\ngamma = 3\n"


def test_the_rest_of_the_file_is_kept_byte_for_byte_including_crlf(root: Path) -> None:
    (root / "win.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    assert edit(root, "two", "2", path="win.txt").success
    assert (root / "win.txt").read_bytes() == b"one\r\n2\r\nthree\r\n"


# --- Errors the model can act on ---------------------------------------------


def test_text_that_is_not_in_the_file_says_so_and_asks_for_a_new_read(root: Path) -> None:
    result = edit(root, "zeta = 26", "zeta = 27")
    assert result.error_code == ErrorCode.NO_MATCH
    assert "not found" in result.message and "Re-read" in result.message
    assert content(root) == SAMPLE  # a failed edit changes nothing


def test_a_near_miss_names_the_closest_lines(root: Path) -> None:
    result = edit(root, "beta = 3", "beta = 30")
    assert result.error_code == ErrorCode.NO_MATCH
    assert "line 2" in result.message and "beta = 2" in result.message


def test_text_that_appears_more_than_once_lists_every_place(root: Path) -> None:
    (root / "twice.py").write_text("x = 1\ny = 2\nx = 1\n", encoding="utf-8")
    result = edit(root, "x = 1", "x = 9", path="twice.py")
    assert result.error_code == ErrorCode.AMBIGUOUS
    assert "2 locations" in result.message and "1" in result.message and "3" in result.message
    assert "more surrounding lines" in result.message
    assert content(root, "twice.py") == "x = 1\ny = 2\nx = 1\n"


def test_a_difference_in_whitespace_is_named_as_such(root: Path) -> None:
    (root / "f.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    result = edit(root, "def f():\n  return 1", "def f():\n    return 2", path="f.py")
    assert result.error_code == ErrorCode.WHITESPACE_MISMATCH
    assert "whitespace" in result.message and "line 1" in result.message
    assert content(root, "f.py") == "def f():\n    return 1\n"


def test_a_whitespace_difference_at_many_places_says_how_many_are_not_listed(root: Path) -> None:
    (root / "many.py").write_text("x = 1\n" * 12, encoding="utf-8")
    result = edit(root, "x  = 1", "x = 2", path="many.py")
    assert result.error_code == ErrorCode.WHITESPACE_MISMATCH
    assert "lines 1, 2, 3, 4, 5, 6, 7, 8, 9, 10" in result.message
    assert "and 2 more" in result.message
    assert content(root, "many.py") == "x = 1\n" * 12


def test_line_numbers_copied_from_read_file_are_recognised(root: Path) -> None:
    result = edit(root, "2│ beta = 2", "2│ beta = 20")
    assert result.error_code == ErrorCode.INVALID_ARGS
    assert "line number" in result.message and "not part of the file" in result.message
    assert content(root) == SAMPLE


def test_an_edit_that_changes_nothing_is_refused(root: Path) -> None:
    result = edit(root, "beta = 2", "beta = 2")
    assert result.error_code == ErrorCode.INVALID_ARGS
    assert "same" in result.message


def test_an_empty_old_str_is_refused(root: Path) -> None:
    assert edit(root, "", "anything").error_code == ErrorCode.INVALID_ARGS
    assert content(root) == SAMPLE


def test_a_missing_file_is_not_found(root: Path) -> None:
    assert edit(root, "a", "b", path="nope.py").error_code == ErrorCode.NOT_FOUND


def test_a_directory_is_not_a_file(root: Path) -> None:
    (root / "pkg").mkdir()
    assert edit(root, "a", "b", path="pkg").error_code == ErrorCode.INVALID_ARGS


def test_a_binary_file_is_refused(root: Path) -> None:
    (root / "blob.bin").write_bytes(b"ab\x00cd")
    assert edit(root, "ab", "xy", path="blob.bin").error_code == ErrorCode.INVALID_ARGS


def test_a_file_over_the_size_limit_is_refused_and_the_limit_is_stated(root: Path) -> None:
    (root / "big.txt").write_text("z" * 300, encoding="utf-8")
    result = edit(root, "z", "y", path="big.txt", max_bytes=100)
    assert result.error_code == ErrorCode.INVALID_ARGS
    assert "100" in result.message and "300" in result.message


# --- Permissions: this is the first tool that writes -------------------------


def test_the_tool_needs_the_write_permission() -> None:
    assert EditFileTool().permission == Permission.WRITE


def test_a_read_only_run_cannot_edit(root: Path) -> None:
    result = edit(root, "beta = 2", "beta = 20", ctx=make_ctx(root, permissions=[Permission.READ]))
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert content(root) == SAMPLE


def test_a_path_outside_write_paths_is_refused(root: Path) -> None:
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("old", encoding="utf-8")
    ctx = make_ctx(root, write=("src/**",))
    result = edit(root, "old", "new", path="docs/a.md", ctx=ctx)
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert "not writable" in result.message
    assert content(root, "docs/a.md") == "old"


def test_no_write_paths_means_nothing_is_writable(root: Path) -> None:
    result = edit(root, "beta = 2", "beta = 20", ctx=make_ctx(root, write=()))
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert content(root) == SAMPLE


def test_a_protected_file_is_refused_even_when_everything_is_writable(root: Path) -> None:
    result = edit(root, "do-not-touch", "changed", path=".env")
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert content(root, ".env") == "API_KEY=do-not-touch\n"


def test_a_path_that_climbs_out_of_the_workspace_is_refused(root: Path) -> None:
    result = edit(root, "outside", "changed", path="../outside.txt")
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert (root.parent / "outside.txt").read_text(encoding="utf-8") == "outside"


def test_a_symlink_to_a_file_outside_is_refused(root: Path) -> None:
    (root / "link.txt").symlink_to(root.parent / "outside.txt")
    result = edit(root, "outside", "changed", path="link.txt")
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert (root.parent / "outside.txt").read_text(encoding="utf-8") == "outside"


def test_a_multi_line_edit_works_in_a_crlf_file_although_the_model_writes_plain_newlines(root: Path) -> None:
    (root / "win.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    assert edit(root, "one\ntwo", "1\n2", path="win.txt").success
    assert (root / "win.txt").read_bytes() == b"1\r\n2\r\nthree\r\n"


# --- Rollback: the original comes back ---------------------------------------


def test_an_edit_can_be_rolled_back(root: Path) -> None:
    ctx = make_ctx(root)
    assert edit(root, "beta = 2", "beta = 20", ctx=ctx).success
    ctx.snapshots.rollback_all()
    assert content(root) == SAMPLE


def test_two_edits_to_one_file_roll_back_to_the_original(root: Path) -> None:
    ctx = make_ctx(root)
    assert edit(root, "beta = 2", "beta = 20", ctx=ctx).success
    assert edit(root, "gamma = 3", "gamma = 30", ctx=ctx).success
    ctx.snapshots.rollback_all()
    assert content(root) == SAMPLE


def test_a_failed_edit_leaves_nothing_to_roll_back(root: Path) -> None:
    ctx = make_ctx(root)
    assert not edit(root, "zeta = 26", "zeta = 27", ctx=ctx).success
    assert ctx.snapshots.paths == ()


# --- Readable but not writable: the read-only paths of a profile ---------------------------------------


def read_only_tests_ctx(root: Path) -> RunContext:
    return RunContext(
        workspace=root,
        strict=True,
        permissions=frozenset({Permission.READ, Permission.WRITE}),
        write=PathMatcher(("**",)),
        write_deny=PathMatcher(("tests/**",)),
    )


def test_a_file_in_a_read_only_path_cannot_be_edited_even_when_everything_is_writable(root: Path) -> None:
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text("assert True\n", encoding="utf-8")
    result = edit(root, "True", "False", path="tests/test_a.py", ctx=read_only_tests_ctx(root))
    assert result.error_code == ErrorCode.PERMISSION_DENIED and "read-only" in result.message
    assert (root / "tests" / "test_a.py").read_text(encoding="utf-8") == "assert True\n"


def test_a_symlink_into_a_read_only_path_does_not_get_around_it(root: Path) -> None:
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text("assert True\n", encoding="utf-8")
    (root / "shortcut.py").symlink_to(root / "tests" / "test_a.py")
    result = edit(root, "True", "False", path="shortcut.py", ctx=read_only_tests_ctx(root))
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert (root / "tests" / "test_a.py").read_text(encoding="utf-8") == "assert True\n"


def test_the_same_context_still_edits_files_outside_the_read_only_paths(root: Path) -> None:
    assert edit(root, "beta = 2", "beta = 20", ctx=read_only_tests_ctx(root)).success
