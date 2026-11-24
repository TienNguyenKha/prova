"""The two verifiers the harness runs by itself: lint after an edit, tests before the model may stop.

The model cannot call them, cannot skip them and cannot change what they run. Both run in the sandbox, and a
check that could not run is never reported as a check that passed.
"""

from pathlib import Path
from typing import Any

import pytest

from prova.agent.loop import run_agent
from prova.config import Settings
from prova.errors import ErrorCode, SandboxUnavailableError
from prova.harness.hooks import HookEvent, Hooks, run_hooks
from prova.harness.patterns import PathMatcher
from prova.harness.verifiers import LintAfterEdit, TestsBeforeStop
from prova.tools.base import Permission, RunContext, ToolResult
from prova.tools.edit import EditFileTool
from tests.fakes import FakeLLM, FakeSandbox, exec_result, make_tool_registry, text_message, tool_use_message

PYTEST = ["python", "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"]


def edited(
    tmp_path: Path,
    sandbox: FakeSandbox | None,
    *,
    tool: str = "edit_file",
    path: str = "a.py",
    ok: bool = True,
) -> HookEvent:
    ctx = RunContext(workspace=tmp_path, sandbox=sandbox)
    result = ToolResult.ok("edited") if ok else ToolResult.error(ErrorCode.NOT_FOUND, "no")
    return HookEvent(kind="post_tool_use", ctx=ctx, tool=tool, args={"path": path}, result=result)


def stopping(tmp_path: Path, sandbox: FakeSandbox | None) -> HookEvent:
    return HookEvent(kind="stop", ctx=RunContext(workspace=tmp_path, sandbox=sandbox))


# --- Lint after an edit ---------------------------------------------------------


def test_after_editing_a_python_file_it_compiles_it_and_then_runs_ruff_in_the_sandbox(tmp_path: Path) -> None:
    sandbox = FakeSandbox(exec_result(0), exec_result(0))
    LintAfterEdit()(edited(tmp_path, sandbox))
    compile_call, ruff_call = sandbox.calls
    assert compile_call["argv"][:2] == ["python", "-c"] and compile_call["argv"][-1] == "a.py"
    assert "compile(" in compile_call["argv"][2]
    assert ruff_call["argv"] == [
        "python",
        "-m",
        "ruff",
        "check",
        "--no-cache",
        "--output-format=concise",
        "--",
        "a.py",
    ]
    assert compile_call["cwd"] == ruff_call["cwd"] == "/work"


def test_a_clean_file_is_confirmed_in_one_short_line(tmp_path: Path) -> None:
    decision = LintAfterEdit()(edited(tmp_path, FakeSandbox(exec_result(0), exec_result(0))))
    assert decision.action == "allow"
    assert "a.py" in decision.message and "compiles" in decision.message and "ruff" in decision.message


def test_a_file_that_does_not_compile_is_reported_and_ruff_is_not_run(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        exec_result(1, stderr="  File 'a.py', line 3\n    def f(:\nSyntaxError: invalid syntax\n")
    )
    decision = LintAfterEdit()(edited(tmp_path, sandbox))
    assert decision.action == "allow"  # information only: the edit is already done
    assert "does not compile" in decision.message and "SyntaxError: invalid syntax" in decision.message
    assert len(sandbox.calls) == 1


def test_ruff_problems_are_reported_with_what_ruff_said(tmp_path: Path) -> None:
    sandbox = FakeSandbox(exec_result(0), exec_result(1, "a.py:2:1: F401 `os` imported but unused\n"))
    decision = LintAfterEdit()(edited(tmp_path, sandbox))
    assert "ruff found problems" in decision.message and "F401" in decision.message


def test_ruff_failing_to_run_is_not_reported_as_problems_in_the_file(tmp_path: Path) -> None:
    sandbox = FakeSandbox(exec_result(0), exec_result(2, stderr="ruff: no such option\n"))
    decision = LintAfterEdit()(edited(tmp_path, sandbox))
    assert "did not run" in decision.message and "2" in decision.message
    assert "found problems" not in decision.message


def test_a_lint_that_runs_out_of_time_says_so(tmp_path: Path) -> None:
    sandbox = FakeSandbox(exec_result(-1, timed_out=True))
    decision = LintAfterEdit(timeout_s=20)(edited(tmp_path, sandbox))
    assert "did not finish" in decision.message and "20" in decision.message


def test_long_ruff_output_is_cut_and_the_cut_is_stated(tmp_path: Path) -> None:
    sandbox = FakeSandbox(exec_result(0), exec_result(1, "problem line\n" * 500))
    decision = LintAfterEdit(max_output_chars=100)(edited(tmp_path, sandbox))
    assert "showing the first 100 of" in decision.message


@pytest.mark.parametrize("tool", ["edit_file", "create_file"])
def test_both_tools_that_write_code_are_checked(tmp_path: Path, tool: str) -> None:
    sandbox = FakeSandbox(exec_result(0))
    LintAfterEdit()(edited(tmp_path, sandbox, tool=tool))
    assert len(sandbox.calls) == 2


@pytest.mark.parametrize(
    ("tool", "path", "ok"),
    [
        ("edit_file", "README.md", True),  # not python
        ("read_file", "a.py", True),  # not a tool that changes files
        ("edit_file", "a.py", False),  # the edit failed, so nothing changed
        ("edit_file", "../a.py", True),  # outside the workspace: never hand it on
        ("edit_file", ".env", True),
    ],
)
def test_anything_else_is_left_alone_and_costs_no_container(
    tmp_path: Path, tool: str, path: str, ok: bool
) -> None:
    sandbox = FakeSandbox(exec_result(0))
    decision = LintAfterEdit()(edited(tmp_path, sandbox, tool=tool, path=path, ok=ok))
    assert decision == decision.allow() and sandbox.calls == []


def test_without_a_sandbox_the_lint_says_it_did_not_run(tmp_path: Path) -> None:
    decision = LintAfterEdit()(edited(tmp_path, None))
    assert decision.action == "allow" and "did not run" in decision.message and "sandbox" in decision.message


def test_a_sandbox_that_is_gone_makes_the_hook_fail_and_the_pipeline_notes_it(tmp_path: Path) -> None:
    sandbox = FakeSandbox(SandboxUnavailableError("Docker is not running"))
    verdict = run_hooks((LintAfterEdit(),), edited(tmp_path, sandbox))
    assert verdict.notes == ("[lint_after_edit] this check did not run: the hook failed",)


# --- Tests before the model may stop --------------------------------------------


def test_the_gate_runs_the_tests_in_the_sandbox_and_lets_a_green_run_end(tmp_path: Path) -> None:
    sandbox = FakeSandbox(exec_result(0, "12 passed in 0.1s\n"))
    decision = TestsBeforeStop()(stopping(tmp_path, sandbox))
    assert decision.action == "allow"
    assert sandbox.calls == [{"argv": PYTEST, "timeout_s": 120.0, "cwd": "/work"}]


def test_red_tests_keep_the_run_going_and_the_model_is_told_which(tmp_path: Path) -> None:
    sandbox = FakeSandbox(exec_result(1, "FAILED test_a.py::test_x - assert False\n1 failed in 0.1s\n"))
    decision = TestsBeforeStop()(stopping(tmp_path, sandbox))
    assert decision.action == "continue"
    assert "tests fail" in decision.message and "test_a.py::test_x" in decision.message


def test_tests_that_cannot_even_be_collected_keep_the_run_going(tmp_path: Path) -> None:
    sandbox = FakeSandbox(exec_result(2, "ERROR collecting test_a.py\nSyntaxError\n"))
    decision = TestsBeforeStop()(stopping(tmp_path, sandbox))
    assert decision.action == "continue" and "could not be collected" in decision.message


def test_a_project_with_no_tests_has_nothing_to_gate_on_and_may_end(tmp_path: Path) -> None:
    decision = TestsBeforeStop()(stopping(tmp_path, FakeSandbox(exec_result(5, "no tests ran\n"))))
    assert decision.action == "allow" and "no tests" in decision.message


@pytest.mark.parametrize("result", [exec_result(137, "Killed\n"), exec_result(-1, timed_out=True)])
def test_when_the_tests_cannot_be_checked_the_run_may_not_end(tmp_path: Path, result: Any) -> None:
    decision = TestsBeforeStop(timeout_s=30)(stopping(tmp_path, FakeSandbox(result)))
    assert decision.action == "continue" and "could not be checked" in decision.message


def test_without_a_sandbox_the_gate_cannot_check_so_the_run_may_not_end(tmp_path: Path) -> None:
    decision = TestsBeforeStop()(stopping(tmp_path, None))
    assert decision.action == "continue" and "no sandbox" in decision.message


def test_a_sandbox_that_is_gone_keeps_the_run_from_ending_as_done(tmp_path: Path) -> None:
    sandbox = FakeSandbox(SandboxUnavailableError("Docker is not running"))
    verdict = run_hooks((TestsBeforeStop(),), stopping(tmp_path, sandbox))
    assert verdict.blocked and "may not end yet" in verdict.message


def test_a_target_goes_after_two_dashes(tmp_path: Path) -> None:
    sandbox = FakeSandbox(exec_result(0))
    TestsBeforeStop(target="tests")(stopping(tmp_path, sandbox))
    assert sandbox.calls[0]["argv"] == [*PYTEST, "--", "tests"]


def test_long_test_output_keeps_the_end_where_pytest_prints_its_summary(tmp_path: Path) -> None:
    output = "".join(f"noise {n}\n" for n in range(1, 500)) + "1 failed in 3s\n"
    decision = TestsBeforeStop(max_output_chars=100)(stopping(tmp_path, FakeSandbox(exec_result(1, output))))
    assert "1 failed in 3s" in decision.message and "noise 1\n" not in decision.message
    assert "last 100 of" in decision.message


def test_the_hooks_have_names_for_the_trajectory() -> None:
    assert LintAfterEdit().name == "lint_after_edit" and TestsBeforeStop().name == "tests_before_stop"


# --- In a real run ---------------------------------------------------------------


def test_the_gate_sends_the_model_back_to_work_and_lets_it_end_once_the_tests_pass(
    settings: Settings, tmp_path: Path
) -> None:
    sandbox = FakeSandbox(
        exec_result(1, "1 failed in 0.1s\nFAILED test_a.py::test_x\n"), exec_result(0, "1 passed\n")
    )
    llm = FakeLLM([text_message("I fixed it"), text_message("Now it is really fixed")])

    result = run_agent(
        "fix the bug",
        llm=llm,
        tools=make_tool_registry(),
        ctx=RunContext(workspace=tmp_path, sandbox=sandbox),
        settings=settings,
        system="",
        hooks=Hooks(stop=(TestsBeforeStop(),)),
    )

    assert (result.status, result.iterations) == ("done", 2)
    retry: Any = llm.messages.calls[1].messages[-1]
    assert "test_a.py::test_x" in retry["content"]


def test_the_lint_note_reaches_the_model_after_a_real_edit(settings: Settings, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("import os\n", encoding="utf-8")
    sandbox = FakeSandbox(exec_result(0), exec_result(1, "a.py:1:1: F401 `os` imported but unused\n"))
    ctx = RunContext(
        workspace=tmp_path,
        permissions=frozenset({Permission.READ, Permission.WRITE}),
        write=PathMatcher(("**",)),
        sandbox=sandbox,
    )
    edit = tool_use_message(
        ("t1", "edit_file", {"path": "a.py", "old_str": "import os", "new_str": "import sys"})
    )

    result = run_agent(
        "tidy the imports",
        llm=FakeLLM([edit, text_message("done")]),
        tools=make_tool_registry(EditFileTool()),
        ctx=ctx,
        settings=settings,
        system="",
        hooks=Hooks(post=(LintAfterEdit(),)),
    )

    answers: Any = result.messages[2]["content"]
    text = answers[0]["content"]
    assert "[lint_after_edit] ruff found problems" in text and "F401" in text
