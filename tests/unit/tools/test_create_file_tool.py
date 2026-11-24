"""Tests for prova.tools.edit.CreateFileTool: makes a NEW file, never overwrites one."""

from collections.abc import Collection
from pathlib import Path

import pytest

from prova.errors import ErrorCode
from prova.harness.patterns import PathMatcher
from prova.registry import Registry
from prova.tools.base import Permission, RunContext, Tool, ToolResult
from prova.tools.edit import CreateFileTool
from prova.tools.registry import ToolRegistry


@pytest.fixture
def root(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    (work / "exists.py").write_text("keep me\n", encoding="utf-8")
    (tmp_path / "outside_dir").mkdir()
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


def create(
    root: Path,
    path: str,
    content: str = "hello\n",
    *,
    ctx: RunContext | None = None,
    max_bytes: int = 200_000,
) -> ToolResult:
    registry = Registry[Tool]("tool")
    registry.register(CreateFileTool(max_bytes=max_bytes))
    registry.freeze()
    args = {"path": path, "content": content}
    return ToolRegistry(registry).dispatch("create_file", args, ctx or make_ctx(root))


# --- Success -----------------------------------------------------------------


def test_creates_a_new_file_and_says_how_big_it_is(root: Path) -> None:
    result = create(root, "new.py", "a = 1\nb = 2\n")
    assert result.success
    assert (root / "new.py").read_text(encoding="utf-8") == "a = 1\nb = 2\n"
    assert "new.py" in result.message and "2 lines" in result.message and "12 bytes" in result.message


def test_missing_parent_directories_are_created(root: Path) -> None:
    assert create(root, "pkg/sub/mod.py", "x = 1\n").success
    assert (root / "pkg" / "sub" / "mod.py").read_text(encoding="utf-8") == "x = 1\n"


def test_an_empty_file_can_be_created(root: Path) -> None:
    assert create(root, "__init__.py", "").success
    assert (root / "__init__.py").read_bytes() == b""


def test_the_content_is_written_as_given_with_no_newline_translation(root: Path) -> None:
    assert create(root, "win.txt", "one\r\ntwo\r\n").success
    assert (root / "win.txt").read_bytes() == b"one\r\ntwo\r\n"


# --- Never overwrite ---------------------------------------------------------


def test_an_existing_file_is_never_overwritten_and_the_model_is_pointed_to_edit_file(root: Path) -> None:
    result = create(root, "exists.py", "replaced\n")
    assert result.error_code == ErrorCode.ALREADY_EXISTS
    assert "already exists" in result.message and "edit_file" in result.message
    assert (root / "exists.py").read_text(encoding="utf-8") == "keep me\n"


def test_an_existing_directory_counts_as_existing(root: Path) -> None:
    (root / "pkg").mkdir()
    assert create(root, "pkg").error_code == ErrorCode.ALREADY_EXISTS


def test_a_parent_that_is_a_file_is_refused(root: Path) -> None:
    result = create(root, "exists.py/child.py")
    assert result.error_code == ErrorCode.INVALID_ARGS
    assert "exists.py" in result.message
    assert (root / "exists.py").read_text(encoding="utf-8") == "keep me\n"


# --- Content limits ----------------------------------------------------------


def test_content_over_the_size_limit_is_refused_and_the_limit_is_stated(root: Path) -> None:
    result = create(root, "big.txt", "z" * 300, max_bytes=100)
    assert result.error_code == ErrorCode.INVALID_ARGS
    assert "100" in result.message and "300" in result.message
    assert not (root / "big.txt").exists()


def test_binary_content_is_refused(root: Path) -> None:
    result = create(root, "blob.txt", "ab\x00cd")
    assert result.error_code == ErrorCode.INVALID_ARGS
    assert not (root / "blob.txt").exists()


def test_the_size_limit_counts_bytes_not_characters(root: Path) -> None:
    assert create(root, "accents.txt", "é" * 60, max_bytes=100).error_code == ErrorCode.INVALID_ARGS


# --- Permissions: a new file is still a write --------------------------------


def test_the_tool_needs_the_write_permission() -> None:
    assert CreateFileTool().permission == Permission.WRITE


def test_a_read_only_run_cannot_create(root: Path) -> None:
    result = create(root, "new.py", ctx=make_ctx(root, permissions=[Permission.READ]))
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert not (root / "new.py").exists()


def test_a_path_outside_write_paths_is_refused_and_nothing_is_made(root: Path) -> None:
    result = create(root, "docs/a.md", ctx=make_ctx(root, write=("src/**",)))
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert "not writable" in result.message
    assert not (root / "docs").exists()  # not even the parent directory


def test_no_write_paths_means_nothing_can_be_created(root: Path) -> None:
    result = create(root, "new.py", ctx=make_ctx(root, write=()))
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert not (root / "new.py").exists()


def test_a_protected_path_cannot_be_created_even_when_everything_is_writable(root: Path) -> None:
    result = create(root, ".env", "ANTHROPIC_API_KEY=planted\n")
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert not (root / ".env").exists()


def test_a_path_that_climbs_out_of_the_workspace_is_refused(root: Path) -> None:
    result = create(root, "../escaped.txt")
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert not (root.parent / "escaped.txt").exists()


def test_a_directory_symlink_pointing_outside_is_refused(root: Path) -> None:
    (root / "out").symlink_to(root.parent / "outside_dir")
    result = create(root, "out/planted.txt")
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert not (root.parent / "outside_dir" / "planted.txt").exists()


def test_a_dangling_symlink_to_outside_is_not_followed(root: Path) -> None:
    (root / "link.txt").symlink_to(root.parent / "not_yet.txt")
    result = create(root, "link.txt")
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert not (root.parent / "not_yet.txt").exists()


def test_the_workspace_root_is_not_a_file(root: Path) -> None:
    assert create(root, ".").error_code == ErrorCode.ALREADY_EXISTS


# --- Rollback: the new file and the directories made for it go away -----------


def test_a_created_file_and_its_new_directories_can_be_rolled_back(root: Path) -> None:
    ctx = make_ctx(root)
    assert create(root, "pkg/sub/mod.py", "x = 1\n", ctx=ctx).success
    ctx.snapshots.rollback_all()
    assert not (root / "pkg").exists()


def test_a_refused_create_leaves_nothing_to_roll_back(root: Path) -> None:
    ctx = make_ctx(root)
    assert not create(root, "exists.py", "replaced\n", ctx=ctx).success
    assert ctx.snapshots.paths == ()
