"""The patches a reviewer is measured on (M14): good and bad, with the hidden tests of each task as the truth.

A `good` patch passes every hidden test of its task; a `bad` one fails at least one. What we meant a patch to
be does not count: a "bad" patch that passes every hidden test is not bad, and the case is refused. A hidden
test that did not run (missing, skipped) is neither a pass nor a failure that counts.

  uv run python -m bench.review_cases        # checks every case in the sandbox (no model, no money)
"""

import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from bench.task import load_task
from bench.validate_tasks import CannotRunTests, OpenSandbox, Outcomes, apply_patch, run_hidden_tests

REVIEW = Path(__file__).resolve().parent / "review"
CASES = REVIEW / "cases.yaml"
TASKS = Path(__file__).resolve().parent / "tasks"
Label = Literal["good", "bad"]


class ReviewCaseError(Exception):
    """The cases file is wrong: a missing task or patch, an unknown field or label, an id used twice."""


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    task: str
    patch: str
    label: Label
    kind: str
    note: str = ""


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    cases: list[_Entry]


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    task: str
    patch_file: Path
    label: Label
    kind: str
    note: str


def load_cases(path: Path, tasks: Path) -> list[Case]:
    """The cases of `path`, each with the patch file it names; every problem is a ReviewCaseError."""
    try:
        parsed = _File.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as e:
        raise ReviewCaseError(f"{path}: {e}") from e
    cases: list[Case] = []
    seen: set[str] = set()
    for entry in parsed.cases:
        if entry.id in seen:
            raise ReviewCaseError(f"the id {entry.id!r} is used twice")
        seen.add(entry.id)
        folder = task_dir(tasks, entry.task)
        patch = (
            folder / "reference.patch"
            if entry.patch == "reference"
            else path.parent / "patches" / entry.patch
        )
        if not patch.is_file():
            raise ReviewCaseError(f"{entry.id}: no patch {patch}")
        cases.append(Case(entry.id, entry.task, patch, entry.label, entry.kind, entry.note))
    return cases


def task_dir(tasks: Path, task_id: str) -> Path:
    found = sorted(p for p in tasks.glob(f"*/{task_id}") if p.is_dir())
    if not found:
        raise ReviewCaseError(f"no task {task_id} under {tasks}")
    return found[0]


def judge(case: Case, outcomes: Outcomes) -> str | None:
    """None if the hidden tests agree with the label of the case, else what is wrong."""
    not_run = sorted(test for test, outcome in outcomes.items() if outcome not in ("pass", "fail"))
    if not_run:
        return f"{case.id}: hidden tests did not run: {', '.join(not_run)}"
    failed = sorted(test for test, outcome in outcomes.items() if outcome == "fail")
    if case.label == "good" and failed:
        return f"{case.id}: a good patch fails hidden tests: {', '.join(failed)}"
    if case.label == "bad" and not failed:
        return f"{case.id}: it passes every hidden test, so it is not bad"
    return None


def check_case(case: Case, tasks: Path, open_sandbox: OpenSandbox) -> str | None:
    """Apply the patch to a clean copy of the task, run its hidden tests in the sandbox and judge."""
    folder = task_dir(tasks, case.task)
    task = load_task(folder)
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for part in ("repo", "hidden_tests"):
            _copy(folder / part, workdir / part)
        problem = apply_patch(case.patch_file, workdir / "repo")
        if problem is not None:
            return f"{case.id}: the patch does not apply: {problem}"
        try:
            with open_sandbox(workdir) as sandbox:
                outcomes = run_hidden_tests(sandbox, workdir, (*task.fail_to_pass, *task.pass_to_pass))
        except CannotRunTests as e:
            return f"{case.id}: {e}"
    return judge(case, outcomes)


def _copy(source: Path, target: Path) -> None:
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))


def main() -> int:  # needs Docker: the parts it joins are tested
    from prova.sandbox.docker import DockerSandbox  # noqa: PLC0415

    problems = 0
    for case in load_cases(CASES, TASKS):
        problem = check_case(case, TASKS, lambda workdir: DockerSandbox(workdir))
        problems += problem is not None
        line = f"{'ok  ' if problem is None else 'FAIL'} {case.id}"
        print(line + (f"\n       - {problem}" if problem else ""))  # noqa: T201
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
