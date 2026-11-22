"""The deny-list protects secrets in EVERY tool: reading, searching and listing, not only reading."""

from pathlib import Path
from typing import Any, cast

import pytest

from prova.errors import ErrorCode
from prova.harness.patterns import PathMatcher
from prova.registry import Registry
from prova.tools.base import RunContext, Tool, ToolResult
from prova.tools.fs import ListDirTool, ReadFileTool
from prova.tools.registry import ToolRegistry
from prova.tools.search import SearchCodeTool

KEY = "sk-ant-THE-REAL-KEY"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "private").mkdir()
    (work / ".env").write_text(f"ANTHROPIC_API_KEY={KEY}\n", encoding="utf-8")
    (work / "id_rsa").write_text(f"-----BEGIN KEY----- {KEY}", encoding="utf-8")
    (work / "src" / "app.py").write_text("api_key = 'visible'  # the key is in .env\n", encoding="utf-8")
    (work / "private" / "a.txt").write_text("visible text a", encoding="utf-8")
    (work / "private" / "b.txt").write_text("visible text b", encoding="utf-8")
    return work


def call(root: Path, tool: Tool, args: dict[str, object], *, ctx: RunContext | None = None) -> ToolResult:
    registry = Registry[Tool]("tool")
    registry.register(tool)
    registry.freeze()
    return ToolRegistry(registry).dispatch(tool.name, args, ctx or RunContext(workspace=root, strict=True))


def data_of(result: ToolResult) -> dict[str, Any]:
    return cast(dict[str, Any], result.data)


# --- read_file ----------------------------------------------------------------


def test_the_default_context_protects_the_usual_secrets() -> None:
    ctx = RunContext(workspace=Path())
    assert ctx.deny.matches(".env")
    assert not ctx.deny.matches("src/app.py")


@pytest.mark.parametrize("path", [".env", ".env.local", "id_rsa"])
def test_read_file_refuses_a_secret_whether_or_not_it_exists(root: Path, path: str) -> None:
    result = call(root, ReadFileTool(), {"path": path})
    assert result.error_code is ErrorCode.PERMISSION_DENIED
    assert KEY not in result.message


def test_read_file_still_reads_ordinary_files(root: Path) -> None:
    assert call(root, ReadFileTool(), {"path": "src/app.py"}).success


def test_a_symlink_to_a_secret_cannot_be_read_through_its_harmless_name(root: Path) -> None:
    (root / "notes.txt").symlink_to(root / ".env")
    result = call(root, ReadFileTool(), {"path": "notes.txt"})
    assert result.error_code is ErrorCode.PERMISSION_DENIED


# --- search_code: the leak that a read-only check would miss ------------------


def test_searching_for_the_key_finds_nothing_and_says_files_were_skipped(root: Path) -> None:
    result = call(root, SearchCodeTool(), {"pattern": "sk-ant"})
    assert result.success
    assert result.snapshot is None  # the key lines of .env and id_rsa are not returned
    assert KEY not in result.message
    assert data_of(result)["files_skipped"] == 2
    assert "protected" in result.message


def test_a_search_never_returns_a_line_of_a_protected_file(root: Path) -> None:
    result = call(root, SearchCodeTool(), {"pattern": "key", "ignore_case": True})
    lines = (result.snapshot or "").splitlines()
    assert lines == ["src/app.py:1: api_key = 'visible'  # the key is in .env"]  # not .env, not id_rsa


def test_searching_a_protected_file_directly_is_denied(root: Path) -> None:
    result = call(root, SearchCodeTool(), {"pattern": "KEY", "path": ".env"})
    assert result.error_code is ErrorCode.PERMISSION_DENIED


# --- list_dir -----------------------------------------------------------------


def test_listing_hides_protected_entries_entirely(root: Path) -> None:
    result = call(root, ListDirTool(), {})
    assert (result.snapshot or "").splitlines() == ["private/", "src/"]
    assert data_of(result)["entries"] == 2  # the hidden entries are not even counted


def test_a_protected_file_cannot_be_listed_as_if_it_were_a_directory(root: Path) -> None:
    result = call(root, ListDirTool(), {"path": ".env"})
    assert result.error_code is ErrorCode.PERMISSION_DENIED  # not "is a file", which would confirm it exists


# --- The deny-list is a parameter of the run, not a hidden constant -----------


def test_a_run_can_carry_its_own_deny_list(root: Path) -> None:
    ctx = RunContext(workspace=root, strict=True, deny=PathMatcher(["private/**"]))

    denied = call(root, ReadFileTool(), {"path": "private/a.txt"}, ctx=ctx)
    assert denied.error_code is ErrorCode.PERMISSION_DENIED
    assert call(root, ReadFileTool(), {"path": ".env"}, ctx=ctx).success  # this run chose its own list


def test_files_inside_a_protected_directory_are_each_counted_as_skipped(root: Path) -> None:
    ctx = RunContext(workspace=root, strict=True, deny=PathMatcher(["private/**"]))
    result = call(root, SearchCodeTool(), {"pattern": "visible text"}, ctx=ctx)
    assert result.snapshot is None
    assert data_of(result)["files_skipped"] == 2
