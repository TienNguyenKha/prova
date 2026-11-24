"""Cross-check a task before it enters the benchmark (spec, section 5).

A hidden test is only worth something if it is shown to do its job: red on the original code, green with the
reference patch, and the same result every time. Nothing here asks a model anything: it runs pytest in a
sandbox and reads the junit report that pytest writes, instead of reading its text.

All four steps of the spec: red on the original, green with the patch, the same result every time, and the
hidden tests run every line and branch that the patch adds (measured with coverage, on the patched code).
Mutation testing of the hidden tests is not done: running a line is not the same as checking it.

Run one or more tasks:  uv run python -m bench.validate_tasks [task folder ...]   (needs Docker)
"""

import json
import shutil
import subprocess  # noqa: S404 - only to run `git apply` on the reference patch, with a list of arguments
import sys
import tempfile
import xml.etree.ElementTree as ET  # noqa: S405 - the report is written by pytest in our own sandbox
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.patch import parse_patch
from bench.task import Task, TaskError, load_task
from prova.sandbox.base import Sandbox

REPORT_NAME = ".prova-report.xml"
COVERAGE_NAME = ".prova-coverage.json"
COVERAGE_DATA = "/work/.prova-coverage-data"
RUNS = 3  # how many times each state is run to see that the result does not change
TIMEOUT_S = 120

OpenSandbox = Callable[[Path], AbstractContextManager[Sandbox]]
Outcomes = dict[str, str]  # test id -> pass | fail | skipped | missing


@dataclass(frozen=True, slots=True)
class ValidationReport:
    task_id: str
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


class CannotRunTests(Exception):
    """The tests could not be run or left no report, so what they say cannot be known."""


def validate(folder: Path, open_sandbox: OpenSandbox, *, runs: int = RUNS) -> ValidationReport:
    """Check one task folder. Every doubt is a problem: nothing that could not be shown is taken as fine."""
    try:
        task = load_task(folder)
    except TaskError as e:
        return ValidationReport(folder.name, (str(e),))
    patch = folder / "reference.patch"
    if not patch.is_file():
        return ValidationReport(task.id, ("there is no reference.patch",))

    with tempfile.TemporaryDirectory() as tmp:
        original, patched = Path(tmp) / "original", Path(tmp) / "patched"
        for workdir in (original, patched):
            _copy_task(folder, workdir)
        applied = _apply(patch, patched / "repo")
        if applied is not None:
            return ValidationReport(task.id, (f"reference.patch does not apply: {applied}",))
        try:
            problems = _check(task, open_sandbox, original, patched, runs)
            if not problems:  # measuring what the patch changes means nothing if the patch is not right
                problems = _check_coverage(task, patch, open_sandbox, patched)
        except CannotRunTests as e:
            problems = [str(e)]
    return ValidationReport(task.id, tuple(problems))


def _check(task: Task, open_sandbox: OpenSandbox, original: Path, patched: Path, runs: int) -> list[str]:
    ids = (*task.fail_to_pass, *task.pass_to_pass)
    problems: list[str] = []
    results: dict[str, Outcomes] = {}
    for label, workdir in (("the original code", original), ("the reference patch", patched)):
        with open_sandbox(workdir) as sandbox:
            attempts = [run_hidden_tests(sandbox, workdir, ids) for _ in range(runs)]
        if any(attempt != attempts[0] for attempt in attempts):
            problems.append(f"the result is not stable on {label}: {_differences(attempts)}")
        results[label] = attempts[0]

    before, after = results["the original code"], results["the reference patch"]
    for test in task.fail_to_pass:
        problems += _expect(test, before[test], "fail", "on the original code", True)
    for test in task.pass_to_pass:
        problems += _expect(test, before[test], "pass", "on the original code")
    for test in ids:
        problems += _expect(test, after[test], "pass", "with the reference patch")
    return problems


def _expect(test: str, outcome: str, wanted: str, where: str, catches_the_bug: bool = False) -> list[str]:
    """No problem when `outcome` is what the task needs. A test that should fail but passes catches no bug."""
    if outcome == wanted:
        return []
    if outcome == "missing":
        return [f"{test}: the test did not run {where}: no such test, or pytest stopped before it"]
    if catches_the_bug and outcome == "pass":
        return [f"{test}: does not fail on the original code: it does not catch the bug"]
    verb = "does not pass" if wanted == "pass" else "does not fail"
    return [f"{test}: {verb} {where} ({outcome})"]


def _differences(attempts: list[Outcomes]) -> str:
    tests = {t for attempt in attempts for t in attempt}
    names = sorted(t for t in tests if len({attempt[t] for attempt in attempts}) > 1)
    return ", ".join(names)


# -- step 3: do the hidden tests run what the patch adds ---------------------------------------------


def changed_lines(patch: str) -> dict[str, set[int]]:
    """The lines a unified diff adds, as numbers in the patched file, for each file that gets any."""
    return {
        path: {number for number, _ in found.added}
        for path, found in parse_patch(patch).items()
        if found.added
    }


def _check_coverage(task: Task, patch: Path, open_sandbox: OpenSandbox, patched: Path) -> list[str]:
    changed = {
        path: numbers
        for path, numbers in changed_lines(patch.read_text(encoding="utf-8")).items()
        if path.endswith(".py")
    }
    if not changed:
        return []
    ids = (*task.fail_to_pass, *task.pass_to_pass)
    with open_sandbox(patched) as sandbox:
        files = _measure(sandbox, patched, ids)
    problems: list[str] = []
    for path, numbers in sorted(changed.items()):
        data = files.get(f"repo/{path}")
        if data is None:
            problems.append(f"{path}: the reference patch changes it and the hidden tests never imported it")
            continue
        missing = sorted(numbers & set(data.get("missing_lines", [])))
        if missing:
            where = f"line {missing[0]}" if len(missing) == 1 else "lines " + ", ".join(map(str, missing))
            verb = "never runs" if len(missing) == 1 else "never run"
            problems.append(
                f"{where} of {path} {verb} under the hidden tests, although the reference patch adds it"
            )
        for origin, target in data.get("missing_branches", []):
            if origin in numbers:
                problems.append(
                    f"the branch from line {origin} to {target} of {path} is never taken by the hidden tests"
                )
    return problems


def _measure(sandbox: Sandbox, workdir: Path, ids: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Run the hidden tests under coverage and return its report: for each file, what ran and what did not."""
    report = workdir / COVERAGE_NAME
    report.unlink(missing_ok=True)
    tests = ["-m", "pytest", "-q", "-p", "no:cacheprovider", *ids]
    run = [
        "python",
        "-m",
        "coverage",
        "run",
        "--branch",
        "--source=/work/repo",
        f"--data-file={COVERAGE_DATA}",
    ]
    sandbox.run([*run, *tests], timeout_s=TIMEOUT_S)  # whether the tests pass was decided by the steps before
    export = [
        "python",
        "-m",
        "coverage",
        "json",
        f"--data-file={COVERAGE_DATA}",
        "-o",
        f"/work/{COVERAGE_NAME}",
    ]
    done = sandbox.run(export, timeout_s=TIMEOUT_S)
    if done.timed_out or not report.is_file():
        tail = " | ".join(done.output.strip().splitlines()[-3:])
        raise CannotRunTests(f"coverage left no coverage report (exit code {done.exit_code}): {tail}")
    return json.loads(report.read_text(encoding="utf-8")).get("files", {})


# -- running ------------------------------------------------------------------------------------------


def _copy_task(folder: Path, workdir: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache")
    for part in ("repo", "hidden_tests"):
        shutil.copytree(folder / part, workdir / part, ignore=ignore)


def _apply(patch: Path, repo: Path) -> str | None:
    """Apply the patch to the copy of repo/. Returns None, or what git said when it did not work."""
    git = shutil.which("git")
    if git is None:
        return "git is not installed"
    command = [git, "-C", str(repo), "apply", str(patch.resolve())]
    done = subprocess.run(  # noqa: S603 - a list of arguments, no shell; the patch is a file of the task
        command, capture_output=True, text=True, timeout=30, check=False
    )
    return None if done.returncode == 0 else (done.stderr.strip() or "git apply failed")


def run_hidden_tests(
    sandbox: Sandbox, workdir: Path, ids: tuple[str, ...], report_name: str = REPORT_NAME
) -> Outcomes:
    """Run the tests `ids` in `workdir` (it has repo/ and hidden_tests/) and say what became of each: pass,
    fail, skipped or missing. It is read from the junit report that pytest writes under `report_name`."""
    report = workdir / report_name
    report.unlink(missing_ok=True)
    junit = f"--junitxml=/work/{report_name}"
    argv = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", junit, *ids]
    result = sandbox.run(argv, timeout_s=TIMEOUT_S)
    if result.timed_out:
        raise CannotRunTests(f"pytest did not finish in {TIMEOUT_S} seconds")
    if not report.is_file():
        tail = " | ".join(result.output.strip().splitlines()[-3:])
        raise CannotRunTests(f"pytest left no report (exit code {result.exit_code}): {tail}")
    cases = _read_cases(report)
    return {test: _outcome(test, cases) for test in ids}


def _read_cases(report: Path) -> list[tuple[str, str, str]]:
    """(classname, name, status) of every test case in a junit report."""
    cases: list[tuple[str, str, str]] = []
    for case in ET.parse(report).getroot().iter("testcase"):  # noqa: S314
        tags = {child.tag for child in case}
        status = "fail" if tags & {"failure", "error"} else "skipped" if "skipped" in tags else "pass"
        cases.append((case.get("classname", ""), case.get("name", ""), status))
    return cases


def _outcome(test: str, cases: list[tuple[str, str, str]]) -> str:
    """What happened to one test id. With parameters one id has many cases, and all of them must pass."""
    path, _, rest = test.partition("::")
    module = path.removesuffix(".py").replace("/", ".")
    *classes, name = rest.split("::")
    wanted_class = ".".join([module, *classes])
    found = [
        status
        for classname, case_name, status in cases
        if classname == wanted_class and (case_name == name or case_name.startswith(f"{name}["))
    ]
    if not found:
        return "missing"
    if "fail" in found:
        return "fail"
    return "skipped" if "skipped" in found else "pass"


# -- the command line ---------------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    from prova.sandbox.docker import DockerSandbox  # only the command line needs Docker

    root = Path(__file__).resolve().parent / "tasks"
    folders = [Path(arg) for arg in argv] or sorted(p for p in root.glob("*/*") if p.is_dir())
    failed = 0
    for folder in folders:
        report = validate(folder, lambda workdir: DockerSandbox(workdir))
        print(f"{'ok  ' if report.ok else 'FAIL'} {report.task_id}")  # noqa: T201 - this is the output
        for problem in report.problems:
            print(f"       - {problem}")  # noqa: T201
        failed += not report.ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
