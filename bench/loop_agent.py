"""The fix loop as a benchmark agent: the same runner, grading and result line as a single run of the product.

The loop is wrapped around the product's own run (see bench.product), so the only difference between the
two agents is the loop. The budget of the task is the loop's, not each attempt's: an attempt gets what the
attempts before it left, so the loop cannot win by spending more. The whole loop has one sandbox and one
trajectory (the grading reads one trajectory to count tool calls and find forbidden tools). The check is the
visible tests of the task; a task with none ends as `unverifiable` before anything is spent.
"""

import os
import time
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast

from bench.runner import AgentRun
from bench.task import Task
from prova.agent.budget import Budget, Pricing
from prova.agent.fixloop import AttemptRun, Goal, run_fix_loop
from prova.agent.loop import AgentResult
from prova.agent.workspace_check import read_text_files, run_check
from prova.harness.redact import Redactor
from prova.harness.trajectory import TrajectoryWriter
from prova.llm.usage import UsageLog
from prova.sandbox.base import Sandbox

CHECK = ("python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests")
CHECK_TIMEOUT_S = 300.0
MAX_ATTEMPTS = 4  # a guess, as the stuck rule of the loop is: to be measured


class Product(Protocol):
    """What the loop needs from the product (bench.product.ProductAgent has it)."""

    @property
    def pricing(self) -> Pricing: ...
    @property
    def clock(self) -> Callable[[], datetime]: ...
    @property
    def config(self) -> dict[str, Any]: ...
    @property
    def open_sandbox(self) -> Callable[[Path], AbstractContextManager[Sandbox]]: ...

    def attempt(  # noqa: PLR0913
        self,
        prompt: str,
        *,
        sandbox: Sandbox,
        workspace: Path,
        writer: TrajectoryWriter,
        budget: Budget,
        redactor: Redactor,
    ) -> AgentResult: ...


@dataclass(frozen=True, slots=True)
class LoopAgent:
    product: Product
    max_attempts: int = MAX_ATTEMPTS
    seconds: Callable[[], float] = field(default=time.monotonic)

    @property
    def config(self) -> dict[str, Any]:
        return {**self.product.config, "loop": {"max_attempts": self.max_attempts, "check": list(CHECK)}}

    def __call__(self, task: Task, workspace: Path, trajectory: Path) -> AgentRun:
        redactor = Redactor.from_environ(os.environ)
        writer = TrajectoryWriter(
            trajectory, run_id=trajectory.stem, clock=self.product.clock, redactor=redactor
        )
        goal = Goal(
            statement=task.issue,
            termination_check=list(CHECK),
            max_attempts=self.max_attempts,
            max_usd=task.budget.max_usd,
            escalation="stop_and_report",
        )
        used = UsageLog()
        tool_calls = 0
        started = self.seconds()

        with self.product.open_sandbox(workspace) as sandbox:  # closed whatever the loop does

            def attempt(prompt: str) -> AttemptRun:
                nonlocal tool_calls
                left = _left(task, self.product.pricing, used, tool_calls, self.seconds() - started)
                if isinstance(left, str):
                    return AttemptRun("budget", left, UsageLog())
                result = self.product.attempt(
                    prompt,
                    sandbox=sandbox,
                    workspace=workspace,
                    writer=writer,
                    budget=left,
                    redactor=redactor,
                )
                for record in result.usage.records:  # each attempt counts its turns from 1: the loop's go on
                    used.add(replace(record, turn=len(used.records) + 1))
                tool_calls += _tool_calls(result.messages)
                return AttemptRun(result.status, result.detail, result.usage)

            result = run_fix_loop(
                goal,
                attempt=attempt,
                read_files=lambda: read_text_files(workspace),
                verify=lambda: run_check(sandbox, list(CHECK), timeout_s=CHECK_TIMEOUT_S),
                pricing=self.product.pricing,
                escalations_md=trajectory.parent / "escalations.md",
                state_file=trajectory.with_name(f"{trajectory.stem}.state.json"),
                now=self.product.clock,
                redactor=redactor,
            )
        status = "done" if result.status == "done" else f"escalated:{result.reason}"
        return AgentRun(status=status, detail=result.detail, usage=used)


def _left(task: Task, pricing: Pricing, used: UsageLog, tool_calls: int, seconds: float) -> Budget | str:
    """What is left of the task's budget for the next attempt, or why nothing is."""
    calls = task.budget.max_tool_calls - tool_calls
    money = task.budget.max_usd - pricing.cost(used)
    time_left = task.budget.max_seconds - seconds
    if calls <= 0:
        return f"the attempts so far made {tool_calls} tool calls, all the task allows"
    if money <= 0:
        return f"the attempts so far spent all of the task's ${task.budget.max_usd:.2f}"
    if time_left <= 0:
        return f"the attempts so far took all of the task's {task.budget.max_seconds:g} seconds"
    return Budget(max_tool_calls=calls, max_seconds=time_left, max_cost_usd=money, pricing=pricing)


def _tool_calls(messages: Iterable[Any]) -> int:
    """The tool calls the model asked for in a conversation (blocks may be SDK objects or plain dicts)."""
    count = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        fields = cast(dict[str, Any], message)
        content: object = fields.get("content")
        if not isinstance(content, list):  # only the model writes tool_use blocks
            continue
        for block in cast(list[Any], content):
            kind: object = (
                cast(dict[str, Any], block).get("type")
                if isinstance(block, dict)
                else getattr(block, "type", None)
            )
            count += kind == "tool_use"
    return count
