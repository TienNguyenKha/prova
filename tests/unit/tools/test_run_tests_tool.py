"""Tests for prova.tools.run_tests.RunTestsTool: runs the tests in the sandbox, never on the host."""

from collections.abc import Collection
from pathlib import Path

import pytest

from prova.errors import ErrorCode, SandboxUnavailableError
from prova.registry import Registry
from prova.tools.base import Permission, RunContext, Tool, ToolResult
from prova.tools.registry import ToolRegistry
from prova.tools.run_tests import RunTestsTool
from tests.fakes import FakeSandbox, exec_result

done = exec_result
PYTEST = ["python", "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    (work / "tests").mkdir(parents=True)
    (work / "tests" / "test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")
    (work / ".env").write_text("KEY=1\n", encoding="utf-8")
    return work


def run_tool(
    root: Path,
    sandbox: FakeSandbox | None,
    args: dict[str, object] | None = None,
    *,
    permissions: Collection[Permission] = (Permission.READ, Permission.EXEC),
    tool: RunTestsTool | None = None,
) -> ToolResult:
    registry = Registry[Tool]("tool")
    registry.register(tool or RunTestsTool())
    registry.freeze()
    ctx = RunContext(workspace=root, strict=True, permissions=frozenset(permissions), sandbox=sandbox)
    return ToolRegistry(registry).dispatch("run_tests", args or {}, ctx)


# --- What is run --------------------------------------------------------------


def test_the_tool_needs_the_exec_permission_and_is_hidden_without_it() -> None:
    registry = Registry[Tool]("tool")
    registry.register(RunTestsTool())
    registry.freeze()
    tools = ToolRegistry(registry)
    assert RunTestsTool().permission == Permission.EXEC
    assert [t["name"] for t in tools.to_api_tools({Permission.EXEC})] == ["run_tests"]
    assert tools.to_api_tools({Permission.READ, Permission.WRITE}) == []


def test_the_command_is_fixed_and_runs_in_the_workspace_mount(root: Path) -> None:
    sandbox = FakeSandbox(done(0, "1 passed"))
    run_tool(root, sandbox)
    assert sandbox.calls == [
        {"argv": PYTEST, "timeout_s": 120.0, "cwd": "/work"}
    ]


def test_the_timeout_can_be_set_when_the_tool_is_made(root: Path) -> None:
    sandbox = FakeSandbox(done(0))
    run_tool(root, sandbox, tool=RunTestsTool(timeout_s=30))
    assert sandbox.calls[0]["timeout_s"] == 30


def test_a_target_goes_after_two_dashes_so_it_can_never_be_a_pytest_option(root: Path) -> None:
    sandbox = FakeSandbox(done(0))
    run_tool(root, sandbox, {"target": "tests"})
    assert sandbox.calls[0]["argv"] == [*PYTEST, "--", "tests"]


def test_a_target_that_looks_like_an_option_is_only_a_path(root: Path) -> None:
    sandbox = FakeSandbox(done(0))
    result = run_tool(root, sandbox, {"target": "-p"})
    assert result.error_code == ErrorCode.NOT_FOUND  # no such file: it was never an option
    assert sandbox.calls == []


def test_unknown_arguments_are_refused_so_the_model_cannot_add_pytest_options(root: Path) -> None:
    sandbox = FakeSandbox(done(0))
    result = run_tool(root, sandbox, {"args": ["-p", "evil"]})
    assert result.error_code == ErrorCode.INVALID_ARGS
    assert sandbox.calls == []


@pytest.mark.parametrize("target", ["../outside", ".env"])
def test_a_target_outside_the_workspace_or_protected_is_refused_before_anything_runs(
    root: Path, target: str
) -> None:
    sandbox = FakeSandbox(done(0))
    result = run_tool(root, sandbox, {"target": target})
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert sandbox.calls == []


def test_a_target_that_does_not_exist_is_not_found(root: Path) -> None:
    sandbox = FakeSandbox(done(0))
    assert run_tool(root, sandbox, {"target": "tests/nope"}).error_code == ErrorCode.NOT_FOUND
    assert sandbox.calls == []


# --- What the model is told ---------------------------------------------------


def test_passing_tests_are_reported_as_passed(root: Path) -> None:
    result = run_tool(root, FakeSandbox(done(0, "....\n4 passed in 0.02s\n")))
    assert result.success
    assert "passed" in result.message and "FAILED" not in result.message
    assert result.data["passed"] is True and result.data["exit_code"] == 0
    assert result.snapshot is not None and "4 passed" in result.snapshot


def test_failing_tests_are_information_not_a_tool_failure_but_say_FAILED(root: Path) -> None:
    result = run_tool(root, FakeSandbox(done(1, "FAILED test_a.py::test_x\n1 failed in 0.1s\n")))
    assert result.success  # the tool did its job; the model must read the message
    assert "FAILED" in result.message
    assert result.data["passed"] is False and result.data["exit_code"] == 1
    assert result.snapshot is not None and "test_a.py::test_x" in result.snapshot


def test_no_tests_collected_is_said_plainly_and_is_not_a_pass(root: Path) -> None:
    result = run_tool(root, FakeSandbox(done(5, "no tests ran\n")))
    assert "no tests were collected" in result.message
    assert result.data["passed"] is False


def test_an_interrupted_run_points_at_import_and_syntax_errors(root: Path) -> None:
    result = run_tool(root, FakeSandbox(done(2, "ERROR collecting test_a.py\nSyntaxError\n")))
    assert "interrupted" in result.message and "import or syntax" in result.message
    assert result.data["passed"] is False


def test_an_unexpected_exit_code_is_an_error_that_names_it(root: Path) -> None:
    result = run_tool(root, FakeSandbox(done(137, "Killed\n")))  # for example out of memory
    assert result.error_code == ErrorCode.INTERNAL
    assert "137" in result.message


def test_a_timeout_is_a_timeout_error_with_the_time_and_what_was_printed(root: Path) -> None:
    slow = FakeSandbox(done(-1, "test_slow ...\n", timed_out=True))
    result = run_tool(root, slow, tool=RunTestsTool(timeout_s=30))
    assert result.error_code == ErrorCode.TIMEOUT
    assert "30 seconds" in result.message


def test_long_output_keeps_the_end_and_says_how_much_was_dropped(root: Path) -> None:
    text = "".join(f"line {n}\n" for n in range(1, 2001)) + "2000 passed in 5s\n"
    result = run_tool(root, FakeSandbox(done(0, text)), tool=RunTestsTool(max_output_chars=200))
    assert result.snapshot is not None
    assert "2000 passed in 5s" in result.snapshot  # the summary pytest prints last
    assert "line 1\n" not in result.snapshot
    assert "last 200 of" in result.snapshot  # the cut is stated


def test_output_cut_by_the_sandbox_is_stated_too(root: Path) -> None:
    result = run_tool(root, FakeSandbox(done(0, "x\n", truncated=True)))
    assert result.snapshot is not None and "sandbox" in result.snapshot and "cut" in result.snapshot


def test_stderr_is_shown_with_stdout(root: Path) -> None:
    result = run_tool(root, FakeSandbox(done(1, "1 failed\n", stderr="Traceback: boom\n")))
    assert result.snapshot is not None and "Traceback: boom" in result.snapshot


# --- Never on the host --------------------------------------------------------


def test_without_a_sandbox_nothing_runs_and_the_reason_is_clear(root: Path) -> None:
    result = run_tool(root, None)
    assert result.error_code == ErrorCode.SAFETY
    assert "sandbox" in result.message and "never" in result.message


def test_when_the_sandbox_is_unavailable_the_tool_says_nothing_was_run(root: Path) -> None:
    result = run_tool(root, FakeSandbox(SandboxUnavailableError("Docker is not running")))
    assert result.error_code == ErrorCode.TRANSIENT
    assert "not available" in result.message and "Nothing was run" in result.message


def test_a_run_without_the_exec_permission_cannot_call_it(root: Path) -> None:
    sandbox = FakeSandbox(done(0))
    result = run_tool(root, sandbox, permissions=[Permission.READ, Permission.WRITE])
    assert result.error_code == ErrorCode.PERMISSION_DENIED
    assert sandbox.calls == []
