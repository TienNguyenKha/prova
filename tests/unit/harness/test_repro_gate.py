"""The stop hook of a reproduction run: the model may end only once its tests show the bug.

It is the reverse of the gate of a fix run: here the tests must FAIL, and for the right reason (see
prova.agent.repro). The hook runs them in the sandbox, so the model can neither skip it nor change what it
runs; when they do not show the bug, the model is sent back with the reason. A check that could not run never
lets the run end.
"""

from pathlib import Path
from typing import Any

import pytest

from prova.agent.loop import run_agent
from prova.agent.repro import REPRO_ARGV
from prova.config import Settings
from prova.errors import SandboxUnavailableError
from prova.harness.hooks import HookEvent, Hooks, run_hooks
from prova.harness.verifiers import ReproFailsBeforeStop
from prova.tools.base import RunContext
from tests.fakes import FakeLLM, FakeSandbox, exec_result, make_tool_registry, text_message
from tests.unit.agent.test_repro import ASSERTION, IMPORT_ERROR, IN_THE_TEST, SUMMARY_LINES, run


def stopping(tmp_path: Path, sandbox: FakeSandbox | None) -> HookEvent:
    return HookEvent(kind="stop", ctx=RunContext(workspace=tmp_path, sandbox=sandbox))


SHOWS_THE_BUG = exec_result(1, run(ASSERTION, summary=[SUMMARY_LINES["assertion"]]))
BREAKS_ON_A_TYPO = exec_result(1, run(IN_THE_TEST, summary=[SUMMARY_LINES["typo"]]))


def test_the_reproduction_tests_are_run_in_the_sandbox_with_native_tracebacks(tmp_path: Path) -> None:
    sandbox = FakeSandbox(SHOWS_THE_BUG)
    ReproFailsBeforeStop()(stopping(tmp_path, sandbox))
    assert sandbox.calls == [{"argv": REPRO_ARGV, "timeout_s": 120.0, "cwd": "/work"}]
    assert "--tb=native" in REPRO_ARGV and REPRO_ARGV[-2:] == ["--", "repro"]


def test_tests_that_fail_for_the_right_reason_let_the_run_end(tmp_path: Path) -> None:
    decision = ReproFailsBeforeStop()(stopping(tmp_path, FakeSandbox(SHOWS_THE_BUG)))
    assert decision.action == "allow" and "test_add" in decision.message


@pytest.mark.parametrize(
    ("result", "said"),
    [
        (BREAKS_ON_A_TYPO, "NameError raised in the test itself"),
        (exec_result(2, IMPORT_ERROR), "could not be collected (ImportError)"),
        (exec_result(0, "1 passed in 0.01s\n"), "every test passes"),
        (exec_result(5, "no tests ran in 0.00s\n"), "found no test"),
    ],
)
def test_tests_that_do_not_show_the_bug_send_the_model_back_with_the_reason(
    tmp_path: Path, result: Any, said: str
) -> None:
    decision = ReproFailsBeforeStop()(stopping(tmp_path, FakeSandbox(result)))
    assert decision.action == "continue"
    assert "do not show the bug yet" in decision.message and said in decision.message


def test_output_that_was_cut_is_not_taken_for_a_reproduction(tmp_path: Path) -> None:
    cut = exec_result(1, run(ASSERTION, summary=[SUMMARY_LINES["assertion"]]), truncated=True)
    decision = ReproFailsBeforeStop()(stopping(tmp_path, FakeSandbox(cut)))
    assert decision.action == "continue" and "cut" in decision.message


def test_tests_that_ran_out_of_time_cannot_end_the_run(tmp_path: Path) -> None:
    decision = ReproFailsBeforeStop(timeout_s=30)(
        stopping(tmp_path, FakeSandbox(exec_result(-1, timed_out=True)))
    )
    assert decision.action == "continue" and "30 seconds" in decision.message


def test_without_a_sandbox_the_run_may_not_end(tmp_path: Path) -> None:
    decision = ReproFailsBeforeStop()(stopping(tmp_path, None))
    assert decision.action == "continue" and "no sandbox" in decision.message


def test_a_sandbox_that_is_gone_keeps_the_run_from_ending(tmp_path: Path) -> None:
    sandbox = FakeSandbox(SandboxUnavailableError("Docker is not running"))
    verdict = run_hooks((ReproFailsBeforeStop(),), stopping(tmp_path, sandbox))
    assert verdict.blocked


def test_the_end_of_long_output_is_shown_when_the_model_is_sent_back(tmp_path: Path) -> None:
    output = "".join(f"noise {n}\n" for n in range(500)) + run(IN_THE_TEST, summary=[SUMMARY_LINES["typo"]])
    decision = ReproFailsBeforeStop(max_output_chars=200)(
        stopping(tmp_path, FakeSandbox(exec_result(1, output)))
    )
    assert "short test summary" in decision.message and "noise 1\n" not in decision.message


def test_the_hook_has_a_name_for_the_trajectory_and_the_contract() -> None:
    assert ReproFailsBeforeStop().name == "repro_fails_before_stop"


def test_in_a_real_run_the_model_is_sent_back_until_its_test_shows_the_bug(
    settings: Settings, tmp_path: Path
) -> None:
    sandbox = FakeSandbox(BREAKS_ON_A_TYPO, SHOWS_THE_BUG)
    llm = FakeLLM([text_message("I wrote the test"), text_message("Fixed my typo")])
    result = run_agent(
        "write a test that shows the bug",
        llm=llm,
        tools=make_tool_registry(),
        ctx=RunContext(workspace=tmp_path, sandbox=sandbox),
        settings=settings,
        system="",
        hooks=Hooks(stop=(ReproFailsBeforeStop(),)),
    )
    assert (result.status, result.iterations) == ("done", 2)
    retry: Any = llm.messages.calls[1].messages[-1]
    assert "NameError raised in the test itself" in retry["content"]
