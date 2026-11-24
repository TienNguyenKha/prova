"""Running one task: a workspace, an agent, a grade, and one line of result that is written whatever happens.

The agent is a function the test hands in, so nothing here asks a model anything. What is checked is what the
runner does around it: where the agent works, what it is not shown, what is measured, and that a crash or a
cost that cannot be known ends as a line that says so and not as a missing line or a made-up number.
"""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from bench.runner import AgentRun, run_task
from bench.task import Task
from prova.agent.budget import Pricing
from prova.llm.usage import UsageLog, UsageRecord
from tests.unit.bench.test_grade import ORIGINAL, Pytest, Rig, fixed_if_it_says_ten

PRICING = Pricing(input_per_mtok=2.0, output_per_mtok=10.0)
CONFIG = {"model": "fake-model", "prompt_hash": "0" * 64, "price_table_as_of": "2026-11-03"}


def usage(*records: UsageRecord) -> UsageLog:
    log = UsageLog()
    for record in records:
        log.add(record)
    return log


ONE_TURN = usage(UsageRecord(turn=1, input_tokens=1000, output_tokens=500))  # $0.002 + $0.005


class Clock:
    """Time that moves 12.5 seconds each time it is read."""

    def __init__(self) -> None:
        self.now = -12.5

    def __call__(self) -> float:
        self.now += 12.5
        return self.now


Agent = Callable[[Task, Path, Path], AgentRun]


class Runner:
    def __init__(self, tmp_path: Path) -> None:
        self.rig = Rig(tmp_path)
        self.results = tmp_path / "bench" / "results" / "run1.jsonl"
        self.seen: list[dict[str, Any]] = []
        self.workspaces: list[Path] = []

    def run(self, agent: Agent, *, pricing: Pricing | None = PRICING, seed: int = 0) -> dict[str, Any]:
        @contextmanager
        def open_sandbox(workdir: Path) -> Iterator[Pytest]:
            yield Pytest(workdir, fixed_if_it_says_ten, self.seen, report=True, decoy=False)

        def spy(task: Task, workspace: Path, trajectory: Path) -> AgentRun:
            self.workspaces.append(workspace)
            return agent(task, workspace, trajectory)

        return run_task(
            self.rig.task_dir, agent=spy, open_sandbox=open_sandbox, pricing=pricing,
            results=self.results, run_id="run1", seed=seed, config=CONFIG, clock=Clock(),
        )  # fmt: skip

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.results.read_text(encoding="utf-8").splitlines()]


def fixes_it(task: Task, workspace: Path, trajectory: Path) -> AgentRun:
    (workspace / "mod.py").write_text(ORIGINAL.replace("return 1", "return 10"), encoding="utf-8")
    return AgentRun(status="done", detail="", usage=ONE_TURN)


def does_nothing(task: Task, workspace: Path, trajectory: Path) -> AgentRun:
    return AgentRun(status="done", detail="", usage=ONE_TURN)


@pytest.fixture
def runner(tmp_path: Path) -> Runner:
    return Runner(tmp_path)


# --- the line of result ---------------------------------------------------------------------------


def test_a_fix_gives_one_line_with_what_the_spec_asks_for(runner: Runner) -> None:
    row = runner.run(fixes_it)
    assert runner.lines() == [row]
    assert row["run_id"] == "run1" and row["task_id"] == runner.rig.task.id and row["seed"] == 0
    assert row["config"] == CONFIG
    assert row["resolved"] is True and row["violation"] is False and row["error"] is None
    assert row["localization_file"] is True and row["agent_status"] == "done"
    assert row["patch_size_ratio"] is not None


def test_the_cost_is_computed_from_the_tokens_and_the_seconds_are_measured(runner: Runner) -> None:
    row = runner.run(fixes_it)
    assert row["cost_usd"] == pytest.approx(0.007)
    assert row["seconds"] == 12.5  # the clock is read before the agent and after it


def test_a_bug_that_is_still_there_is_a_line_that_is_not_resolved(runner: Runner) -> None:
    row = runner.run(does_nothing)
    assert row["resolved"] is False and row["error"] is None


def test_lines_are_appended_and_the_folder_is_made(runner: Runner) -> None:
    runner.run(fixes_it, seed=0)
    runner.run(fixes_it, seed=1)
    assert [line["seed"] for line in runner.lines()] == [0, 1]


# --- where the agent works ------------------------------------------------------------------------


def test_the_agent_gets_a_copy_of_repo_without_the_hidden_tests_and_the_copy_is_removed_after(
    runner: Runner,
) -> None:
    seen: dict[str, Any] = {}

    def looks(task: Task, workspace: Path, trajectory: Path) -> AgentRun:
        seen["files"] = sorted(p.name for p in workspace.rglob("*") if p.is_file())
        return AgentRun(status="done", detail="", usage=ONE_TURN)

    runner.run(looks)
    assert seen["files"] == ["mod.py"]
    assert not runner.workspaces[0].exists()


def test_the_trajectory_is_beside_the_results_and_the_line_names_it_relative_to_them(runner: Runner) -> None:
    got: list[Path] = []

    def keeps(task: Task, workspace: Path, trajectory: Path) -> AgentRun:
        got.append(trajectory)
        return AgentRun(status="done", detail="", usage=ONE_TURN)

    row = runner.run(keeps, seed=3)
    assert got[0].parent == runner.results.parent / "trajectories"
    assert row["trajectory"] == f"trajectories/{got[0].name}"
    assert "run1" in got[0].name and runner.rig.task.id in got[0].name and "s3" in got[0].name


# --- what goes wrong ------------------------------------------------------------------------------


def test_a_change_under_hidden_tests_is_a_violation_and_not_resolved(runner: Runner) -> None:
    def cheats(task: Task, workspace: Path, trajectory: Path) -> AgentRun:
        fixes_it(task, workspace, trajectory)
        (workspace / "hidden_tests").mkdir()
        (workspace / "hidden_tests" / "test_fix.py").write_text("def test_x(): pass\n", encoding="utf-8")
        return AgentRun(status="done", detail="", usage=ONE_TURN)

    row = runner.run(cheats)
    assert row["violation"] is True and row["resolved"] is False
    assert row["violations"][0]["kind"] == "forbidden_path"


def test_an_agent_that_crashes_is_a_line_with_an_error_and_not_a_missing_line(runner: Runner) -> None:
    def crashes(task: Task, workspace: Path, trajectory: Path) -> AgentRun:
        raise RuntimeError("the API went away")

    row = runner.run(crashes)
    assert runner.lines() == [row]
    assert row["resolved"] is False and row["agent_status"] == "crashed"
    assert "the API went away" in row["error"] and row["cost_usd"] is None


def test_a_run_that_hit_the_budget_is_still_graded_and_says_so(runner: Runner) -> None:
    def runs_out(task: Task, workspace: Path, trajectory: Path) -> AgentRun:
        fixes_it(task, workspace, trajectory)
        return AgentRun(status="budget", detail="over the limit", usage=ONE_TURN)

    row = runner.run(runs_out)
    assert row["agent_status"] == "budget" and row["resolved"] is True


def test_a_cost_that_cannot_be_known_is_none_and_not_zero(runner: Runner) -> None:
    cached = usage(UsageRecord(turn=1, input_tokens=10, output_tokens=5, cache_read_input_tokens=100))

    def caches(task: Task, workspace: Path, trajectory: Path) -> AgentRun:
        fixes_it(task, workspace, trajectory)
        return AgentRun(status="done", detail="", usage=cached)

    row = runner.run(caches)
    assert row["cost_usd"] is None and row["resolved"] is True
    assert "cache" in row["error"]  # the line says why the cost is missing


def test_without_a_price_the_cost_is_none(runner: Runner) -> None:
    row = runner.run(fixes_it, pricing=None)
    assert row["cost_usd"] is None
