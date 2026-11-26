"""The patches a reviewer is measured on (M14): good and bad, with the hidden tests of each task as the truth.

A `good` patch passes every hidden test of its task; a `bad` one fails at least one. If a "bad" patch passes
them all, it is not bad, whatever we meant it to be, and the case is wrong. These tests check the file and the
rule; tests/docker/test_review_cases.py checks every real case in the sandbox.
"""

from pathlib import Path

import pytest
import yaml

from bench.review_cases import CASES, Case, ReviewCaseError, judge, load_cases

TASKS = Path(__file__).resolve().parents[3] / "bench" / "tasks"


def write(tmp_path: Path, cases: list[dict[str, str]], patches: tuple[str, ...] = ("p.patch",)) -> Path:
    (tmp_path / "patches").mkdir(exist_ok=True)
    for name in patches:
        (tmp_path / "patches" / name).write_text("diff\n", encoding="utf-8")
    path = tmp_path / "cases.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "cases": cases}), encoding="utf-8")
    return path


GOOD = {
    "id": "t009-reference",
    "task": "t009_fail_open_review",
    "patch": "reference",
    "label": "good",
    "kind": "reference",
}
BAD = {
    "id": "t009-bad",
    "task": "t009_fail_open_review",
    "patch": "p.patch",
    "label": "bad",
    "kind": "partial",
}


# --- the real file ---------------------------------------------------------------------------------


def test_the_real_cases_load_and_every_task_has_one_good_patch_and_some_bad_ones() -> None:
    cases = load_cases(CASES, TASKS)
    goods = [c for c in cases if c.label == "good"]
    assert len({c.task for c in goods}) == len(goods)
    assert {c.task for c in cases if c.label == "bad"} <= {c.task for c in goods}
    assert len([c for c in cases if c.label == "bad"]) >= len(goods)


def test_a_reference_case_points_to_the_task_and_a_file_case_to_bench_review_patches() -> None:
    cases = {c.id: c for c in load_cases(CASES, TASKS)}
    assert cases["t009-reference"].patch_file == TASKS / "dev" / "t009_fail_open_review" / "reference.patch"
    assert cases["t009-symptom-only"].patch_file.parent.name == "patches"


# --- what the file may say ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"task": "t999_nothing"}, "t999_nothing"),
        ({"patch": "missing.patch"}, "missing.patch"),
        ({"label": "maybe"}, "label"),
        ({"surprise": "x"}, "surprise"),
    ],
)
def test_a_case_that_is_wrong_is_an_error_that_says_what(
    tmp_path: Path, change: dict[str, str], said: str
) -> None:
    path = write(tmp_path, [GOOD, {**BAD, **change}])
    with pytest.raises(ReviewCaseError, match=said):
        load_cases(path, TASKS)


def test_two_cases_with_one_id_are_an_error(tmp_path: Path) -> None:
    with pytest.raises(ReviewCaseError, match="t009-bad"):
        load_cases(write(tmp_path, [GOOD, BAD, BAD]), TASKS)


# --- the truth is the hidden tests --------------------------------------------------------------------


def case(label: str) -> Case:
    return Case(id="c", task="t", patch_file=Path("p"), label=label, kind="k", note="")  # type: ignore[arg-type]


def test_a_good_patch_must_pass_every_hidden_test() -> None:
    assert judge(case("good"), {"a": "pass", "b": "pass"}) is None
    problem = judge(case("good"), {"a": "pass", "b": "fail"})
    assert problem is not None and "b" in problem


def test_a_bad_patch_must_fail_at_least_one_hidden_test() -> None:
    assert judge(case("bad"), {"a": "pass", "b": "fail"}) is None
    problem = judge(case("bad"), {"a": "pass", "b": "pass"})
    assert problem is not None and "not bad" in problem


def test_a_hidden_test_that_did_not_run_is_never_a_pass_or_a_failure_that_counts() -> None:
    assert judge(case("bad"), {"a": "pass", "b": "missing"}) is not None  # missing is not "fails a test"
    assert judge(case("good"), {"a": "pass", "b": "skipped"}) is not None
