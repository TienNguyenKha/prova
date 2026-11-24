"""Tests for bench.validate_tasks: a task enters the benchmark only if its hidden tests prove what it claims.

The sandbox is a fake that plays pytest: it looks at the repo/ in the workspace (still buggy, or patched) and
writes the junit report a real pytest would. So the tests are about the decisions of the validator.
"""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from xml.sax.saxutils import quoteattr

import pytest

from bench.validate_tasks import COVERAGE_NAME, REPORT_NAME, changed_lines, validate
from prova.sandbox.base import ExecResult
from tests.unit.bench.test_task import make_task

F2P = "hidden_tests/test_fix.py::test_stacked"
P2P = ("hidden_tests/test_fix.py::test_single", "hidden_tests/test_fix.py::test_ordinary")
PATCH = '--- a/mod.py\n+++ b/mod.py\n@@ -1 +1 @@\n-VALUE = "BUGGY"\n+VALUE = "FIXED"\n'

Case = tuple[str, str]  # (test name, status): pass | fail | error | skipped
Decide = Callable[[str, int], list[Case]]  # (the text of repo/mod.py, number of this run) -> cases
Calls = list[tuple[list[str], str]]  # what the fake was asked to run: (argv, cwd)
Coverage = dict[str, dict[str, list[object]]]  # file -> executed_lines, missing_lines, missing_branches

GOOD_COVERAGE: Coverage = {
    "repo/mod.py": {"executed_lines": [1], "missing_lines": [], "missing_branches": []}
}


def good(state: str, run: int) -> list[Case]:
    stacked = "pass" if state == "FIXED" else "fail"
    return [("test_stacked", stacked), ("test_single", "pass"), ("test_ordinary", "pass")]


def junit(cases: list[Case]) -> str:
    inner = ""
    for name, status in cases:
        body = {"pass": "", "fail": "<failure message='x'/>", "error": "<error message='x'/>"}.get(
            status, "<skipped/>"
        )
        inner += f"<testcase classname='hidden_tests.test_fix' name={quoteattr(name)}>{body}</testcase>"
    return f"<testsuites><testsuite>{inner}</testsuite></testsuites>"


class FakePytest:
    """A sandbox whose `run` does what pytest would do with the files in the workspace."""

    def __init__(
        self, workdir: Path, decide: Decide, calls: Calls, *, report: bool, coverage: Coverage | None
    ) -> None:
        self.workdir, self.decide, self.calls, self.report = workdir, decide, calls, report
        self.coverage = coverage

    def run(
        self, argv: list[str], *, timeout_s: float, cwd: str = "/work", on_output: object = None
    ) -> ExecResult:
        self.calls.append((argv, cwd))
        if argv[:3] == ["python", "-m", "coverage"]:
            return self._coverage(argv)
        source = (self.workdir / "repo" / "mod.py").read_text(encoding="utf-8")
        state = "FIXED" if "FIXED" in source else "BUGGY"
        cases = self.decide(state, len(self.calls))
        if self.report:
            # Where pytest was told to write the report: the validator and the grader name it differently.
            name = next(a for a in argv if a.startswith("--junitxml=")).removeprefix("--junitxml=/work/")
            (self.workdir / name).write_text(junit(cases), encoding="utf-8")
        failed = any(status in ("fail", "error") for _, status in cases)
        return ExecResult(int(failed), "", "", timed_out=False, duration_s=0.1, truncated=False)

    def _coverage(self, argv: list[str]) -> ExecResult:
        """`coverage run` runs nothing here; `coverage json` writes what the test asked it to say."""
        if argv[3] == "json" and self.coverage is not None:
            files = {name: {"summary": {}, **data} for name, data in self.coverage.items()}
            (self.workdir / COVERAGE_NAME).write_text(json.dumps({"files": files}), encoding="utf-8")
        return ExecResult(0, "", "", timed_out=False, duration_s=0.1, truncated=False)

    def close(self) -> None:
        return None


def validate_with(
    folder: Path,
    decide: Decide = good,
    *,
    report: bool = True,
    coverage: Coverage | None = GOOD_COVERAGE,
) -> tuple[list[str], Calls]:
    calls: Calls = []

    @contextmanager
    def open_sandbox(workdir: Path) -> Iterator[FakePytest]:
        yield FakePytest(workdir, decide, calls, report=report, coverage=coverage)

    result = validate(folder, open_sandbox)
    return list(result.problems), calls


@pytest.fixture
def task(tmp_path: Path) -> Path:
    folder = make_task(tmp_path, {"fail_to_pass": [F2P], "pass_to_pass": list(P2P)})
    (folder / "repo" / "mod.py").write_text('VALUE = "BUGGY"\n', encoding="utf-8")
    (folder / "reference.patch").write_text(PATCH, encoding="utf-8")
    return folder


# --- A task that proves what it claims ------------------------------------------------------------


def test_a_good_task_has_no_problems(task: Path) -> None:
    problems, _ = validate_with(task)
    assert problems == []


def test_the_tests_run_in_the_sandbox_with_the_ids_of_the_task_and_a_report_file(task: Path) -> None:
    _, calls = validate_with(task)
    argv, cwd = calls[0]
    assert cwd == "/work" and argv[:3] == ["python", "-m", "pytest"]
    assert F2P in argv and P2P[0] in argv and P2P[1] in argv
    assert f"--junitxml=/work/{REPORT_NAME}" in argv


def test_the_task_is_run_three_times_on_the_original_and_three_times_on_the_patched_code(task: Path) -> None:
    _, calls = validate_with(task)
    assert len([argv for argv, _ in calls if "pytest" in argv and "coverage" not in argv]) == 6


def test_validating_does_not_touch_the_task_folder(task: Path) -> None:
    before = {p.relative_to(task): p.read_bytes() for p in task.rglob("*") if p.is_file()}
    validate_with(task)
    after = {p.relative_to(task): p.read_bytes() for p in task.rglob("*") if p.is_file()}
    assert after == before


# --- Step 1: the hidden test must catch the bug ---------------------------------------------------


def test_a_fail_to_pass_test_that_already_passes_on_the_original_is_a_problem(task: Path) -> None:
    def always_green(state: str, run: int) -> list[Case]:
        return [("test_stacked", "pass"), ("test_single", "pass"), ("test_ordinary", "pass")]

    problems, _ = validate_with(task, always_green)
    assert any("test_stacked" in p and "does not fail on the original" in p for p in problems)


def test_a_pass_to_pass_test_that_fails_on_the_original_is_a_problem(task: Path) -> None:
    def broken(state: str, run: int) -> list[Case]:
        return [("test_stacked", "pass" if state == "FIXED" else "fail"), ("test_single", "fail"),
                ("test_ordinary", "pass")]  # fmt: skip

    problems, _ = validate_with(task, broken)
    assert any("test_single" in p and "original" in p for p in problems)


# --- Step 2: the reference patch must fix it and break nothing ------------------------------------


def test_a_patch_that_does_not_fix_the_bug_is_a_problem(task: Path) -> None:
    def never_fixed(state: str, run: int) -> list[Case]:
        return [("test_stacked", "fail"), ("test_single", "pass"), ("test_ordinary", "pass")]

    problems, _ = validate_with(task, never_fixed)
    assert any("test_stacked" in p and "does not pass with the reference patch" in p for p in problems)


def test_a_patch_that_breaks_a_pass_to_pass_test_is_a_problem(task: Path) -> None:
    def breaks(state: str, run: int) -> list[Case]:
        ordinary = "fail" if state == "FIXED" else "pass"
        return [("test_stacked", "pass" if state == "FIXED" else "fail"), ("test_single", "pass"),
                ("test_ordinary", ordinary)]  # fmt: skip

    problems, _ = validate_with(task, breaks)
    assert any("test_ordinary" in p and "reference patch" in p for p in problems)


def test_a_patch_that_does_not_apply_is_a_problem_and_nothing_is_run_on_it(task: Path) -> None:
    (task / "reference.patch").write_text(PATCH.replace('"BUGGY"', '"SOMETHING ELSE"'), encoding="utf-8")
    problems, _ = validate_with(task)
    assert any("reference.patch does not apply" in p for p in problems)


def test_a_task_without_a_reference_patch_is_a_problem(task: Path) -> None:
    (task / "reference.patch").unlink()
    problems, _ = validate_with(task)
    assert any("reference.patch" in p and "no " in p for p in problems)


# --- What counts as a pass ------------------------------------------------------------------------


def test_a_skipped_test_is_not_a_pass(task: Path) -> None:
    def skipped(state: str, run: int) -> list[Case]:
        stacked = "skipped" if state == "FIXED" else "fail"
        return [("test_stacked", stacked), ("test_single", "pass"), ("test_ordinary", "pass")]

    problems, _ = validate_with(task, skipped)
    assert any("test_stacked" in p and "skipped" in p for p in problems)


def test_a_test_that_did_not_run_at_all_is_named_and_is_not_a_pass(task: Path) -> None:
    def missing(state: str, run: int) -> list[Case]:
        return [("test_stacked", "pass" if state == "FIXED" else "fail"), ("test_single", "pass")]

    problems, _ = validate_with(task, missing)
    assert any("test_ordinary" in p and "did not run" in p for p in problems)


def test_a_test_with_parameters_passes_only_if_every_case_passes(task: Path) -> None:
    def one_case_fails(state: str, run: int) -> list[Case]:
        b = "pass" if state == "FIXED" else "fail"
        both = [("test_stacked[a]", "pass"), ("test_stacked[b]", b)]
        return [*both, ("test_single", "pass"), ("test_ordinary", "pass")]

    assert validate_with(task, one_case_fails)[0] == []

    def one_stays_red(state: str, run: int) -> list[Case]:
        return [("test_stacked[a]", "fail"), ("test_stacked[b]", "fail"), ("test_single", "pass"),
                ("test_ordinary", "pass")]  # fmt: skip

    assert validate_with(task, one_stays_red)[0] != []


def test_a_pytest_that_leaves_no_report_is_a_problem_and_not_a_pass(task: Path) -> None:
    problems, _ = validate_with(task, report=False)
    assert problems and any("no report" in p for p in problems)


# --- Step 4: the result must not change from one run to the next ----------------------------------


def test_a_test_that_changes_its_result_between_runs_is_flaky(task: Path) -> None:
    def flaky(state: str, run: int) -> list[Case]:
        ordinary = "fail" if run == 5 else "pass"  # the second run on the patched code
        return [("test_stacked", "pass" if state == "FIXED" else "fail"), ("test_single", "pass"),
                ("test_ordinary", ordinary)]  # fmt: skip

    problems, _ = validate_with(task, flaky)
    assert any("not stable" in p for p in problems)


# --- Step 3: the hidden tests must run what the patch changes --------------------------------------


def test_the_lines_a_patch_adds_are_numbered_as_in_the_patched_file() -> None:
    patch = (
        "--- a/a.py\n+++ b/a.py\n@@ -2,3 +2,4 @@\n keep\n-old\n+new one\n+new two\n keep\n"
        "@@ -10 +11 @@\n-x\n+y\n"
        "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-p\n+q\n"
    )
    assert changed_lines(patch) == {"a.py": {3, 4, 11}, "b.py": {1}}


def test_a_patch_that_only_deletes_lines_adds_none_and_a_deleted_file_is_left_out() -> None:
    patch = (
        "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1 @@\n keep\n-gone\n--- a/d.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
    )
    assert changed_lines(patch) == {}


def test_a_new_file_in_a_patch_counts_every_line_as_added() -> None:
    patch = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+one\n+two\n"
    assert changed_lines(patch) == {"new.py": {1, 2}}


def test_the_coverage_is_measured_on_the_patched_code_with_branches_in_the_sandbox(task: Path) -> None:
    _, calls = validate_with(task)
    run = next(argv for argv, _ in calls if argv[:4] == ["python", "-m", "coverage", "run"])
    assert "--branch" in run and "--source=/work/repo" in run and "-m" in run and "pytest" in run
    assert F2P in run
    assert any(argv[:4] == ["python", "-m", "coverage", "json"] for argv, _ in calls)


def test_a_line_the_patch_adds_and_no_hidden_test_runs_is_a_problem(task: Path) -> None:
    uncovered: Coverage = {
        "repo/mod.py": {"executed_lines": [], "missing_lines": [1], "missing_branches": []}
    }
    problems, _ = validate_with(task, coverage=uncovered)
    assert any("mod.py" in p and "line 1" in p and "never runs" in p for p in problems)


def test_a_line_that_cannot_run_such_as_a_comment_is_not_asked_to_be_covered(task: Path) -> None:
    comment_only: Coverage = {
        "repo/mod.py": {"executed_lines": [], "missing_lines": [], "missing_branches": []}
    }
    assert validate_with(task, coverage=comment_only)[0] == []


def test_a_branch_the_patch_adds_and_no_hidden_test_takes_is_a_problem(task: Path) -> None:
    one_way: Coverage = {
        "repo/mod.py": {"executed_lines": [1], "missing_lines": [], "missing_branches": [[1, 5]]}
    }
    problems, _ = validate_with(task, coverage=one_way)
    assert any("mod.py" in p and "branch" in p and "line 1" in p for p in problems)


def test_a_branch_that_starts_outside_the_patch_is_not_the_patch_s_business(task: Path) -> None:
    elsewhere: Coverage = {
        "repo/mod.py": {"executed_lines": [1], "missing_lines": [7], "missing_branches": [[7, 9]]}
    }
    assert validate_with(task, coverage=elsewhere)[0] == []


def test_a_changed_python_file_that_the_coverage_never_saw_is_a_problem(task: Path) -> None:
    problems, _ = validate_with(task, coverage={})
    assert any("mod.py" in p and "never imported" in p for p in problems)


def test_a_coverage_run_that_leaves_no_report_is_a_problem_and_not_a_pass(task: Path) -> None:
    problems, _ = validate_with(task, coverage=None)
    assert any("no coverage report" in p for p in problems)


def test_the_coverage_is_not_measured_for_a_task_that_already_has_problems(task: Path) -> None:
    def never_fixed(state: str, run: int) -> list[Case]:
        return [("test_stacked", "fail"), ("test_single", "pass"), ("test_ordinary", "pass")]

    _, calls = validate_with(task, never_fixed, coverage=None)
    assert not any("coverage" in argv for argv, _ in calls)


def test_a_file_that_is_not_python_is_not_asked_to_be_covered(task: Path) -> None:
    (task / "repo" / "README.md").write_text("docs\n", encoding="utf-8")
    docs = "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-docs\n+better docs\n"
    patch = task / "reference.patch"
    patch.write_text(patch.read_text(encoding="utf-8") + docs, encoding="utf-8")
    assert validate_with(task)[0] == []
