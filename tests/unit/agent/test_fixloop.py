"""The loop that fixes, tests and fixes again, and gives up in the right way.

The agent, the files and the check are all handed in, so the loop is tested with fakes and costs nothing.
What is checked is the loop's own rules: when it is done, when it stops and why, what the agent is told on
the next attempt, and that giving up leaves a record (a state file and one line in escalations.md). The
rule that matters most: a check that cannot show anything is never a pass.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from prova.agent.budget import Pricing
from prova.agent.fixloop import AttemptRun, Goal, LoopResult, Verification, run_fix_loop
from prova.harness.redact import Redactor
from prova.llm.usage import UsageLog, UsageRecord
from tests.unit.agent.test_fingerprint import EQ, KEY, output

PRICING = Pricing(input_per_mtok=2.0, output_per_mtok=10.0)
NOW = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
PASSED = Verification(exit_code=0, output=".\n1 passed in 0.00s")
RED_EQ = Verification(exit_code=1, output=output(EQ))
RED_KEY = Verification(exit_code=1, output=output(KEY))
NO_TESTS = Verification(exit_code=5, output="no tests ran in 0.00s")


def usage(tokens_in: int = 1000, tokens_out: int = 500, cache: int = 0) -> UsageLog:
    log = UsageLog()
    log.add(
        UsageRecord(turn=1, input_tokens=tokens_in, output_tokens=tokens_out, cache_read_input_tokens=cache)
    )
    return log


def goal(**changes: Any) -> Goal:
    data: dict[str, Any] = {
        "statement": "make the guard flag stacked override phrases",
        "termination_check": ["python", "-m", "pytest", "-q"],
        "max_attempts": 4,
        "max_usd": 1.0,
        "escalation": "stop_and_report",
    }
    return Goal(**{**data, **changes})


class Rig:
    """An agent that changes a file each time it is asked, a check with scripted answers, and the folders."""

    def __init__(self, tmp_path: Path, checks: list[Verification]) -> None:
        self.files: dict[str, str] = {"guard.py": "v0\n"}
        self.checks = list(checks)
        self.prompts: list[str] = []
        self.verified = 0
        self.escalations = tmp_path / "escalations.md"
        self.state = tmp_path / "state" / "loop.json"
        self.agent: Callable[[int], AttemptRun] = lambda n: AttemptRun("done", "", usage())

    def attempt(self, prompt: str) -> AttemptRun:
        self.prompts.append(prompt)
        n = len(self.prompts)
        self.files["guard.py"] = f"v{n}\n"  # the agent changes the file, whatever it says about it
        return self.agent(n)

    def verify(self) -> Verification:
        self.verified += 1
        return self.checks.pop(0) if len(self.checks) > 1 else self.checks[0]

    def run(self, the_goal: Goal | None = None, redactor: Redactor | None = None) -> LoopResult:
        return run_fix_loop(
            the_goal or goal(), attempt=self.attempt, read_files=lambda: dict(self.files), verify=self.verify,
            pricing=PRICING, escalations_md=self.escalations, state_file=self.state, now=lambda: NOW,
            redactor=redactor or Redactor(),
        )  # fmt: skip

    def saved(self) -> dict[str, Any]:
        return json.loads(self.state.read_text(encoding="utf-8"))


@pytest.fixture
def rig(tmp_path: Path) -> Callable[..., Rig]:
    return lambda *checks: Rig(tmp_path, list(checks))


# --- the goal ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"termination_check": []},  # a goal with no check cannot be met or missed
        {"max_attempts": 0},
        {"max_usd": 0},
        {"escalation": "shrug"},
        {"unknown_field": 1},
    ],
)
def test_a_goal_that_cannot_be_checked_or_bounded_is_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        goal(**bad)


# --- done -------------------------------------------------------------------------------------------


def test_a_check_that_passes_on_the_first_attempt_is_done_and_leaves_a_record_but_no_escalation(
    rig: Callable[..., Rig],
) -> None:
    r = rig(PASSED)
    result = r.run()
    assert result.status == "done" and result.reason is None and result.attempts == 1
    assert r.saved()["status"] == "done" and not r.escalations.exists()


def test_a_fix_on_the_second_attempt_is_done_and_the_third_is_never_asked_for(
    rig: Callable[..., Rig],
) -> None:
    r = rig(RED_EQ, PASSED)
    result = r.run()
    assert result.status == "done" and result.attempts == 2 and len(r.prompts) == 2
    assert len(result.lessons) == 1  # the lesson of the attempt that failed


# --- what the agent is told -------------------------------------------------------------------------


def test_the_first_prompt_has_the_goal_as_data_and_the_check_and_no_feedback(rig: Callable[..., Rig]) -> None:
    r = rig(PASSED)
    r.run()
    first = r.prompts[0]
    assert "<untrusted_issue>" in first and "make the guard flag stacked override phrases" in first
    assert "python -m pytest -q" in first and "did not meet" not in first


def test_the_next_prompt_has_the_structured_feedback_and_what_was_already_tried(
    rig: Callable[..., Rig],
) -> None:
    r = rig(RED_EQ, PASSED)
    r.run()
    second = r.prompts[1]
    assert "did not meet" in second and "test_a.py::test_eq" in second
    assert "same failure" not in second  # the failure of the attempt just made is not "back"
    assert "Already tried" in second and "guard.py" in second  # from the files, not from the agent's word
    assert "FAILED test_a.py" not in second.replace("test_a.py::test_eq: assert", "")  # no raw output


def test_a_check_that_timed_out_is_not_a_pass_even_if_it_says_exit_zero(rig: Callable[..., Rig]) -> None:
    result = rig(Verification(exit_code=0, output="1 passed", timed_out=True)).run(goal(max_attempts=2))
    assert result.status == "escalated" and result.attempts == 2


def test_what_a_check_that_timed_out_printed_is_not_believed(rig: Callable[..., Rig]) -> None:
    half = Verification(exit_code=-1, output=output(EQ), timed_out=True)  # it showed a failure, then it hung
    result = rig(half).run(goal(max_attempts=2))
    assert result.reason == "max_attempts"  # not "stuck": the two lessons cannot be called the same
    assert all(lesson.fingerprint is None for lesson in result.lessons)


def test_a_file_the_agent_made_is_part_of_what_was_tried(rig: Callable[..., Rig]) -> None:
    r = rig(RED_EQ, PASSED)
    original = r.attempt

    def attempt_that_adds_a_file(prompt: str) -> AttemptRun:
        r.files["new_helper.py"] = "x = 1\n"
        return original(prompt)

    result = run_fix_loop(
        goal(), attempt=attempt_that_adds_a_file, read_files=lambda: dict(r.files), verify=r.verify,
        pricing=PRICING, escalations_md=r.escalations, state_file=r.state, now=lambda: NOW,
    )  # fmt: skip
    assert "created new_helper.py" in result.lessons[0].tried


# --- when it gives up -------------------------------------------------------------------------------


def test_the_same_failure_twice_in_a_row_is_stuck_and_stops_at_once(rig: Callable[..., Rig]) -> None:
    r = rig(RED_EQ)
    result = r.run()
    assert (result.status, result.reason, result.attempts) == ("escalated", "stuck", 2)


def test_a_failure_that_came_back_after_another_one_is_not_stuck(rig: Callable[..., Rig]) -> None:
    r = rig(RED_EQ, RED_KEY, RED_EQ)
    result = r.run(goal(max_attempts=3))
    assert (result.reason, result.attempts) == ("max_attempts", 3)


def test_output_that_shows_no_failure_is_never_the_same_as_the_last_so_it_is_not_stuck(
    rig: Callable[..., Rig],
) -> None:
    unreadable = Verification(exit_code=2, output="Traceback ...\nRuntimeError: boom")
    result = rig(unreadable).run(goal(max_attempts=3))
    assert (result.reason, result.attempts) == ("max_attempts", 3)


def test_a_check_that_did_not_finish_is_a_failure_that_cannot_be_read_and_the_loop_goes_on(
    rig: Callable[..., Rig],
) -> None:
    slow = Verification(exit_code=-1, output="", timed_out=True)
    result = rig(slow, PASSED).run()
    assert result.status == "done" and "could not tell" in result.lessons[0].failed_because


def test_attempts_are_limited_and_the_last_one_is_the_limit(rig: Callable[..., Rig]) -> None:
    r = rig(RED_EQ, RED_KEY)
    result = r.run(goal(max_attempts=2))
    assert (result.reason, result.attempts, len(r.prompts)) == ("max_attempts", 2, 2)


def test_a_check_that_finds_no_test_is_unverifiable_and_never_a_pass(rig: Callable[..., Rig]) -> None:
    result = rig(NO_TESTS).run()
    assert (result.status, result.reason, result.attempts) == ("escalated", "unverifiable", 1)


def test_the_money_limit_stops_the_loop_before_another_attempt(rig: Callable[..., Rig]) -> None:
    r = rig(RED_EQ, RED_KEY)
    r.agent = lambda n: AttemptRun("done", "", usage(tokens_in=1_000_000, tokens_out=0))  # $2 for one attempt
    result = r.run(goal(max_usd=1.0))
    assert (result.reason, result.attempts, len(r.prompts)) == ("budget", 1, 1)
    assert result.cost_usd == pytest.approx(2.0)


def test_the_money_is_counted_over_all_the_attempts_and_not_one_at_a_time(rig: Callable[..., Rig]) -> None:
    r = rig(RED_EQ, RED_KEY, RED_EQ)
    r.agent = lambda n: AttemptRun("done", "", usage(tokens_in=300_000, tokens_out=0))  # $0.60 for each
    result = r.run(goal(max_usd=1.0, max_attempts=5))
    assert (result.reason, result.attempts) == ("budget", 2)  # $0.60 is under the limit, $1.20 is not
    assert result.cost_usd == pytest.approx(1.2)


def test_a_cost_that_cannot_be_known_stops_the_loop_because_the_fence_cannot_be_kept(
    rig: Callable[..., Rig],
) -> None:
    r = rig(RED_EQ, RED_KEY)
    r.agent = lambda n: AttemptRun("done", "", usage(cache=100))  # a price for cache tokens is not known
    result = r.run()
    assert result.reason == "budget" and result.cost_usd is None and "cannot be known" in result.detail


def test_an_agent_that_crashes_ends_in_an_escalation_that_keeps_the_lessons_so_far(
    rig: Callable[..., Rig],
) -> None:
    r = rig(RED_EQ, RED_KEY)

    def crash_on_the_second(n: int) -> AttemptRun:
        if n == 2:
            raise RuntimeError("the API went away")
        return AttemptRun("done", "", usage())

    r.agent = crash_on_the_second
    result = r.run()
    assert (result.status, result.reason, result.attempts) == ("escalated", "error", 2)
    assert "the API went away" in result.detail and len(result.lessons) == 1


def test_a_goal_with_open_questions_is_not_attempted_it_is_escalated(rig: Callable[..., Rig]) -> None:
    r = rig(PASSED)
    asking = goal(open_questions=["Which phrases count as stacked?"], escalation="ask_human")
    result = r.run(asking)
    assert (result.reason, result.attempts, r.prompts) == ("open_questions", 0, [])
    assert result.question is not None and "Which phrases count as stacked?" in result.question
    assert result.question.startswith("I need an answer")


# --- the record -------------------------------------------------------------------------------------


def test_an_escalation_is_one_line_in_escalations_md_and_the_next_one_is_added_under_it(
    rig: Callable[..., Rig],
) -> None:
    r = rig(RED_EQ)
    r.run()
    r.run()
    lines = r.escalations.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "2026-12-01" in lines[0] and "stuck" in lines[0] and "stacked override phrases" in lines[0]
    assert str(r.state) in lines[0]


def test_the_state_file_has_the_goal_the_reason_the_attempts_and_the_lessons(rig: Callable[..., Rig]) -> None:
    r = rig(RED_EQ)
    r.run()
    saved = r.saved()
    assert saved["status"] == "escalated" and saved["reason"] == "stuck" and saved["attempts"] == 2
    assert saved["goal"]["statement"].startswith("make the guard") and len(saved["lessons"]) == 2
    assert saved["lessons"][0]["fingerprint"] is not None and "guard.py" in saved["lessons"][0]["tried"]


def test_what_the_redactor_hides_is_in_neither_file(rig: Callable[..., Rig]) -> None:
    secret = "sk-ant-api03-THIS-IS-A-FAKE-KEY-0123456789"  # noqa: S105 - a fake key, to see it hidden
    leaking = Verification(exit_code=1, output=output(f"FAILED t.py::test_x - KeyError: '{secret}'"))
    r = rig(leaking)
    r.run(goal(statement=f"fix it, the key is {secret}"))
    assert secret not in r.state.read_text(encoding="utf-8")
    assert secret not in r.escalations.read_text(encoding="utf-8")


def test_ask_human_gives_a_question_and_stop_and_report_does_not(rig: Callable[..., Rig]) -> None:
    asked = rig(RED_EQ).run(goal(escalation="ask_human"))
    reported = rig(RED_EQ).run(goal(escalation="stop_and_report"))
    assert asked.question is not None and "stuck" in asked.question
    assert reported.question is None
