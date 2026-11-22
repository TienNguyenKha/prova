"""Tests for prova.tools.paths.resolve_inside: the one place that keeps tools inside the workspace."""

from pathlib import Path

import pytest

from prova.errors import ErrorCode, ToolError
from prova.harness.patterns import PathMatcher
from prova.tools.paths import resolve_inside


@pytest.fixture
def root(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    (work / "sub").mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    (tmp_path / "work_evil").mkdir()  # sibling sharing the "work" prefix
    return work


def test_a_relative_path_resolves_to_its_real_location(root: Path) -> None:
    assert resolve_inside(root, "sub/a.txt") == root.resolve() / "sub" / "a.txt"


def test_the_root_itself_is_inside(root: Path) -> None:
    assert resolve_inside(root, ".") == root.resolve()


def test_an_absolute_path_inside_is_accepted(root: Path) -> None:
    assert resolve_inside(root, str(root / "sub")) == root.resolve() / "sub"


@pytest.mark.parametrize(
    "path", ["..", "../secret.txt", "sub/../../secret.txt", "../work_evil", "/etc/passwd"]
)
def test_anything_resolving_outside_is_permission_denied(root: Path, path: str) -> None:
    with pytest.raises(ToolError) as exc:
        resolve_inside(root, path)
    assert exc.value.code is ErrorCode.PERMISSION_DENIED


def test_a_symlink_pointing_outside_is_permission_denied(root: Path) -> None:
    (root / "link").symlink_to(root.parent / "secret.txt")
    with pytest.raises(ToolError) as exc:
        resolve_inside(root, "link")
    assert exc.value.code is ErrorCode.PERMISSION_DENIED


def test_a_path_with_a_nul_character_is_invalid_args_not_a_crash(root: Path) -> None:
    # Path.resolve() raises ValueError for this: without a check it would surface as a bug in our tool.
    with pytest.raises(ToolError) as exc:
        resolve_inside(root, "a\x00b")
    assert exc.value.code is ErrorCode.INVALID_ARGS


# --- The deny-list ------------------------------------------------------------

DENY = PathMatcher([".env*", "*.pem", "secrets/**"])


def test_a_denied_path_is_refused_even_if_the_file_does_not_exist(root: Path) -> None:
    # "denied" for an existing file and "not found" for a missing one would tell the model which is which
    with pytest.raises(ToolError, match="protected") as exc:
        resolve_inside(root, ".env.local", deny=DENY)
    assert exc.value.code is ErrorCode.PERMISSION_DENIED


@pytest.mark.parametrize("path", [".env", "sub/.env", "SUB/.ENV", "a/b/server.pem", "secrets/token.txt"])
def test_the_deny_list_applies_at_any_depth_and_ignores_case(root: Path, path: str) -> None:
    with pytest.raises(ToolError) as exc:
        resolve_inside(root, path, deny=DENY)
    assert exc.value.code is ErrorCode.PERMISSION_DENIED


def test_a_harmless_name_that_points_at_a_denied_file_is_denied(root: Path) -> None:
    (root / ".env").write_text("KEY=1", encoding="utf-8")
    (root / "harmless.txt").symlink_to(root / ".env")
    with pytest.raises(ToolError) as exc:
        resolve_inside(root, "harmless.txt", deny=DENY)  # the REAL location is what is checked
    assert exc.value.code is ErrorCode.PERMISSION_DENIED


def test_a_tilde_is_a_plain_directory_name_never_the_home_directory(root: Path) -> None:
    target = resolve_inside(root, "~/notes.txt", deny=DENY)
    assert target == root.resolve() / "~" / "notes.txt"
    assert Path.home() not in target.parents


def test_the_workspace_root_itself_is_never_denied(root: Path) -> None:
    assert resolve_inside(root, ".", deny=PathMatcher(["**"])) == root.resolve()


def test_without_a_deny_list_nothing_is_denied(root: Path) -> None:
    assert resolve_inside(root, ".env") == root.resolve() / ".env"
