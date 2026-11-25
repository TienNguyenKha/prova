"""The fix loop as a benchmark agent: the same runner, grading and result line as a single run of the product.

A fake product stands in for the model, so nothing here costs money, and every command the sandbox gets is the
loop's own check. What is checked is the wiring: one sandbox and one trajectory for the whole loop, the
budget of the task shared by all the attempts (each gets only what is left), the usage of every attempt added
up for the runner to price, and the record of the loop kept next to the trajectory.
"""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bench.loop_agent import CHECK, LoopAgent, _tool_calls  # pyright: ignore[reportPrivateUsage]
from bench.task import Task, load_task
from prova.agent.budget import Budget, Pricing
from prova.agent.loop import AgentResult
from prova.harness.redact import Redactor
from prova.harness.trajectory import TrajectoryWriter
from prova.llm.usage import UsageLog, UsageRecord
from prova.sandbox.base import ExecResult, Sandbox
from tests.fakes import FakeSandbox
from tests.unit.agent.test_fingerprint import EQ, KEY, output
from tests.unit.bench.test_task import make_task

PRICING = Pricing(input_per_mtok=2.0, output_per_mtok=10.0)


def check(exit_code: int, text: str = "") -> ExecResult:
    return ExecResult(exit_code, text, "", timed_out=False, duration_s=0.1, truncated=False)


RED_EQ, RED_KEY, GREEN, NO_TESTS = check(1, output(EQ)), check(1, output(KEY)), check(0, "1 passed"), check(5)


def usage(tokens_in: int = 10_000, tokens_out: int = 1_000) -> UsageLog:  # $0.03 at PRICING
    log = UsageLog()
    log.add(UsageRecord(turn=1, input_tokens=tokens_in, output_tokens=tokens_out))
    return log


def calls(n: int) -> tuple[Any, ...]:
    """The messages of a run that made `n` tool calls."""
    blocks = [{"type": "tool_use", "id": f"t{i}", "name": "read_file", "input": {}} for i in range(n)]
    return ({"role": "user", "content": "x"}, {"role": "assistant", "content": blocks})


class Product:
    """Stands in for the product: each attempt writes the next version of a file and reports what it used."""

    def __init__(self, checks: list[ExecResult], runs: list[AgentResult] | None = None) -> None:
        self.pricing = PRICING
        self.config: dict[str, Any] = {"model": "claude-test", "prompt_hash": "h" * 64}
        self.sandbox = FakeSandbox(*checks)
        self.opened: list[Path] = []
        self.closed = 0
        self.runs = runs
        self.seen: list[dict[str, Any]] = []
        self.after_attempt: Callable[[], None] = lambda: None

    def clock(self) -> datetime:
        return datetime(2026, 12, 1, tzinfo=UTC)

    @contextmanager
    def open_sandbox(self, workspace: Path) -> Iterator[Sandbox]:
        self.opened.append(workspace)
        try:
            yield self.sandbox
        finally:
            self.closed += 1

    def attempt(  # noqa: PLR0913 - the same as the product's
        self,
        prompt: str,
        *,
        sandbox: Sandbox,
        workspace: Path,
        writer: TrajectoryWriter,
        budget: Budget,
        redactor: Redactor,
    ) -> AgentResult:
        n = len(self.seen) + 1
        self.seen.append({"prompt": prompt, "sandbox": sandbox, "workspace": workspace, "writer": writer})
        self.seen[-1]["budget"] = budget
        (workspace / "fix.py").write_text(f"v{n}\n", encoding="utf-8")
        self.after_attempt()
        if self.runs:
            return self.runs[min(n, len(self.runs)) - 1]
        return AgentResult("done", "ok", 1, usage(), calls(2))


class Rig:
    def __init__(self, tmp_path: Path, product: Product, **budget: Any) -> None:
        limits = {"max_tool_calls": 40, "max_usd": 0.5, "max_seconds": 600, **budget}
        self.task: Task = load_task(make_task(tmp_path / "tasks", {"budget": limits}))
        self.product = product
        self.workspace = tmp_path / "agent"
        self.workspace.mkdir()
        self.trajectory = tmp_path / "out" / "trajectories" / "run1-t001-s0.jsonl"
        self.time = 0.0

    def run(self, max_attempts: int = 4) -> Any:
        agent = LoopAgent(self.product, max_attempts=max_attempts, seconds=lambda: self.time)  # type: ignore[arg-type]
        return agent(self.task, self.workspace, self.trajectory)


def rig(tmp_path: Path, *checks: ExecResult, runs: list[AgentResult] | None = None, **budget: Any) -> Rig:
    return Rig(tmp_path, Product(list(checks), runs), **budget)


# --- one sandbox, one workspace, one trajectory --------------------------------------------------------


def test_the_whole_loop_has_one_sandbox_one_workspace_and_one_trajectory(tmp_path: Path) -> None:
    r = rig(tmp_path, RED_EQ, RED_KEY, GREEN)
    r.run()
    assert r.product.opened == [r.workspace] and r.product.closed == 1
    assert len(r.product.seen) == 2
    assert all(s["sandbox"] is r.product.sandbox and s["workspace"] == r.workspace for s in r.product.seen)
    assert r.product.seen[0]["writer"] is r.product.seen[1]["writer"]


def test_the_check_is_the_visible_tests_run_in_the_sandbox(tmp_path: Path) -> None:
    r = rig(tmp_path, RED_EQ, GREEN)
    r.run()
    assert [c["argv"] for c in r.product.sandbox.calls] == [list(CHECK)] * 2
    assert CHECK == ("python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests")


def test_the_first_prompt_has_the_issue_as_data_and_the_check(tmp_path: Path) -> None:
    r = rig(tmp_path, RED_EQ, GREEN)
    r.run()
    first = r.product.seen[0]["prompt"]
    assert first.startswith("<untrusted_issue>") and r.task.issue in first
    assert "python -m pytest -q -p no:cacheprovider tests" in first


def test_the_second_prompt_says_what_failed_after_the_first_attempt(tmp_path: Path) -> None:
    r = rig(tmp_path, RED_EQ, RED_KEY, GREEN)
    r.run()
    second = r.product.seen[1]["prompt"]
    assert "test_a.py::test_key" in second and "fix.py" in second  # the failure, and what was changed


# --- what the runner gets ------------------------------------------------------------------------------


def test_a_loop_that_meets_the_goal_is_done_with_the_usage_of_every_attempt(tmp_path: Path) -> None:
    two_turns = usage()
    two_turns.add(UsageRecord(turn=2, input_tokens=10_000, output_tokens=1_000))
    r = rig(tmp_path, RED_EQ, RED_KEY, GREEN, runs=[AgentResult("done", "", 2, two_turns, calls(1))])
    run = r.run()
    assert run.status == "done", run.detail
    assert [r.turn for r in run.usage.records] == [1, 2, 3, 4]  # each attempt counts its turns from 1
    assert run.usage.total_input_tokens == 40_000


def test_a_loop_that_gives_up_says_why_in_its_status(tmp_path: Path) -> None:
    r = rig(tmp_path, RED_EQ)
    run = r.run(max_attempts=3)
    assert run.status == "escalated:stuck" and "attempts" in run.detail


def test_a_task_with_no_visible_test_is_unverifiable_and_costs_nothing(tmp_path: Path) -> None:
    r = rig(tmp_path, NO_TESTS)
    run = r.run()
    assert run.status == "escalated:unverifiable" and r.product.seen == []
    assert run.usage.records == ()


def test_the_record_of_the_loop_is_kept_next_to_the_trajectory(tmp_path: Path) -> None:
    r = rig(tmp_path, RED_EQ)
    r.run(max_attempts=2)
    state = json.loads((r.trajectory.parent / "run1-t001-s0.state.json").read_text(encoding="utf-8"))
    assert state["status"] == "escalated" and state["attempts"] == 2
    assert "run1-t001-s0.state.json" in (r.trajectory.parent / "escalations.md").read_text(encoding="utf-8")


def test_the_config_says_it_is_the_loop_and_how_it_was_set(tmp_path: Path) -> None:
    r = rig(tmp_path, GREEN)
    agent = LoopAgent(r.product, max_attempts=5)  # type: ignore[arg-type]
    assert agent.config["model"] == "claude-test" and agent.config["prompt_hash"] == "h" * 64
    assert agent.config["loop"] == {"max_attempts": 5, "check": list(CHECK)}


# --- the budget of the task is the loop's --------------------------------------------------------------


def test_each_attempt_gets_only_what_is_left_of_the_task_budget(tmp_path: Path) -> None:
    first_run = AgentResult("done", "", 1, usage(), calls(15))  # $0.03 and 15 tool calls
    r = rig(
        tmp_path, RED_EQ, RED_KEY, GREEN, runs=[first_run], max_tool_calls=40, max_usd=0.5, max_seconds=600
    )
    r.product.after_attempt = lambda: setattr(r, "time", r.time + 100)  # each attempt takes 100 seconds
    r.run()
    first, second = (s["budget"] for s in r.product.seen)
    assert (first.max_tool_calls, first.max_cost_usd, first.max_seconds) == (40, 0.5, 600)
    assert second.max_tool_calls == 25 and second.max_seconds == pytest.approx(500)
    assert second.max_cost_usd == pytest.approx(0.5 - 0.03)
    assert second.pricing == PRICING


@pytest.mark.parametrize(
    ("limits", "first_run"),
    [
        ({"max_tool_calls": 10}, AgentResult("done", "", 1, usage(), calls(10))),
        ({"max_usd": 0.03}, AgentResult("done", "", 1, usage(), calls(1))),
        ({"max_seconds": 100}, AgentResult("done", "", 1, usage(), calls(1))),
    ],
)
def test_nothing_left_of_the_budget_means_no_more_attempts(
    tmp_path: Path, limits: dict[str, Any], first_run: AgentResult
) -> None:
    r = rig(tmp_path, RED_EQ, RED_KEY, runs=[first_run], **limits)  # the check stays red
    r.product.after_attempt = lambda: setattr(r, "time", r.time + 100)
    run = r.run()
    assert len(r.product.seen) == 1 and run.status == "escalated:budget"


def test_an_attempt_that_ran_out_of_budget_ends_the_loop(tmp_path: Path) -> None:
    out = AgentResult("budget", "the run cost $0.51", 3, usage(), calls(1))
    r = rig(tmp_path, RED_EQ, RED_KEY, GREEN, runs=[out])
    run = r.run()
    assert len(r.product.seen) == 1 and run.status == "escalated:budget"


def test_only_the_tool_calls_the_model_asked_for_are_counted() -> None:
    messages = [
        {"role": "user", "content": "the task"},
        {"role": "assistant", "content": [SimpleNamespace(type="text"), SimpleNamespace(type="tool_use")]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a"}]},
        {"role": "assistant", "content": [{"type": "tool_use"}, {"type": "tool_use"}, {"type": "text"}]},
        {"role": "assistant", "content": "a final answer"},
    ]
    assert _tool_calls(messages) == 3
