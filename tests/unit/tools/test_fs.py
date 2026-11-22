"""Tests for prova.tools.fs: read_file confined to a root directory, and its tiny dispatcher."""

from pathlib import Path

import pytest

from prova.errors import ErrorCode, ToolError
from prova.tools.fs import FS_TOOLS, make_fs_runner, read_file


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A workspace with two readable files, plus files OUTSIDE it that must never be reachable."""
    work = tmp_path / "work"
    (work / "sub").mkdir(parents=True)
    (work / "hello.txt").write_text("hello\n", encoding="utf-8")
    (work / "sub" / "inner.txt").write_text("inner", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    (tmp_path / "work_evil").mkdir()  # sibling whose name starts with "work": a classic prefix-check bug
    (tmp_path / "work_evil" / "loot.txt").write_text("LOOT", encoding="utf-8")
    return work


def code_of(excinfo: pytest.ExceptionInfo[ToolError]) -> ErrorCode:
    return excinfo.value.code


# --- read_file: success paths -----------------------------------------------


def test_reads_a_file_in_the_root(root: Path) -> None:
    assert read_file(root, "hello.txt") == "hello\n"


def test_reads_a_file_in_a_subdirectory(root: Path) -> None:
    assert read_file(root, "sub/inner.txt") == "inner"


def test_absolute_path_inside_the_root_is_allowed(root: Path) -> None:
    assert read_file(root, str(root / "hello.txt")) == "hello\n"


# --- read_file: the root is a hard boundary ---------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../secret.txt",
        "sub/../../secret.txt",  # starts inside, climbs out
        "../work_evil/loot.txt",  # sibling directory sharing the "work" prefix
    ],
)
def test_paths_that_climb_out_of_the_root_are_denied(root: Path, path: str) -> None:
    with pytest.raises(ToolError) as exc:
        read_file(root, path)
    assert code_of(exc) == ErrorCode.PERMISSION_DENIED
    assert "TOP SECRET" not in str(exc.value)


def test_absolute_path_outside_the_root_is_denied(root: Path) -> None:
    with pytest.raises(ToolError) as exc:
        read_file(root, str(root.parent / "secret.txt"))
    assert code_of(exc) == ErrorCode.PERMISSION_DENIED


def test_symlink_pointing_outside_the_root_is_denied(root: Path) -> None:
    (root / "link.txt").symlink_to(root.parent / "secret.txt")
    with pytest.raises(ToolError) as exc:
        read_file(root, "link.txt")
    assert code_of(exc) == ErrorCode.PERMISSION_DENIED


# --- read_file: other failures the model can fix ----------------------------


def test_missing_file_is_not_found_and_names_the_path(root: Path) -> None:
    with pytest.raises(ToolError, match="missing.txt") as exc:
        read_file(root, "missing.txt")
    assert code_of(exc) == ErrorCode.NOT_FOUND


def test_directory_is_invalid_args(root: Path) -> None:
    with pytest.raises(ToolError, match="directory") as exc:
        read_file(root, "sub")
    assert code_of(exc) == ErrorCode.INVALID_ARGS


def test_non_utf8_file_is_invalid_args(root: Path) -> None:
    (root / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(ToolError, match="UTF-8") as exc:
        read_file(root, "blob.bin")
    assert code_of(exc) == ErrorCode.INVALID_ARGS


def test_file_larger_than_the_limit_is_rejected(root: Path) -> None:
    with pytest.raises(ToolError, match="too large") as exc:
        read_file(root, "hello.txt", max_bytes=3)
    assert code_of(exc) == ErrorCode.INVALID_ARGS


# --- the tiny dispatcher ----------------------------------------------------


def test_runner_dispatches_read_file(root: Path) -> None:
    run = make_fs_runner(root)
    assert run("read_file", {"path": "hello.txt"}) == "hello\n"


def test_runner_unknown_tool_is_not_found_and_names_it(root: Path) -> None:
    run = make_fs_runner(root)
    with pytest.raises(ToolError, match="web_search") as exc:
        run("web_search", {"path": "hello.txt"})
    assert code_of(exc) == ErrorCode.NOT_FOUND


@pytest.mark.parametrize("args", [{}, {"path": 42}, {"path": None}])
def test_runner_missing_or_non_string_path_is_invalid_args(root: Path, args: dict[str, object]) -> None:
    run = make_fs_runner(root)
    with pytest.raises(ToolError, match="path") as exc:
        run("read_file", args)
    assert code_of(exc) == ErrorCode.INVALID_ARGS


def test_every_advertised_tool_can_be_dispatched(root: Path) -> None:
    # Guards against schema/dispatcher drift: advertised as `search_web` but dispatched as `web_search`.
    run = make_fs_runner(root)
    for tool in FS_TOOLS:
        assert isinstance(run(tool["name"], {"path": "hello.txt"}), str)
