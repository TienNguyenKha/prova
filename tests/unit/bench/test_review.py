"""Measuring reviewers (M14): on good and bad patches, how many bad ones are blocked, how many good ones are
blocked by mistake, and at what cost; plus the warning for a reviewer that never blocks, and the list of the
cases where two reviewers disagree, which is what a person should look at.

A reviewer gets the patched code without the hidden tests (they are the answer) and the diff. Fake reviewers
stand in for the model; a small real task and real patches are applied with `git apply`.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from bench.review import (
    ReviewRunError,
    ceiling_usd,
    disagreements,
    main,
    review_case,
    rubber_stamps,
    summarize,
)
from bench.review_cases import Case
from bench.reviewers import Review
from bench.task import Task
from prova.agent.budget import Pricing
from prova.llm.usage import UsageLog, UsageRecord
from tests.unit.bench.test_task import make_task

PRICING = Pricing(input_per_mtok=2.0, output_per_mtok=10.0)
PATCH = """\
diff --git a/lib.py b/lib.py
--- a/lib.py
+++ b/lib.py
@@ -1 +1 @@
-STATE = "BUG"
+STATE = "FIXED"
"""


def a_task(tmp_path: Path) -> tuple[Path, Case]:
    folder = make_task(tmp_path / "tasks" / "dev")
    (folder / "repo" / "lib.py").write_text('STATE = "BUG"\n', encoding="utf-8")
    (folder / "hidden_tests" / "test_fix.py").write_text("def test_secret(): pass\n", encoding="utf-8")
    patch = tmp_path / "p.patch"
    patch.write_text(PATCH, encoding="utf-8")
    return tmp_path / "tasks", Case("c1", "t001_injection_regex", patch, "bad", "partial", "")


class Seen:
    """A reviewer that remembers what it was given and answers with a fixed verdict."""

    def __init__(self, verdict: str = "block", tokens: int = 10_000) -> None:
        self.verdict, self.tokens = verdict, tokens
        self.given: list[dict[str, Any]] = []

    def __call__(self, task: Task, diff: str, workspace: Path, trajectory: Path) -> Review:
        self.given.append(
            {
                "diff": diff,
                "state": (workspace / "lib.py").read_text(encoding="utf-8").strip(),
                "files": sorted(
                    p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if p.is_file()
                ),
                "trajectory": trajectory,
            }
        )
        log = UsageLog()
        log.add(UsageRecord(turn=1, input_tokens=self.tokens, output_tokens=0))
        return Review(self.verdict, "because", log)  # type: ignore[arg-type]


def row(
    reviewer: str, case: str, label: str, verdict: str, seed: int = 0, cost: float = 0.01
) -> dict[str, Any]:
    return {
        "reviewer": reviewer,
        "case": case,
        "label": label,
        "seed": seed,
        "verdict": verdict,
        "correct": (verdict == "block") == (label == "bad"),
        "cost_usd": cost,
        "seconds": 1.0,
    }


# --- one review ------------------------------------------------------------------------------------


def test_the_reviewer_gets_the_patched_code_and_the_diff_but_never_the_hidden_tests(tmp_path: Path) -> None:
    tasks, case = a_task(tmp_path)
    reviewer = Seen()
    review_case(
        case,
        "adversary",
        reviewer,
        tasks=tasks,
        results=tmp_path / "out" / "r.jsonl",
        run_id="r",
        seed=0,
        pricing=PRICING,
    )
    [given] = reviewer.given
    assert given["state"] == 'STATE = "FIXED"' and given["diff"] == PATCH
    assert not any(name.startswith("hidden_tests") for name in given["files"])
    assert given["trajectory"].name == "r-adversary-c1-s0.jsonl"


def test_one_line_is_written_for_each_review_with_whether_it_was_right(tmp_path: Path) -> None:
    tasks, case = a_task(tmp_path)
    results = tmp_path / "out" / "r.jsonl"
    written = review_case(
        case, "self", Seen("pass"), tasks=tasks, results=results, run_id="r", seed=2, pricing=PRICING
    )
    [line] = [json.loads(text) for text in results.read_text(encoding="utf-8").splitlines()]
    assert line == written
    assert (line["reviewer"], line["case"], line["label"], line["seed"]) == ("self", "c1", "bad", 2)
    assert line["verdict"] == "pass" and line["correct"] is False  # a bad patch let through
    assert line["cost_usd"] == pytest.approx(0.02) and line["reason"] == "because"


def test_a_reviewer_that_crashes_is_a_line_that_says_so_and_never_a_verdict(tmp_path: Path) -> None:
    tasks, case = a_task(tmp_path)

    def crash(task: Task, diff: str, workspace: Path, trajectory: Path) -> Review:
        raise RuntimeError("the API went away")

    line = review_case(
        case,
        "adversary",
        crash,
        tasks=tasks,
        results=tmp_path / "r.jsonl",
        run_id="r",
        seed=0,
        pricing=PRICING,
    )
    assert line["verdict"] is None and line["correct"] is None and "the API went away" in line["error"]


# --- the numbers -----------------------------------------------------------------------------------


def test_the_summary_gives_caught_false_blocks_and_cost_for_each_reviewer_with_its_sem() -> None:
    rows = [
        row("adversary", "b1", "bad", "block", seed=0), row("adversary", "b1", "bad", "pass", seed=1),
        row("adversary", "g1", "good", "pass", seed=0), row("adversary", "g1", "good", "block", seed=1),
        row("none", "b1", "bad", "pass", cost=0.0), row("none", "g1", "good", "pass", cost=0.0),
    ]  # fmt: skip
    summary = summarize(rows)
    adversary = summary["adversary"]
    assert adversary.caught == (1, 2) and adversary.false_blocks == (1, 2)
    assert adversary.caught_by_seed == [1.0, 0.0] and adversary.cost_per_review == pytest.approx(0.01)
    assert summary["none"].caught == (0, 1) and summary["none"].false_blocks == (0, 1)


def test_reviews_that_crashed_are_counted_apart_and_not_as_verdicts() -> None:
    crashed = {**row("self", "b1", "bad", "pass"), "verdict": None, "correct": None, "error": "boom"}
    summary = summarize([row("self", "b2", "bad", "block"), crashed])
    assert summary["self"].caught == (1, 1) and summary["self"].crashed == 1


def test_a_reviewer_that_never_blocked_in_its_last_reviews_is_named() -> None:
    rows = [row("self", f"c{i}", "bad", "pass", seed=0) for i in range(5)]
    rows += [
        row("adversary", "c0", "bad", "block"),
        *[row("adversary", f"c{i}", "good", "pass") for i in range(4)],
    ]
    assert rubber_stamps(rows, last=5) == ["self"]
    assert rubber_stamps(rows, last=10) == ["self"]  # fewer reviews than asked for: all of them count


def test_the_disagreements_of_two_reviewers_are_the_cases_and_seeds_where_they_differ() -> None:
    rows = [
        row("self", "b1", "bad", "pass"), row("adversary", "b1", "bad", "block"),
        row("self", "g1", "good", "pass"), row("adversary", "g1", "good", "pass"),
        row("self", "b2", "bad", "block"),  # the adversary did not review it: no disagreement to show
    ]  # fmt: skip
    assert disagreements(rows, "self", "adversary") == [("b1", 0, "pass", "block")]


# --- the command -------------------------------------------------------------------------------------


def test_the_ceiling_counts_the_adversary_at_the_money_limit_of_its_task_and_self_review_at_a_bound() -> None:
    assert ceiling_usd(cases=10, seeds=3, reviewers=["none"], task_limit=0.5) == 0.0
    assert ceiling_usd(cases=10, seeds=3, reviewers=["adversary"], task_limit=0.5) == pytest.approx(15.0)
    assert ceiling_usd(cases=10, seeds=3, reviewers=["self"], task_limit=0.5) == pytest.approx(10 * 3 * 0.05)


def test_without_confirm_it_shows_the_plan_and_spends_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    ran: list[object] = []
    code = main(["--reviewer", "self", "--seeds", "2"], execute=lambda *args: ran.append(args) or 0)
    out = capsys.readouterr().out
    assert code == 0 and ran == [] and "self" in out and "--confirm" in out and "$" in out


def test_an_unknown_reviewer_is_refused() -> None:
    with pytest.raises(SystemExit):
        main(["--reviewer", "jury"], execute=lambda *args: 0)


# --- the edges -------------------------------------------------------------------------------------


def test_a_good_patch_let_through_is_right_and_a_good_patch_blocked_is_wrong(tmp_path: Path) -> None:
    tasks, case = a_task(tmp_path)
    good = Case(case.id, case.task, case.patch_file, "good", "reference", "")
    out = tmp_path / "r.jsonl"
    assert review_case(
        good, "s", Seen("pass"), tasks=tasks, results=out, run_id="r", seed=0, pricing=PRICING
    )["correct"]
    assert not review_case(
        good, "s", Seen("block"), tasks=tasks, results=out, run_id="r", seed=1, pricing=PRICING
    )["correct"]


def test_a_patch_that_does_not_apply_stops_the_run_with_an_error(tmp_path: Path) -> None:
    tasks, case = a_task(tmp_path)
    (tasks / "dev" / "t001_injection_regex" / "repo" / "lib.py").write_text("other\n", encoding="utf-8")
    with pytest.raises(ReviewRunError, match="c1"):
        review_case(
            case, "s", Seen(), tasks=tasks, results=tmp_path / "r.jsonl", run_id="r", seed=0, pricing=PRICING
        )


def test_a_cost_that_cannot_be_known_is_none_and_the_summary_says_n_a(tmp_path: Path) -> None:
    tasks, case = a_task(tmp_path)

    def cached(task: Task, diff: str, workspace: Path, trajectory: Path) -> Review:
        log = UsageLog()
        log.add(UsageRecord(turn=1, input_tokens=10, output_tokens=1, cache_read_input_tokens=5))
        return Review("block", "x", log)

    line = review_case(
        case, "s", cached, tasks=tasks, results=tmp_path / "r.jsonl", run_id="r", seed=0, pricing=PRICING
    )
    assert line["cost_usd"] is None
    assert summarize([line, row("s", "b2", "bad", "block")])["s"].cost_per_review is None


def test_only_the_last_reviews_count_for_a_rubber_stamp_and_never_the_no_reviewer_baseline() -> None:
    rows = [
        row("adversary", "c0", "bad", "block"),
        *[row("adversary", f"c{i}", "good", "pass") for i in range(5)],
    ]
    rows += [row("none", f"c{i}", "bad", "pass") for i in range(20)]
    assert rubber_stamps(rows, last=5) == ["adversary"] and rubber_stamps(rows, last=6) == []


def test_a_reviewer_whose_reviews_all_crashed_is_not_a_rubber_stamp() -> None:
    crashed = {**row("self", "b1", "bad", "pass"), "verdict": None, "correct": None, "error": "boom"}
    assert rubber_stamps([crashed]) == []


def test_the_ceiling_of_an_adversary_review_is_the_money_limit_given() -> None:
    assert ceiling_usd(cases=2, seeds=1, reviewers=["adversary"], task_limit=0.25) == pytest.approx(0.5)
