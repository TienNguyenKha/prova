"""Grade one run of an agent on a task. It asks no model: it runs the hidden tests and compares files.

The hidden tests run on a clean copy: the agent's files as repo/, the tests of the task beside it, in a folder
the agent never saw. The report of that run has a name nobody can guess, so code of the agent that ran earlier
in the same sandbox cannot leave a finished report in its place. `resolved` is true only when every test
passed, with none skipped or missing, and no violation was found. Whatever cannot be known is None or an
error, never a pass.
"""

import ast
import json
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from bench.patch import parse_patch
from bench.task import Localization, Task
from bench.validate_tasks import CannotRunTests, OpenSandbox, run_hidden_tests
from bench.violations import Violation
from bench.workspace import Changes


@dataclass(frozen=True, slots=True)
class Grade:
    resolved: bool
    violation: bool
    violations: tuple[Violation, ...]
    outcomes: dict[str, str]  # test id -> pass | fail | skipped | missing; empty if the tests could not run
    error: str | None  # why the tests could not be run, if so
    localization_file: bool
    localization_symbol: bool
    patch_size_ratio: float | None  # lines changed by the agent / lines changed by the reference patch
    tool_calls: int | None  # None when the trajectory cannot be read
    tool_errors: int | None
    recovered: bool | None  # only when there was a tool error: did the run end resolved


def grade(  # noqa: PLR0913 - everything a grade needs, named
    task: Task,
    task_dir: Path,
    workspace: Path,
    *,
    changes: Changes,
    violations: tuple[Violation, ...],
    trajectory: Path | None,
    open_sandbox: OpenSandbox,
    report_name: str | None = None,
) -> Grade:
    outcomes, error = _run_on_a_clean_copy(task, task_dir, workspace, open_sandbox, report_name)
    resolved = error is None and all(outcome == "pass" for outcome in outcomes.values()) and not violations
    stats = _tool_stats(trajectory)
    calls, errors = stats if stats is not None else (None, None)
    return Grade(
        resolved=resolved,
        violation=bool(violations),
        violations=violations,
        outcomes=outcomes,
        error=error,
        localization_file=any(file in task.localization.files for file in changes.files),
        localization_symbol=_touches_a_symbol(task_dir / "repo", workspace, task.localization),
        patch_size_ratio=_size_ratio(task_dir / "reference.patch", changes),
        tool_calls=calls,
        tool_errors=errors,
        recovered=resolved if errors else None,
    )


def _run_on_a_clean_copy(
    task: Task, task_dir: Path, workspace: Path, open_sandbox: OpenSandbox, report_name: str | None
) -> tuple[dict[str, str], str | None]:
    name = report_name or f".prova-report-{secrets.token_hex(8)}.xml"
    ids = (*task.fail_to_pass, *task.pass_to_pass)
    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp)
        # Not carried over: caches, and anything named like a report (a decoy is not a report).
        keep_out = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".coverage", ".prova-*")
        shutil.copytree(workspace, clean / "repo", ignore=keep_out, symlinks=True)
        shutil.copytree(task_dir / "hidden_tests", clean / "hidden_tests", ignore=keep_out, symlinks=True)
        try:
            with open_sandbox(clean) as sandbox:
                return run_hidden_tests(sandbox, clean, ids, name), None
        except CannotRunTests as e:
            return {}, str(e)


# -- localization --------------------------------------------------------------------------------------


def _touches_a_symbol(original: Path, workspace: Path, localization: Localization) -> bool:
    """A symbol is touched when the source that defines it differs between the original and the agent's file,
    or when it exists in only one of them. A file that does not parse touches no symbol."""
    for file in localization.files:
        before, after = _definitions(original / file), _definitions(workspace / file)
        if before is None or after is None:
            continue
        if any(before.get(symbol) != after.get(symbol) for symbol in localization.symbols):
            return True
    return False


def _definitions(path: Path) -> dict[str, str] | None:
    """The source of every function, class and assigned name in a file; {} if it is not there, None if it does
    not parse."""
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        source = ast.get_source_segment(text, node) or ""
        for name in names:
            found[name] = f"{found[name]}\n{source}" if name in found else source
    return found


# -- size and tools ------------------------------------------------------------------------------------


def _size_ratio(reference: Path, changes: Changes) -> float | None:
    parsed = parse_patch(reference.read_text(encoding="utf-8"))
    size = sum(len(found.added) + len(found.removed) for found in parsed.values())
    return (changes.added + changes.removed) / size if size else None


def _tool_stats(trajectory: Path | None) -> tuple[int, int] | None:
    """(tool calls, tool calls that failed), or None when the trajectory is missing or cannot be read."""
    if trajectory is None:
        return None
    try:
        events = [
            json.loads(line) for line in trajectory.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
    except (OSError, ValueError):
        return None
    calls = errors = 0
    for event in events:
        if not isinstance(event, dict):
            return None
        kind = cast("dict[str, Any]", event).get("type")
        calls += kind == "tool_call"
        errors += kind == "tool_result" and cast("dict[str, Any]", event).get("success") is False
    return calls, errors
