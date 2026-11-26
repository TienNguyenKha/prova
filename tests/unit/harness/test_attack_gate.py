"""The stop hook of an adversary run (M14): it may end only once it has tried at least one attack, as a test.

Unlike a reproduction run, an adversary may end with every attack failing (the patch held): that is a
"pass". What it may not do is pass without trying, or hand in an attack that is itself broken (it cannot be
imported, or it breaks on its own mistake). The verdict is not this hook's: the harness reads it from the
attack tests.
"""

from pathlib import Path
from typing import Any

import pytest

from prova.agent.repro import ATTACK_ARGV, REPRO_ARGV, pytest_argv
from prova.errors import SandboxUnavailableError
from prova.harness.hooks import HookEvent, run_hooks
from prova.harness.verifiers import AttacksWrittenBeforeStop
from prova.tools.base import RunContext
from tests.fakes import FakeSandbox, exec_result
from tests.unit.agent.test_repro import ASSERTION, IMPORT_ERROR, IN_CODE, IN_THE_TEST, SUMMARY_LINES, run


def stopping(tmp_path: Path, sandbox: FakeSandbox | None) -> HookEvent:
    return HookEvent(kind="stop", ctx=RunContext(workspace=tmp_path, sandbox=sandbox))


def test_the_attack_tests_run_like_the_reproduction_tests_in_their_own_folder() -> None:
    assert pytest_argv("repro") == REPRO_ARGV and pytest_argv("attack") == ATTACK_ARGV
    assert ATTACK_ARGV[-2:] == ["--", "attack"] and "--tb=native" in ATTACK_ARGV


def test_the_attacks_are_run_in_the_sandbox(tmp_path: Path) -> None:
    sandbox = FakeSandbox(exec_result(0, "2 passed in 0.01s\n"))
    AttacksWrittenBeforeStop()(stopping(tmp_path, sandbox))
    assert sandbox.calls == [{"argv": ATTACK_ARGV, "timeout_s": 120.0, "cwd": "/work"}]


@pytest.mark.parametrize(
    "result",
    [
        exec_result(0, "2 passed in 0.01s\n"),  # every attack failed: the patch held
        exec_result(1, run(ASSERTION, summary=[SUMMARY_LINES["assertion"]])),  # an attack broke it
        exec_result(1, run(IN_CODE, summary=[SUMMARY_LINES["in_code"]])),
    ],
)
def test_attacks_that_ran_let_the_run_end_whatever_they_found(tmp_path: Path, result: Any) -> None:
    decision = AttacksWrittenBeforeStop()(stopping(tmp_path, FakeSandbox(result)))
    assert decision.action == "allow"


@pytest.mark.parametrize(
    ("result", "said"),
    [
        (exec_result(5, "no tests ran in 0.00s\n"), "no attack"),
        (
            exec_result(1, run(IN_THE_TEST, summary=[SUMMARY_LINES["typo"]])),
            "NameError raised in the test itself",
        ),
        (exec_result(2, IMPORT_ERROR), "could not be collected"),
        (exec_result(-1, timed_out=True), "did not finish"),
        (exec_result(1, "garbage"), "does not show"),
    ],
)
def test_no_attack_or_a_broken_one_sends_the_model_back(tmp_path: Path, result: Any, said: str) -> None:
    decision = AttacksWrittenBeforeStop()(stopping(tmp_path, FakeSandbox(result)))
    assert decision.action == "continue" and said in decision.message


def test_without_a_sandbox_or_with_one_that_is_gone_the_run_may_not_end(tmp_path: Path) -> None:
    assert AttacksWrittenBeforeStop()(stopping(tmp_path, None)).action == "continue"
    gone = FakeSandbox(SandboxUnavailableError("Docker is not running"))
    assert run_hooks((AttacksWrittenBeforeStop(),), stopping(tmp_path, gone)).blocked


def test_the_hook_has_a_name_for_the_trajectory_and_the_contract() -> None:
    assert AttacksWrittenBeforeStop().name == "attacks_written_before_stop"


def test_an_attack_whose_output_was_cut_cannot_be_read_so_the_model_is_sent_back(tmp_path: Path) -> None:
    cut = exec_result(1, run(ASSERTION, summary=[SUMMARY_LINES["assertion"]]), truncated=True)
    decision = AttacksWrittenBeforeStop()(stopping(tmp_path, FakeSandbox(cut)))
    assert decision.action == "continue" and "cut" in decision.message
