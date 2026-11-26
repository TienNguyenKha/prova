"""The reproduction rate: do the tests a reproduction run wrote show the bug, and does the reference fix
make them pass? Measured offline, from the tests kept next to the trajectories, at no cost.

A test that fails on the bug for the right reason may still expect the wrong thing; only the second half
(green with `reference.patch`) shows that. A reproduce-first run that kept no test did not reproduce: it is
counted as a failure, not left out. Rows of other agents are not mixed in.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from bench.repro_rate import ReproRateError, measure, render
from prova.agent.repro import REPRO_ARGV
from prova.sandbox.base import ExecResult, Sandbox
from tests.unit.agent.test_repro import ASSERTION, IN_THE_TEST, SUMMARY_LINES, run
from tests.unit.bench.test_task import make_task

PATCH = """\
diff --git a/lib.py b/lib.py
--- a/lib.py
+++ b/lib.py
@@ -1 +1 @@
-STATE = "BUG"
+STATE = "FIXED"
"""
RED = ExecResult(1, run(ASSERTION, summary=[SUMMARY_LINES["assertion"]]), "", False, 0.1, False)
RED_WRONG = ExecResult(1, run(IN_THE_TEST, summary=[SUMMARY_LINES["typo"]]), "", False, 0.1, False)
GREEN = ExecResult(0, "1 passed in 0.01s\n", "", False, 0.1, False)


class Sandboxes:
    """Answers by what is in the workspace: the tests' verdict depends on whether lib.py has the bug."""

    def __init__(self, on_bug: ExecResult, on_fix: ExecResult) -> None:
        self.on_bug, self.on_fix = on_bug, on_fix
        self.seen: list[dict[str, Any]] = []

    @contextmanager
    def open(self, workspace: Path) -> Iterator[Sandbox]:
        outer = self

        class One:
            def run(
                self, argv: list[str], *, timeout_s: float, cwd: str = "/work", on_output: Any = None
            ) -> ExecResult:
                state = (workspace / "lib.py").read_text(encoding="utf-8")
                tests = sorted(p.name for p in (workspace / "repro").glob("*.py"))
                outer.seen.append({"argv": argv, "state": state.strip(), "tests": tests, "cwd": cwd})
                return outer.on_bug if "BUG" in state else outer.on_fix

            def close(self) -> None:
                pass

        yield One()


def a_task(root: Path) -> Path:
    folder = make_task(root / "tasks" / "dev")
    (folder / "repo" / "lib.py").write_text('STATE = "BUG"\n', encoding="utf-8")
    (folder / "reference.patch").write_text(PATCH, encoding="utf-8")
    return folder


def results(tmp_path: Path, *runs: tuple[int, bool], repro: bool = True) -> Path:
    """A results file with one line per (seed, kept tests), and the tests kept next to the trajectories."""
    out = tmp_path / "results" / "run.jsonl"
    (out.parent / "trajectories").mkdir(parents=True)
    lines = []
    for seed, kept in runs:
        name = f"run-t001_injection_regex-s{seed}"
        config: dict[str, Any] = {"model": "m", **({"repro": {"profile": "repro"}} if repro else {})}
        row = {
            "task_id": "t001_injection_regex",
            "seed": seed,
            "config": config,
            "trajectory": f"trajectories/{name}.jsonl",
        }
        lines.append(json.dumps(row))
        if kept:
            (out.parent / "trajectories" / f"{name}.repro").mkdir()
            (out.parent / "trajectories" / f"{name}.repro" / "test_bug.py").write_text(
                "x\n", encoding="utf-8"
            )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def test_tests_red_for_the_right_reason_on_the_bug_and_green_with_the_fix_reproduce_it(
    tmp_path: Path,
) -> None:
    a_task(tmp_path)
    boxes = Sandboxes(RED, GREEN)
    [one] = measure(results(tmp_path, (0, True)), tmp_path / "tasks", boxes.open)
    assert one.reproduces is True and one.red_on_bug == "assertion" and one.green_with_fix is True
    assert [s["state"] for s in boxes.seen] == ['STATE = "BUG"', 'STATE = "FIXED"']
    assert all(
        s["argv"] == REPRO_ARGV and s["tests"] == ["test_bug.py"] and s["cwd"] == "/work" for s in boxes.seen
    )


def test_tests_that_stay_red_with_the_reference_fix_expect_the_wrong_thing(tmp_path: Path) -> None:
    a_task(tmp_path)
    [one] = measure(results(tmp_path, (0, True)), tmp_path / "tasks", Sandboxes(RED, RED).open)
    assert one.reproduces is False and one.red_on_bug == "assertion" and one.green_with_fix is False


def test_tests_red_for_the_wrong_reason_do_not_reproduce_and_the_fix_is_not_tried(tmp_path: Path) -> None:
    a_task(tmp_path)
    boxes = Sandboxes(RED_WRONG, GREEN)
    [one] = measure(results(tmp_path, (0, True)), tmp_path / "tasks", boxes.open)
    assert one.reproduces is False and one.red_on_bug == "exception_in_test" and one.green_with_fix is None
    assert len(boxes.seen) == 1


def test_a_run_that_kept_no_test_did_not_reproduce(tmp_path: Path) -> None:
    a_task(tmp_path)
    boxes = Sandboxes(RED, GREEN)
    [one] = measure(results(tmp_path, (0, False)), tmp_path / "tasks", boxes.open)
    assert one.reproduces is False and one.red_on_bug is None and boxes.seen == []
    assert "no test" in one.note


def test_rows_of_another_agent_are_refused(tmp_path: Path) -> None:
    a_task(tmp_path)
    with pytest.raises(ReproRateError, match="not a reproduce-first run"):
        measure(results(tmp_path, (0, True), repro=False), tmp_path / "tasks", Sandboxes(RED, GREEN).open)


def test_a_reference_patch_that_does_not_apply_is_an_error(tmp_path: Path) -> None:
    task = a_task(tmp_path)
    (task / "repo" / "lib.py").write_text("something else\n", encoding="utf-8")
    with pytest.raises(ReproRateError, match="reference.patch"):
        measure(results(tmp_path, (0, True)), tmp_path / "tasks", Sandboxes(RED, GREEN).open)


def test_a_task_that_is_not_in_the_tasks_folder_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "tasks").mkdir()
    with pytest.raises(ReproRateError, match="t001_injection_regex"):
        measure(results(tmp_path, (0, True)), tmp_path / "tasks", Sandboxes(RED, GREEN).open)


def test_the_report_gives_each_task_and_the_whole_rate(tmp_path: Path) -> None:
    a_task(tmp_path)
    rows = measure(results(tmp_path, (0, True), (1, False)), tmp_path / "tasks", Sandboxes(RED, GREEN).open)
    text = render(rows)
    assert "t001_injection_regex" in text and "1/2" in text
    assert "reproduction rate: 1/2" in text


def test_output_that_was_cut_or_tests_that_did_not_finish_reproduce_nothing(tmp_path: Path) -> None:
    a_task(tmp_path)
    cut = ExecResult(1, RED.stdout, "", False, 0.1, True)
    late = ExecResult(-1, "", "", True, 120.0, False)
    for name, on_bug in (("cut", cut), ("late", late)):
        (tmp_path / name).mkdir()
        a_task(tmp_path / name)
        [one] = measure(
            results(tmp_path / name, (0, True)), tmp_path / name / "tasks", Sandboxes(on_bug, GREEN).open
        )
        assert one.reproduces is False and one.red_on_bug == "unreadable" and one.green_with_fix is None
        assert ("did not finish" in one.note) is (name == "late")


def test_tests_that_do_not_finish_with_the_fix_are_not_green(tmp_path: Path) -> None:
    a_task(tmp_path)
    late_but_zero = ExecResult(0, "", "", True, 120.0, False)
    [one] = measure(results(tmp_path, (0, True)), tmp_path / "tasks", Sandboxes(RED, late_but_zero).open)
    assert one.reproduces is False and one.green_with_fix is False and "did not finish" in one.note
