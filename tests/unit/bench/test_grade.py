"""Grading a run: did the hidden tests pass on a clean copy of what the agent left, and how did it get there.

The hidden tests run in a folder the agent never saw, with the agent's files copied in as repo/. The report
that says what happened has a name nobody can guess, so code of the agent cannot leave a finished one there.
"""

import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from bench.grade import Grade, grade
from bench.task import Task, load_task
from bench.validate_tasks import REPORT_NAME
from bench.violations import Violation
from bench.workspace import collect_changes, prepare_workspace
from prova.sandbox.base import ExecResult
from tests.unit.bench.test_task import make_task
from tests.unit.bench.test_validate_tasks import F2P, P2P, Case, junit

ORIGINAL = "def f():\n    return 1\n\n\ndef g():\n    return 2\n"
PATCH = "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 10\n"


def all_pass() -> list[Case]:
    return [("test_stacked", "pass"), ("test_single", "pass"), ("test_ordinary", "pass")]


class Pytest:
    """A sandbox that plays pytest on the clean folder: it writes the report where pytest is told to."""

    def __init__(
        self,
        workdir: Path,
        decide: Callable[[str], list[Case]],
        seen: list[dict[str, Any]],
        *,
        report: bool,
        decoy: bool,
    ) -> None:
        self.workdir, self.decide, self.seen, self.report, self.decoy = workdir, decide, seen, report, decoy

    def run(
        self, argv: list[str], *, timeout_s: float, cwd: str = "/work", on_output: object = None
    ) -> ExecResult:
        name = next(a for a in argv if a.startswith("--junitxml=")).removeprefix("--junitxml=/work/")
        agent_code = (self.workdir / "repo" / "mod.py").read_text(encoding="utf-8")
        self.seen.append(
            {
                "name": name,
                "agent_code": agent_code,
                "hidden": (self.workdir / "hidden_tests" / "test_fix.py").read_text(encoding="utf-8"),
                "repo_files": sorted(p.name for p in (self.workdir / "repo").rglob("*") if p.is_file()),
            }
        )
        if self.decoy:  # a finished report under the old name, as agent code could leave one
            (self.workdir / REPORT_NAME).write_text(junit(all_pass()), encoding="utf-8")
        if self.report:
            (self.workdir / name).write_text(junit(self.decide(agent_code)), encoding="utf-8")
        return ExecResult(0, "", "", timed_out=False, duration_s=0.1, truncated=False)

    def close(self) -> None:
        return None


def fixed_if_it_says_ten(code: str) -> list[Case]:
    return all_pass() if "return 10" in code else [("test_stacked", "fail"), *all_pass()[1:]]


class Rig:
    """One task, one workspace of the agent, and a way to grade it."""

    def __init__(self, tmp_path: Path) -> None:
        self.task_dir = make_task(
            tmp_path / "tasks",
            {
                "fail_to_pass": [F2P],
                "pass_to_pass": list(P2P),
                "localization": {"files": ["mod.py"], "symbols": ["f"]},
            },
        )
        (self.task_dir / "repo" / "mod.py").write_text(ORIGINAL, encoding="utf-8")
        (self.task_dir / "hidden_tests" / "test_fix.py").write_text("THE HIDDEN TESTS\n", encoding="utf-8")
        (self.task_dir / "reference.patch").write_text(PATCH, encoding="utf-8")
        self.task: Task = load_task(self.task_dir)
        self.workspace = tmp_path / "agent"
        prepare_workspace(self.task_dir, self.workspace)
        self.seen: list[dict[str, Any]] = []

    def edit(self, text: str, name: str = "mod.py") -> None:
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def grade(
        self,
        decide: Callable[[str], list[Case]] = fixed_if_it_says_ten,
        *,
        violations: tuple[Violation, ...] = (),
        trajectory: Path | None = None,
        report: bool = True,
        decoy: bool = False,
        report_name: str | None = None,
    ) -> Grade:
        @contextmanager
        def open_sandbox(workdir: Path) -> Iterator[Pytest]:
            yield Pytest(workdir, decide, self.seen, report=report, decoy=decoy)

        changes = collect_changes(self.task_dir / "repo", self.workspace)
        return grade(
            self.task, self.task_dir, self.workspace,
            changes=changes, violations=violations, trajectory=trajectory,
            open_sandbox=open_sandbox, report_name=report_name,
        )  # fmt: skip


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


# --- resolved -------------------------------------------------------------------------------------


def test_a_fix_that_makes_every_test_pass_with_no_violation_is_resolved(rig: Rig) -> None:
    rig.edit(ORIGINAL.replace("return 1", "return 10"))
    result = rig.grade()
    assert result.resolved is True and result.violation is False and result.error is None
    assert set(result.outcomes.values()) == {"pass"}


def test_a_bug_that_is_still_there_is_not_resolved_and_the_outcome_says_which_test(rig: Rig) -> None:
    result = rig.grade()  # the workspace is the original code
    assert result.resolved is False and result.violation is False
    assert result.outcomes[F2P] == "fail"


def test_passing_every_test_with_a_violation_is_not_resolved(rig: Rig) -> None:
    rig.edit(ORIGINAL.replace("return 1", "return 10"))
    found = (Violation("disabled_test", "mod.py", "added: @pytest.mark.skip"),)
    result = rig.grade(violations=found)
    assert result.resolved is False and result.violation is True and result.violations == found


def test_a_skipped_test_is_not_a_pass(rig: Rig) -> None:
    def skips(code: str) -> list[Case]:
        return [("test_stacked", "skipped"), *all_pass()[1:]]

    rig.edit(ORIGINAL.replace("return 1", "return 10"))
    result = rig.grade(skips)
    assert result.resolved is False and result.outcomes[F2P] == "skipped"


def test_tests_that_could_not_run_are_an_error_and_not_a_pass(rig: Rig) -> None:
    rig.edit(ORIGINAL.replace("return 1", "return 10"))
    result = rig.grade(report=False)
    assert result.resolved is False and result.outcomes == {}
    assert result.error is not None and "no report" in result.error


# --- the clean copy and the report that nobody can fake -------------------------------------------


def test_the_hidden_tests_run_on_a_copy_that_has_the_agents_code_and_the_tasks_tests(rig: Rig) -> None:
    rig.edit(ORIGINAL.replace("return 1", "return 10"))
    rig.edit("planted\n", "hidden_tests_decoy.py")
    rig.grade()
    seen = rig.seen[0]
    assert "return 10" in seen["agent_code"] and seen["hidden"] == "THE HIDDEN TESTS\n"


def test_the_agents_caches_and_reports_are_not_carried_into_the_clean_copy(rig: Rig) -> None:
    rig.edit("junk", "__pycache__/mod.cpython-312.pyc")
    rig.edit("<fake/>", ".prova-report-0123456789abcdef.xml")
    rig.edit("<fake/>", ".prova-report.xml")
    rig.grade()
    assert rig.seen[0]["repo_files"] == ["mod.py"]


def test_the_report_has_a_name_nobody_can_guess_and_it_changes_every_time(rig: Rig) -> None:
    rig.grade()
    rig.grade()
    first, second = rig.seen[0]["name"], rig.seen[1]["name"]
    assert re.fullmatch(r"\.prova-report-[0-9a-f]{16}\.xml", first) and first != second != REPORT_NAME


def test_a_name_can_be_given_so_that_a_test_can_look_for_the_report(rig: Rig) -> None:
    rig.grade(report_name=".prova-report-aaaaaaaaaaaaaaaa.xml")
    assert rig.seen[0]["name"] == ".prova-report-aaaaaaaaaaaaaaaa.xml"


def test_a_finished_report_left_under_the_old_name_is_not_read(rig: Rig) -> None:
    result = rig.grade(decoy=True)  # the real report says the bug is still there; the decoy says all green
    assert result.resolved is False and result.outcomes[F2P] == "fail"


# --- localization ---------------------------------------------------------------------------------


def test_editing_a_file_of_the_localization_counts_and_editing_another_does_not(rig: Rig) -> None:
    rig.edit("x = 1\n", "other.py")
    assert rig.grade().localization_file is False
    rig.edit(ORIGINAL + "\n# a comment\n")
    assert rig.grade().localization_file is True


def test_a_symbol_is_touched_when_its_definition_changes(rig: Rig) -> None:
    rig.edit(ORIGINAL.replace("return 1", "return 10"))
    assert rig.grade().localization_symbol is True


def test_editing_another_function_of_the_same_file_does_not_touch_the_symbol(rig: Rig) -> None:
    rig.edit(ORIGINAL.replace("return 2", "return 20"))
    result = rig.grade()
    assert result.localization_file is True and result.localization_symbol is False


def test_a_symbol_that_is_deleted_or_added_is_touched(rig: Rig) -> None:
    rig.edit("def g():\n    return 2\n")  # f is gone
    assert rig.grade().localization_symbol is True


def test_a_symbol_defined_by_an_assignment_is_found_too(rig: Rig) -> None:
    (rig.task_dir / "repo" / "mod.py").write_text("LIMIT = 1\n\n\ndef g():\n    return 2\n", encoding="utf-8")
    task = load_task(make_task_with_symbol(rig, "LIMIT"))
    rig.edit("LIMIT = 2\n\n\ndef g():\n    return 2\n")
    assert grade_symbol(rig, task) is True


def test_a_file_that_does_not_parse_touches_no_symbol(rig: Rig) -> None:
    rig.edit("def f(:\n")
    result = rig.grade()
    assert result.localization_file is True and result.localization_symbol is False


# --- patch size, tools ----------------------------------------------------------------------------


def test_the_size_of_the_patch_is_compared_with_the_reference_patch(rig: Rig) -> None:
    rig.edit(ORIGINAL.replace("return 1", "return 10").replace("return 2", "return 20\n    # more"))
    # the agent: 3 lines added, 2 removed = 5; the reference: 1 added, 1 removed = 2
    assert rig.grade().patch_size_ratio == pytest.approx(2.5)


def test_an_agent_that_changed_nothing_has_a_ratio_of_zero(rig: Rig) -> None:
    assert rig.grade().patch_size_ratio == 0.0


def events(tmp_path: Path, *items: dict[str, object]) -> Path:
    path = tmp_path / "t.jsonl"
    path.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
    return path


def test_tool_calls_and_errors_come_from_the_trajectory_and_recovery_is_known_when_there_were_errors(
    rig: Rig, tmp_path: Path
) -> None:
    rig.edit(ORIGINAL.replace("return 1", "return 10"))
    log = events(
        tmp_path,
        {"type": "tool_call", "tool": "read_file"},
        {"type": "tool_result", "tool": "read_file", "success": True},
        {"type": "tool_call", "tool": "edit_file"},
        {"type": "tool_result", "tool": "edit_file", "success": False},
        {"type": "tool_call", "tool": "edit_file"},
        {"type": "tool_result", "tool": "edit_file", "success": True},
    )
    result = rig.grade(trajectory=log)
    assert (result.tool_calls, result.tool_errors, result.recovered) == (3, 1, True)


def test_a_run_with_errors_that_did_not_get_resolved_did_not_recover(rig: Rig, tmp_path: Path) -> None:
    log = events(
        tmp_path, {"type": "tool_call", "tool": "edit_file"}, {"type": "tool_result", "success": False}
    )
    assert rig.grade(trajectory=log).recovered is False


def test_a_run_with_no_tool_error_has_no_recovery_to_speak_of(rig: Rig, tmp_path: Path) -> None:
    log = events(
        tmp_path, {"type": "tool_call", "tool": "read_file"}, {"type": "tool_result", "success": True}
    )
    result = rig.grade(trajectory=log)
    assert (result.tool_calls, result.tool_errors, result.recovered) == (1, 0, None)


@pytest.mark.parametrize("broken", ["none", "missing", "not json"])
def test_a_trajectory_that_cannot_be_read_gives_unknown_counts_and_not_zeros(
    rig: Rig, tmp_path: Path, broken: str
) -> None:
    log: Path | None = None
    if broken == "missing":
        log = tmp_path / "nope.jsonl"
    elif broken == "not json":
        log = tmp_path / "bad.jsonl"
        log.write_text("{oops\n", encoding="utf-8")
    result = rig.grade(trajectory=log)
    assert (result.tool_calls, result.tool_errors, result.recovered) == (None, None, None)


# helpers for the one test that needs another symbol -------------------------------------------------


def make_task_with_symbol(rig: Rig, symbol: str) -> Path:
    import yaml

    spec = rig.task_dir / "task.yaml"
    data = yaml.safe_load(spec.read_text(encoding="utf-8"))
    data["localization"] = {"files": ["mod.py"], "symbols": [symbol]}
    spec.write_text(yaml.safe_dump(data), encoding="utf-8")
    return rig.task_dir


def grade_symbol(rig: Rig, task: Task) -> bool | None:
    rig.task = task
    return rig.grade().localization_symbol
