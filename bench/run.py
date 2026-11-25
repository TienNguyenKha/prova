"""Run the benchmark: the agent of the product on every task of a split, one line of result per run.

  uv run python -m bench.run                      # shows the plan and the most it can cost, runs nothing
  uv run python -m bench.run --confirm            # runs it (calls the API, costs money; needs Docker)
  uv run python -m bench.run --task t001_injection_regex --seeds 3 --confirm
  uv run python -m bench.run --loop --confirm      # the fix loop instead of one run (see bench.loop_agent)

Spending money is a decision, so without `--confirm` the command only shows what it would do. The most a run
can cost is the money limit of its task: a limit is checked between turns, so a run can end over it by the
cost of one turn.
"""

import argparse
import os
import secrets
import shutil
import subprocess  # noqa: S404 - only to read the commit of the harness, with a list of arguments
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bench.loop_agent import MAX_ATTEMPTS, LoopAgent
from bench.pricing import PricingError, load_price_table
from bench.product import make_agent
from bench.report import load_results, render
from bench.runner import run_task
from bench.task import TaskError, load_task
from prova.config import Settings
from prova.harness.redact import Redactor
from prova.llm.cassette import RecordingClient
from prova.llm.client import LLMClient, make_client

BENCH = Path(__file__).resolve().parent
TASKS = BENCH / "tasks"
# (task folders, seeds, cassette, the fix loop or one run) -> exit code
Execute = Callable[[list[Path], int, Path | None, bool], int]


class RunError(Exception):
    """The run cannot be planned: a task that does not exist, or nothing to run."""


def select_tasks(root: Path, split: str, only: list[str]) -> list[Path]:
    folders = sorted(p for p in (root / split).glob("*") if p.is_dir())
    if not folders:
        raise RunError(f"there is no task in the split {split!r} ({root / split})")
    if not only:
        return folders
    by_name = {p.name: p for p in folders}
    unknown = [name for name in only if name not in by_name]
    if unknown:
        raise RunError(f"no such task in the split {split!r}: {', '.join(unknown)}")
    return [by_name[name] for name in sorted(set(only))]


def ceiling_usd(folders: list[Path], *, seeds: int) -> float:
    """The most the run can cost if every run of every task spends the whole of its money limit."""
    return sum(load_task(folder).budget.max_usd for folder in folders) * seeds


def new_run_id(now: datetime) -> str:
    return f"{now:%Y-%m-%dT%H-%M}_{secrets.token_hex(3)}"


def _plan(folders: list[Path], seeds: int, ceiling: float, record: Path | None, loop: bool) -> str:
    names = "\n".join(f"  - {folder.name}" for folder in folders)
    lines = [
        f"{len(folders)} task(s) x {seeds} seed(s) = {len(folders) * seeds} run(s):\n{names}",
        f"the most this can cost: ${ceiling:.2f} (the money limit of each task, for each run)",
    ]
    if loop:
        lines.append(f"agent: the fix loop, up to {MAX_ATTEMPTS} attempts that share the budget of the task")
        blind = [f.name for f in folders if not (f / "repo" / "tests").is_dir()]
        if blind:
            why = "no test the agent can run, so it ends as unverifiable before any money is spent"
            lines.append(f"{why}: {', '.join(blind)}")
    else:
        lines.append("agent: one run of the product")
    if record:
        lines.append(f"the model's answers will be recorded in {record}")
    return "\n".join(lines)


def main(argv: list[str], *, root: Path = TASKS, execute: Execute | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the benchmark with the agent of the product")
    parser.add_argument("--split", default="dev")
    parser.add_argument("--task", action="append", default=[], help="a task to run (default: all)")
    parser.add_argument("--seeds", type=_at_least_one, default=1)
    parser.add_argument("--record", type=Path, help="write the model's answers to this new cassette file")
    parser.add_argument("--loop", action="store_true", help="run the fix loop instead of one run")
    parser.add_argument("--confirm", action="store_true", help="really run: calls the API, costs money")
    args = parser.parse_args(argv)
    try:
        folders = select_tasks(root, args.split, args.task)
        ceiling = ceiling_usd(folders, seeds=args.seeds)
    except (RunError, TaskError) as e:
        print(f"error: {e}")  # noqa: T201
        return 1
    if args.record is not None and args.record.exists():
        print(f"error: {args.record} already exists: a cassette is written once, never added to")  # noqa: T201
        return 1
    print(_plan(folders, args.seeds, ceiling, args.record, args.loop))  # noqa: T201
    if not args.confirm:
        print("nothing was run. Add --confirm to run it.")  # noqa: T201
        return 0
    return (execute or _execute)(folders, args.seeds, args.record, args.loop)


def _at_least_one(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


# -- the real run: needs the API and Docker, so it has no unit test; the parts it joins have ----------


def _execute(folders: list[Path], seeds: int, record: Path | None, loop: bool) -> int:
    from prova.app import build_app
    from prova.config import load_settings
    from prova.errors import ContractError, SandboxUnavailableError
    from prova.harness.permissions import load_profiles
    from prova.sandbox.docker import DockerSandbox

    settings = load_settings()
    root = settings.project_root
    try:
        table = load_price_table(BENCH / "pricing.yaml")
        pricing = table.pricing_for(settings.model)
        product = make_agent(
            app=build_app(settings, llm=_llm(settings, record)),
            profiles=load_profiles(root / "permissions.yaml"),
            root=root,
            pricing=pricing,
            open_sandbox=lambda workspace: DockerSandbox(workspace),
            clock=lambda: datetime.now(UTC),
        )
    except (PricingError, ContractError) as e:
        print(f"error: the run does not start: {e}")  # noqa: T201
        return 1
    agent = LoopAgent(product) if loop else product

    run_id = new_run_id(datetime.now(UTC))
    results = BENCH / "results" / f"{run_id}.jsonl"
    config: dict[str, Any] = {
        **agent.config,
        "price_table_as_of": str(table.as_of),
        "price_table_source": table.source,
        "harness_commit": _commit(root),
    }
    try:
        for folder in folders:
            for seed in range(seeds):
                row = run_task(
                    folder, agent=agent, open_sandbox=lambda workdir: DockerSandbox(workdir),
                    pricing=pricing, results=results, run_id=run_id, seed=seed, config=config,
                )  # fmt: skip
                state = "resolved" if row["resolved"] else f"not resolved ({row['agent_status']})"
                print(f"{folder.name} seed {seed}: {state}")  # noqa: T201
    except SandboxUnavailableError as e:
        print(f"cannot run: {e}")  # noqa: T201
        return 1
    finally:
        if results.is_file():
            print(f"\nresults: {results}\n\n{render(load_results(results))}")  # noqa: T201
    return 0


def _llm(settings: Settings, record: Path | None) -> LLMClient:
    """The real client, and with `--record` one that also writes down what the model says."""
    client = make_client(settings)
    if record is None:
        return client
    return RecordingClient(client, record, Redactor.from_environ(os.environ))


def _commit(root: Path) -> str | None:
    """The commit the harness was run from, with `+dirty` if the files differ from it; None if unknown."""
    git = shutil.which("git")
    if git is None:
        return None
    try:
        head = _git(git, root, "rev-parse", "HEAD")
        dirty = _git(git, root, "status", "--porcelain")
    except (OSError, subprocess.SubprocessError):
        return None
    return f"{head}+dirty" if dirty else head


def _git(git: str, root: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 - a list of arguments, no shell
        [git, "-C", str(root), *args], capture_output=True, text=True, timeout=10, check=True
    )
    return done.stdout.strip()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
