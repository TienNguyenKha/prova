"""Reproduce first, then fix (M13b), as a benchmark agent on the same runner and grading as the others.

A run of the product in the `repro` role writes tests in repro/ that show the bug. The harness then checks, by
itself, that they fail for the right reason (see prova.agent.repro): the stop hook of that run checks it too,
but gives up after two tries. Without a valid reproduction nothing is fixed, and the run ends as
`cannot_reproduce`. With one, the fix loop of M13 runs with those tests (and the task's visible tests, if any)
as its check, so the goal is a test that was shown to fail on the bug: the "grill -> goal" of M13.

Both phases share one sandbox, one trajectory and the task's budget. At the end the reproduction tests are
taken out of the workspace and kept next to the trajectory, with the result of their check: the grade sees
only the fix, and the reproduction rate can be measured later from what was kept.
"""

import json
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bench.loop_agent import MAX_ATTEMPTS, Product, Session, fix_loop, open_session, status_of
from bench.runner import AgentRun
from bench.task import Task
from prova.agent.repro import REPRO_ARGV, REPRO_DIR, ReproCheck, classify_repro
from prova.prompts.render import render_user_context

REPRO_TASK = (
    f"Write a test in {REPRO_DIR}/ (for example {REPRO_DIR}/test_bug.py) that fails on the code as it is "
    "because of the bug above, and that will pass once the bug is fixed. Do not change the code. "
    "Run your test and see it fail before you end."
)
REPRO_TIMEOUT_S = 120.0


def fix_check(workspace: Path) -> list[str]:
    """The check of the fix loop: the reproduction tests, and the visible tests of the task if it has some."""
    visible = ["tests"] if (workspace / "tests").is_dir() else []
    return ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--", REPRO_DIR, *visible]


@dataclass(frozen=True, slots=True)
class ReproAgent:
    fixer: Product  # the product in the `fix` role
    reproducer: Product  # the product in the `repro` role
    max_attempts: int = MAX_ATTEMPTS
    seconds: Callable[[], float] = field(default=time.monotonic)

    @property
    def config(self) -> dict[str, Any]:
        repro = {
            "profile": self.reproducer.config["profile"],
            "prompt_hash": self.reproducer.config["prompt_hash"],
        }
        loop = {"max_attempts": self.max_attempts, "check": f"{REPRO_DIR} (and tests, if the task has them)"}
        return {**self.fixer.config, "repro": repro, "loop": loop}

    def __call__(self, task: Task, workspace: Path, trajectory: Path) -> AgentRun:
        with open_session(self.fixer, task, workspace, trajectory, self.seconds) as session:
            try:
                status, detail = self._phases(task, session, trajectory)
            finally:
                _keep_out_of_the_workspace(workspace, trajectory)
        return AgentRun(status=status, detail=detail, usage=session.spend.used)

    def _phases(self, task: Task, session: Session, trajectory: Path) -> tuple[str, str]:
        written = session.attempt(self.reproducer, f"{render_user_context(task.issue)}\n\n{REPRO_TASK}")
        found = session.sandbox.run(REPRO_ARGV, timeout_s=REPRO_TIMEOUT_S, cwd="/work")
        if found.timed_out:
            check = ReproCheck(valid=False, kind="unreadable", reason="the reproduction tests did not finish")
        else:
            check = classify_repro(
                found.output, exit_code=found.exit_code, truncated=found.truncated, root="/work"
            )
        _save(trajectory, check)
        if not check.valid:
            if written.status == "budget":
                return "escalated:budget", f"the reproduction run ran out of budget: {written.detail}"
            return "escalated:cannot_reproduce", f"{check.kind}: {check.reason}"
        result = fix_loop(
            self.fixer,
            session,
            task=task,
            trajectory=trajectory,
            check=fix_check(session.workspace),
            max_attempts=self.max_attempts,
        )
        return status_of(result), result.detail


def _save(trajectory: Path, check: ReproCheck) -> None:
    path = trajectory.with_name(f"{trajectory.stem}.repro.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(check), indent=2, ensure_ascii=False), encoding="utf-8")


def _keep_out_of_the_workspace(workspace: Path, trajectory: Path) -> None:
    """Move repro/ next to the trajectory: the grade must see only the fix."""
    source = workspace / REPRO_DIR
    if not source.exists():
        return
    target = trajectory.with_name(f"{trajectory.stem}.{REPRO_DIR}")
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
