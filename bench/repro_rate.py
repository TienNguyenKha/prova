"""The reproduction rate of a reproduce-first run (M13b), measured offline from the tests it kept.

A reproduction test is worth something only if it fails on the bug for the right reason AND passes once the
bug is fixed. The first half is what the run itself checked; the second half needs the reference patch, which
the agent never sees: a test that expects the wrong thing is red on the bug and stays red with the fix. So for
each run, its kept tests are run in the sandbox on a clean copy of repo/, then on a copy with reference.patch
applied. A reproduce-first run that kept no test did not reproduce, and counts as such.

  uv run python -m bench.repro_rate bench/results/<run>.jsonl
"""

import argparse
import shutil
import sys
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.report import ReportError, load_results
from bench.validate_tasks import apply_patch
from bench.workspace import prepare_workspace
from prova.agent.repro import REPRO_ARGV, REPRO_DIR, Kind, classify_repro
from prova.sandbox.base import Sandbox

OpenSandbox = Callable[[Path], AbstractContextManager[Sandbox]]
TIMEOUT_S = 120.0


class ReproRateError(Exception):
    """The rate cannot be measured: rows of another agent, a missing task, or a patch that does not apply."""


@dataclass(frozen=True, slots=True)
class Reproduction:
    task_id: str
    seed: int
    red_on_bug: Kind | None  # how the kept tests failed on the bug; None if no test was kept
    green_with_fix: bool | None  # None if not tried (no test, or not red for the right reason)
    note: str = ""

    @property
    def reproduces(self) -> bool:
        return self.red_on_bug in ("assertion", "exception_in_code") and self.green_with_fix is True


def measure(results: Path, tasks: Path, open_sandbox: OpenSandbox) -> list[Reproduction]:
    """One Reproduction for each line of `results`, which must come from `bench.run --repro`."""
    try:
        rows = load_results(results)
    except ReportError as e:
        raise ReproRateError(str(e)) from e
    measured: list[Reproduction] = []
    for row in rows:
        if "repro" not in (row.get("config") or {}):
            raise ReproRateError(f"{row.get('task_id')} seed {row.get('seed')}: not a reproduce-first run")
        trajectory = results.parent / str(row["trajectory"])
        kept = trajectory.with_name(f"{trajectory.stem}.{REPRO_DIR}")
        measured.append(_one(row, _task_dir(tasks, str(row["task_id"])), kept, open_sandbox))
    return measured


def _one(row: dict[str, Any], task_dir: Path, kept: Path, open_sandbox: OpenSandbox) -> Reproduction:
    task_id, seed = str(row["task_id"]), int(row["seed"])
    if not kept.is_dir():
        return Reproduction(task_id, seed, None, None, "no test was kept: the run wrote none")
    with tempfile.TemporaryDirectory() as tmp:
        bug, fixed = Path(tmp) / "bug", Path(tmp) / "fixed"
        for workspace in (bug, fixed):
            prepare_workspace(task_dir, workspace)
            shutil.copytree(kept, workspace / REPRO_DIR)
        problem = apply_patch(task_dir / "reference.patch", fixed)
        if problem is not None:
            raise ReproRateError(f"{task_id}: reference.patch does not apply: {problem}")
        with open_sandbox(bug) as sandbox:
            found = sandbox.run(REPRO_ARGV, timeout_s=TIMEOUT_S, cwd="/work")
        if found.timed_out:
            return Reproduction(task_id, seed, "unreadable", None, "the tests did not finish on the bug")
        check = classify_repro(
            found.output, exit_code=found.exit_code, truncated=found.truncated, root="/work"
        )
        if not check.valid:
            return Reproduction(task_id, seed, check.kind, None, check.reason)
        with open_sandbox(fixed) as sandbox:
            after = sandbox.run(REPRO_ARGV, timeout_s=TIMEOUT_S, cwd="/work")
        green = after.exit_code == 0 and not after.timed_out
        note = (
            ""
            if green
            else "did not finish with the reference fix"
            if after.timed_out
            else "red with the reference fix: the test expects something else"
        )
        return Reproduction(task_id, seed, check.kind, green, note)


def _task_dir(tasks: Path, task_id: str) -> Path:
    found = sorted(p for p in tasks.glob(f"*/{task_id}") if p.is_dir())
    if not found:
        raise ReproRateError(f"no task {task_id} under {tasks}")
    return found[0]


def render(measured: list[Reproduction]) -> str:
    lines = ["| task | runs | red on the bug | green with the fix | reproduces |", "|---|---|---|---|---|"]
    for task_id in sorted({m.task_id for m in measured}):
        mine = [m for m in measured if m.task_id == task_id]
        red = sum(m.red_on_bug in ("assertion", "exception_in_code") for m in mine)
        green = sum(m.green_with_fix is True for m in mine)
        done = sum(m.reproduces for m in mine)
        lines.append(
            f"| {task_id} | {len(mine)} | {red}/{len(mine)} | {green}/{len(mine)} | {done}/{len(mine)} |"
        )
    total = sum(m.reproduces for m in measured)
    lines += ["", f"reproduction rate: {total}/{len(measured)}"]
    lines += [f"- {m.task_id} seed {m.seed}: {m.note}" for m in measured if m.note]
    return "\n".join(lines)


def main(argv: list[str]) -> int:  # pragma: no cover - needs Docker; the parts it joins are tested
    from prova.sandbox.docker import DockerSandbox

    parser = argparse.ArgumentParser(description="The reproduction rate of a reproduce-first run")
    parser.add_argument("results", type=Path)
    parser.add_argument("--tasks", type=Path, default=Path(__file__).resolve().parent / "tasks")
    args = parser.parse_args(argv)
    try:
        measured = measure(args.results, args.tasks, lambda workspace: DockerSandbox(workspace))
    except ReproRateError as e:
        print(f"error: {e}")  # noqa: T201
        return 1
    print(render(measured))  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
