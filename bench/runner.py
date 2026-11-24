"""Run one benchmark task: a workspace for the agent, the agent, the grade, and one line of result.

The agent is a function handed in, so this file knows nothing about models. What it does is what has to be
the same for every agent: the agent works in a copy of repo/ that has no hidden tests, the run is timed and
priced, the copy is graded, and one line is appended to the results file whatever happened. A crash, or a
cost that cannot be known, is a line that says so: a missing line or an invented number would be read as a
result.
"""

import json
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.grade import grade
from bench.task import Task, load_task
from bench.validate_tasks import OpenSandbox
from bench.violations import find_violations
from bench.workspace import collect_changes, prepare_workspace
from prova.agent.budget import Pricing
from prova.llm.usage import UsageLog


@dataclass(frozen=True, slots=True)
class AgentRun:
    """What the agent says about its own run. Whether it fixed anything is not here: the grade decides."""

    status: str  # done | budget | ... as the loop reports it
    detail: str
    usage: UsageLog


Agent = Callable[[Task, Path, Path], AgentRun]  # (task, workspace, where to write the trajectory)


def run_task(  # noqa: PLR0913 - everything a run needs, named
    task_dir: Path,
    *,
    agent: Agent,
    open_sandbox: OpenSandbox,
    pricing: Pricing | None,
    results: Path,
    run_id: str,
    seed: int,
    config: Mapping[str, Any],
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Run `agent` on the task in `task_dir`, grade it, append one line to `results` and return it."""
    task = load_task(task_dir)
    trajectory = results.parent / "trajectories" / f"{run_id}-{task.id}-s{seed}.jsonl"
    head = {"run_id": run_id, "task_id": task.id, "seed": seed, "config": dict(config)}
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "agent"
        prepare_workspace(task_dir, workspace)
        started = clock()
        try:
            run = agent(task, workspace, trajectory)
        except Exception as e:  # noqa: BLE001 - whatever the agent did, the run still gets its line
            row = {**head, **_crashed(e, clock() - started)}
        else:
            seconds = clock() - started
            row = {
                **head,
                **_graded(task, task_dir, workspace, run, seconds, trajectory, pricing, open_sandbox),
            }
    row["trajectory"] = f"trajectories/{trajectory.name}"
    results.parent.mkdir(parents=True, exist_ok=True)
    with results.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _crashed(error: Exception, seconds: float) -> dict[str, Any]:
    return {
        "resolved": False,
        "violation": False,
        "violations": [],
        "outcomes": {},
        "error": f"the agent crashed: {type(error).__name__}: {error}",
        "agent_status": "crashed",
        "localization_file": None,
        "localization_symbol": None,
        "patch_size_ratio": None,
        "tool_calls": None,
        "tool_errors": None,
        "recovered": None,
        "cost_usd": None,
        "seconds": seconds,
    }


def _graded(  # noqa: PLR0913
    task: Task,
    task_dir: Path,
    workspace: Path,
    run: AgentRun,
    seconds: float,
    trajectory: Path,
    pricing: Pricing | None,
    open_sandbox: OpenSandbox,
) -> dict[str, Any]:
    log = trajectory if trajectory.is_file() else None
    changes = collect_changes(task_dir / "repo", workspace)
    violations = find_violations(task, changes, log)
    result = grade(
        task, task_dir, workspace,
        changes=changes, violations=violations, trajectory=log, open_sandbox=open_sandbox,
    )  # fmt: skip
    cost, cost_error = _cost(run.usage, pricing)
    errors = [text for text in (result.error, cost_error) if text]
    return {
        "resolved": result.resolved,
        "violation": result.violation,
        "violations": [{"kind": v.kind, "where": v.where, "detail": v.detail} for v in result.violations],
        "outcomes": result.outcomes,
        "error": "; ".join(errors) or None,
        "agent_status": run.status,
        "localization_file": result.localization_file,
        "localization_symbol": result.localization_symbol,
        "patch_size_ratio": result.patch_size_ratio,
        "tool_calls": result.tool_calls,
        "tool_errors": result.tool_errors,
        "recovered": result.recovered,
        "cost_usd": cost,
        "seconds": seconds,
    }


def _cost(usage: UsageLog, pricing: Pricing | None) -> tuple[float | None, str | None]:
    """The dollars spent, or None and why not: a cost that cannot be known is never reported as 0."""
    if pricing is None:
        return None, "no price was given, so the cost is not known"
    try:
        return pricing.cost(usage), None
    except ValueError as e:
        return None, f"the cost is not known: {e}"
