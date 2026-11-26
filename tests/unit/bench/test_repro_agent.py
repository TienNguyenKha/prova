"""Reproduce first, then fix (M13b), as a benchmark agent on the same runner and grading as the others.

A reproduction run writes tests in repro/; the harness checks they fail for the right reason; only then does
the fix loop of M13 run, with those tests as its check. Fakes stand in for both roles and for the sandbox, so
nothing here costs money. What is checked is the wiring: one sandbox, one trajectory and one budget for both
phases, no fix without a valid reproduction, and the tests taken out of the workspace at the end (kept next to
the trajectory), so the grade sees only the fix.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bench.repro_agent import REPRO_TASK, ReproAgent, fix_check
from bench.task import Task, load_task
from prova.agent.budget import Budget, Pricing
from prova.agent.loop import AgentResult
from prova.agent.repro import REPRO_ARGV
from prova.harness.redact import Redactor
from prova.harness.trajectory import TrajectoryWriter
from prova.llm.usage import UsageLog, UsageRecord
from prova.sandbox.base import ExecResult, Sandbox
from tests.fakes import FakeSandbox
from tests.unit.agent.test_fingerprint import EQ, output
from tests.unit.agent.test_repro import ASSERTION, IN_THE_TEST, SUMMARY_LINES, run
from tests.unit.bench.test_task import make_task

PRICING = Pricing(input_per_mtok=2.0, output_per_mtok=10.0)


def result(exit_code: int, text: str = "") -> ExecResult:
    return ExecResult(exit_code, text, "", timed_out=False, duration_s=0.1, truncated=False)


REPRODUCED = result(1, run(ASSERTION, summary=[SUMMARY_LINES["assertion"]]))
NOT_REPRODUCED = result(1, run(IN_THE_TEST, summary=[SUMMARY_LINES["typo"]]))
RED, GREEN = result(1, output(EQ)), result(0, "2 passed")


def usage() -> UsageLog:  # $0.03 at PRICING
    log = UsageLog()
    log.add(UsageRecord(turn=1, input_tokens=10_000, output_tokens=1_000))
    return log


class Role:
    """Stands in for the product in one role: each run writes a file and reports what it used."""

    def __init__(self, writes: str, prompt_hash: str, sandbox: FakeSandbox) -> None:
        self.writes = writes
        self.pricing = PRICING
        self.config: dict[str, Any] = {
            "model": "claude-test",
            "profile": writes.split("/")[0],
            "prompt_hash": prompt_hash,
        }
        self.sandbox = sandbox
        self.opened: list[Path] = []
        self.seen: list[dict[str, Any]] = []

    def clock(self) -> datetime:
        return datetime(2026, 12, 1, tzinfo=UTC)

    @contextmanager
    def open_sandbox(self, workspace: Path) -> Iterator[Sandbox]:
        self.opened.append(workspace)
        yield self.sandbox

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
        self.seen.append({"prompt": prompt, "sandbox": sandbox, "writer": writer, "budget": budget})
        target = workspace / self.writes
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# run {len(self.seen)}\n", encoding="utf-8")
        return AgentResult("done", "ok", 1, usage(), ())


class Rig:
    def __init__(
        self, tmp_path: Path, *checks: ExecResult, visible_tests: bool = False, **budget: Any
    ) -> None:
        limits = {"max_tool_calls": 40, "max_usd": 0.5, "max_seconds": 600, **budget}
        self.task: Task = load_task(make_task(tmp_path / "tasks", {"budget": limits}))
        self.sandbox = FakeSandbox(*checks)
        self.reproducer = Role("repro/test_bug.py", "r" * 64, self.sandbox)
        self.fixer = Role("fix.py", "f" * 64, self.sandbox)
        self.workspace = tmp_path / "agent"
        self.workspace.mkdir()
        if visible_tests:
            (self.workspace / "tests").mkdir()
        self.trajectory = tmp_path / "out" / "trajectories" / "run1-t001-s0.jsonl"

    def agent(self, max_attempts: int = 3) -> ReproAgent:
        return ReproAgent(self.fixer, self.reproducer, max_attempts=max_attempts, seconds=lambda: 0.0)  # type: ignore[arg-type]

    def run(self, max_attempts: int = 3) -> Any:
        return self.agent(max_attempts)(self.task, self.workspace, self.trajectory)

    def argv(self) -> list[list[str]]:
        return [c["argv"] for c in self.sandbox.calls]

    def kept(self, name: str) -> Path:
        return self.trajectory.parent / f"{self.trajectory.stem}{name}"


# --- reproduce first ------------------------------------------------------------------------------------


def test_the_bug_is_reproduced_then_fixed_with_the_reproduction_as_the_check(tmp_path: Path) -> None:
    r = Rig(tmp_path, REPRODUCED, RED, GREEN)
    agent_run = r.run()
    assert agent_run.status == "done", agent_run.detail
    check = fix_check(r.workspace)
    assert r.argv() == [REPRO_ARGV, check, check]  # reproduced, red before the fix, green after
    assert "repro" in check and "tests" not in check


def test_the_visible_tests_of_the_task_are_part_of_the_check_when_there_are_some(tmp_path: Path) -> None:
    r = Rig(tmp_path, visible_tests=True)
    assert fix_check(r.workspace)[-2:] == ["repro", "tests"]


def test_the_reproduction_run_is_told_the_issue_as_data_and_what_to_write(tmp_path: Path) -> None:
    r = Rig(tmp_path, REPRODUCED, RED, GREEN)
    r.run()
    prompt = r.reproducer.seen[0]["prompt"]
    assert prompt.startswith("<untrusted_issue>") and r.task.issue in prompt and REPRO_TASK in prompt


def test_without_a_valid_reproduction_nothing_is_fixed(tmp_path: Path) -> None:
    r = Rig(tmp_path, NOT_REPRODUCED)
    agent_run = r.run()
    assert agent_run.status == "escalated:cannot_reproduce"
    assert "exception_in_test" in agent_run.detail and "NameError" in agent_run.detail
    assert r.fixer.seen == [] and r.argv() == [REPRO_ARGV]


def test_a_reproduction_that_spends_the_whole_budget_leaves_nothing_to_fix_with(tmp_path: Path) -> None:
    r = Rig(tmp_path, REPRODUCED, max_usd=0.03)  # the reproduction spends all of it
    agent_run = r.run()
    assert len(r.reproducer.seen) == 1 and r.fixer.seen == []
    assert agent_run.status == "escalated:budget"


# --- one session for both phases ------------------------------------------------------------------------


def test_both_phases_share_one_sandbox_one_trajectory_and_one_budget(tmp_path: Path) -> None:
    r = Rig(tmp_path, REPRODUCED, RED, GREEN, max_usd=0.5)
    agent_run = r.run()
    assert r.fixer.opened == [r.workspace] and r.reproducer.opened == []
    assert r.reproducer.seen[0]["writer"] is r.fixer.seen[0]["writer"]
    assert r.fixer.seen[0]["budget"].max_cost_usd == 0.5 - 0.03  # what the reproduction left
    assert [rec.turn for rec in agent_run.usage.records] == [1, 2]


def test_the_config_names_both_roles_and_the_loop(tmp_path: Path) -> None:
    config = Rig(tmp_path).agent(max_attempts=5).config
    assert config["prompt_hash"] == "f" * 64 and config["repro"] == {
        "profile": "repro",
        "prompt_hash": "r" * 64,
    }
    assert config["loop"]["max_attempts"] == 5


# --- what the grade sees, and what is kept --------------------------------------------------------------


def test_the_reproduction_tests_are_taken_out_of_the_workspace_and_kept(tmp_path: Path) -> None:
    r = Rig(tmp_path, REPRODUCED, RED, GREEN)
    r.run()
    assert not (r.workspace / "repro").exists() and (r.workspace / "fix.py").is_file()
    assert (r.kept(".repro") / "test_bug.py").is_file()


def test_the_check_of_the_reproduction_is_kept_whether_it_was_valid_or_not(tmp_path: Path) -> None:
    for name, check, valid in (("ok", REPRODUCED, True), ("no", NOT_REPRODUCED, False)):
        r = Rig(tmp_path / name, check, RED, GREEN)
        r.run()
        kept = json.loads(r.kept(".repro.json").read_text(encoding="utf-8"))
        assert kept["valid"] is valid and kept["kind"] in {"assertion", "exception_in_test"}
        assert not (r.workspace / "repro").exists()


def test_the_tests_are_taken_out_even_when_the_fix_crashes(tmp_path: Path) -> None:
    r = Rig(tmp_path, REPRODUCED, RED)

    def crash(*args: Any, **kwargs: Any) -> AgentResult:
        raise RuntimeError("the API went away")

    r.fixer.attempt = crash  # type: ignore[method-assign]
    agent_run = r.run()
    assert agent_run.status == "escalated:error"
    assert not (r.workspace / "repro").exists() and r.kept(".repro").is_dir()


def test_a_check_of_the_reproduction_that_was_cut_or_ran_out_of_time_is_not_a_reproduction(
    tmp_path: Path,
) -> None:
    cut = ExecResult(1, REPRODUCED.stdout, "", timed_out=False, duration_s=0.1, truncated=True)
    late = ExecResult(-1, "", "", timed_out=True, duration_s=120, truncated=False)
    for name, found, kind in (("cut", cut, "unreadable"), ("late", late, "unreadable")):
        r = Rig(tmp_path / name, found, RED, GREEN)
        agent_run = r.run()
        assert agent_run.status == "escalated:cannot_reproduce" and r.fixer.seen == []
        assert json.loads(r.kept(".repro.json").read_text(encoding="utf-8"))["kind"] == kind
        if name == "late":
            assert "did not finish" in agent_run.detail
    assert r.sandbox.calls[0]["cwd"] == "/work" and r.sandbox.calls[0]["timeout_s"] == 120.0


def test_a_reproduction_run_that_ran_out_of_budget_says_so(tmp_path: Path) -> None:
    r = Rig(tmp_path, NOT_REPRODUCED)
    r.reproducer.attempt = lambda *a, **k: AgentResult(
        "budget", "", 1, usage(), (), detail="the run cost $0.51"
    )  # type: ignore[method-assign]
    agent_run = r.run()
    assert agent_run.status == "escalated:budget" and "$0.51" in agent_run.detail


def test_tests_kept_by_an_earlier_run_of_the_same_trajectory_are_replaced_not_nested(tmp_path: Path) -> None:
    r = Rig(tmp_path, REPRODUCED, RED, GREEN)
    (r.kept(".repro")).mkdir(parents=True)
    (r.kept(".repro") / "old_test.py").write_text("# old\n", encoding="utf-8")
    r.run()
    assert sorted(p.name for p in r.kept(".repro").iterdir()) == ["test_bug.py"]
