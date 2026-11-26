"""The permissions.yaml of this repository, tried with the tools themselves.

The rule "the agent does not edit tests" is only as good as the profile that is supposed to enforce it, so the
profile is tested the way the agent would meet it: by trying to write.
"""

from pathlib import Path

import pytest

from prova.errors import ErrorCode
from prova.harness.permissions import get_profile, load_profiles
from prova.registry import Registry
from prova.tools.base import RunContext, Tool, ToolResult
from prova.tools.edit import CreateFileTool
from prova.tools.fs import ReadFileTool
from prova.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[3]


def context(workspace: Path, name: str) -> RunContext:
    profile = get_profile(load_profiles(ROOT / "permissions.yaml"), name)
    return RunContext.from_profile(profile, workspace, strict=True)


def fix_context(workspace: Path) -> RunContext:
    return context(workspace, "fix")


def dispatch(name: str, args: dict[str, object], ctx: RunContext) -> ToolResult:
    registry = Registry[Tool]("tool")
    registry.register(CreateFileTool())
    registry.register(ReadFileTool())
    registry.freeze()
    return ToolRegistry(registry).dispatch(name, args, ctx)


TEST_FILES = [
    "tests/unit/test_x.py",
    "tests/conftest.py",
    "pkg/tests/test_y.py",
    "test_injection_guard.py",
    "pkg/test_z.py",
    "conftest.py",
    "pkg/x_test.py",
]


@pytest.mark.parametrize("path", TEST_FILES)
def test_the_fix_profile_cannot_write_a_test_file(tmp_path: Path, path: str) -> None:
    result = dispatch("create_file", {"path": path, "content": "x = 1\n"}, fix_context(tmp_path))
    assert result.error_code == ErrorCode.PERMISSION_DENIED, path
    assert not (tmp_path / path).exists()


@pytest.mark.parametrize("path", TEST_FILES)
def test_the_fix_profile_can_still_read_a_test_file(tmp_path: Path, path: str) -> None:
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def test_it():\n    assert True\n", encoding="utf-8")
    result = dispatch("read_file", {"path": path}, fix_context(tmp_path))
    assert result.success, path


@pytest.mark.parametrize(
    "path", ["src/prova/app.py", "pkg/module.py", "README.md", "latest.py", "contest.py"]
)
def test_the_fix_profile_can_write_ordinary_files(tmp_path: Path, path: str) -> None:
    result = dispatch("create_file", {"path": path, "content": "x = 1\n"}, fix_context(tmp_path))
    assert result.success, f"{path}: {result.message}"


# --- reproduction tests (M13b): written by one role, never touched by the other ---------------------


@pytest.mark.parametrize("path", ["repro/test_bug.py", "repro/helpers.py", "repro/data.json"])
def test_the_fix_profile_cannot_write_the_reproduction_tests_or_their_helpers(
    tmp_path: Path, path: str
) -> None:
    result = dispatch("create_file", {"path": path, "content": "x = 1\n"}, fix_context(tmp_path))
    assert result.error_code == ErrorCode.PERMISSION_DENIED, path


@pytest.mark.parametrize("path", ["repro/test_bug.py", "repro/helpers.py", "repro/sub/test_more.py"])
def test_the_repro_profile_can_write_in_repro(tmp_path: Path, path: str) -> None:
    result = dispatch("create_file", {"path": path, "content": "x = 1\n"}, context(tmp_path, "repro"))
    assert result.success, f"{path}: {result.message}"


@pytest.mark.parametrize(
    "path", ["lib.py", "pkg/module.py", "tests/test_y.py", "test_top.py", "conftest.py", "reprox/test_a.py"]
)
def test_the_repro_profile_cannot_write_anything_outside_repro(tmp_path: Path, path: str) -> None:
    result = dispatch("create_file", {"path": path, "content": "x = 1\n"}, context(tmp_path, "repro"))
    assert result.error_code == ErrorCode.PERMISSION_DENIED, path


def test_the_repro_profile_can_read_the_code_but_not_the_hidden_tests(tmp_path: Path) -> None:
    for path in ("lib.py", "hidden_tests/test_fix.py"):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("x = 1\n", encoding="utf-8")
    ctx = context(tmp_path, "repro")
    assert dispatch("read_file", {"path": "lib.py"}, ctx).success
    assert (
        dispatch("read_file", {"path": "hidden_tests/test_fix.py"}, ctx).error_code
        == ErrorCode.PERMISSION_DENIED
    )


# --- attack tests (M14): the adversary writes them, and only them ------------------------------------


@pytest.mark.parametrize("path", ["attack/test_break.py", "attack/helpers.py", "attack/sub/test_more.py"])
def test_the_adversary_profile_can_write_in_attack(tmp_path: Path, path: str) -> None:
    result = dispatch("create_file", {"path": path, "content": "x = 1\n"}, context(tmp_path, "adversary"))
    assert result.success, f"{path}: {result.message}"


@pytest.mark.parametrize(
    "path", ["lib.py", "repro/test_bug.py", "tests/test_y.py", "conftest.py", "attackx/a.py"]
)
def test_the_adversary_profile_cannot_write_anything_outside_attack(tmp_path: Path, path: str) -> None:
    result = dispatch("create_file", {"path": path, "content": "x = 1\n"}, context(tmp_path, "adversary"))
    assert result.error_code == ErrorCode.PERMISSION_DENIED, path


def test_the_adversary_profile_can_read_the_code_but_not_the_hidden_tests(tmp_path: Path) -> None:
    for path in ("lib.py", "hidden_tests/test_fix.py"):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("x = 1\n", encoding="utf-8")
    ctx = context(tmp_path, "adversary")
    assert dispatch("read_file", {"path": "lib.py"}, ctx).success
    assert (
        dispatch("read_file", {"path": "hidden_tests/test_fix.py"}, ctx).error_code
        == ErrorCode.PERMISSION_DENIED
    )
