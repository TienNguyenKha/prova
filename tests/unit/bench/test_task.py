"""Tests for bench.task: task.yaml comes from a person, so it is loaded strictly and refused when wrong."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from bench.task import Task, TaskError, load_task

GOOD: dict[str, Any] = {
    "id": "t001_injection_regex",
    "benches": ["fix"],
    "split": "dev",
    "difficulty": "easy",
    "issue": "Some attempts to override the instructions are not caught.",
    "fail_to_pass": ["hidden_tests/test_fix.py::test_blocks_classic_jailbreak"],
    "pass_to_pass": ["hidden_tests/test_fix.py::test_allows_normal_question"],
    "localization": {"files": ["injection_guard.py"], "symbols": ["looks_like_injection"]},
    "forbidden_paths": ["hidden_tests/", "tests/"],
    "budget": {"max_tool_calls": 40, "max_usd": 0.5, "max_seconds": 600},
}


def make_task(root: Path, changes: dict[str, Any] | None = None, *, drop: tuple[str, ...] = ()) -> Path:
    """A task folder as the spec describes it, with the fields of GOOD changed or dropped."""
    folder = root / "t001_injection_regex"
    (folder / "repo").mkdir(parents=True)
    (folder / "hidden_tests").mkdir()
    data = {**GOOD, **(changes or {})}
    for key in drop:
        data.pop(key)
    (folder / "task.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return folder


# --- A good task ----------------------------------------------------------------------------------


def test_a_good_task_loads_with_every_field(tmp_path: Path) -> None:
    task = load_task(make_task(tmp_path))
    assert isinstance(task, Task)
    assert task.id == "t001_injection_regex" and task.split == "dev" and task.benches == ("fix",)
    assert task.fail_to_pass == ("hidden_tests/test_fix.py::test_blocks_classic_jailbreak",)
    assert task.localization.files == ("injection_guard.py",)
    assert task.budget.max_usd == 0.5 and task.budget.max_tool_calls == 40


def test_the_optional_fields_have_safe_defaults(tmp_path: Path) -> None:
    task = load_task(make_task(tmp_path, drop=("forbidden_paths",)))
    assert task.forbidden_tools == () and task.tags == () and task.notes == "" and task.source == ""
    assert task.forbidden_paths == ("hidden_tests/",)  # the one fence no task can be loaded without


def test_a_task_that_names_no_forbidden_paths_still_forbids_the_hidden_tests(tmp_path: Path) -> None:
    task = load_task(make_task(tmp_path, {"forbidden_paths": []}))
    assert "hidden_tests/" in task.forbidden_paths


# --- A wrong task is refused, and the error says where --------------------------------------------


@pytest.mark.parametrize("field", ["id", "benches", "split", "difficulty", "issue", "fail_to_pass", "budget"])
def test_a_missing_required_field_is_named(tmp_path: Path, field: str) -> None:
    with pytest.raises(TaskError, match=field):
        load_task(make_task(tmp_path, drop=(field,)))


def test_a_field_nobody_knows_is_refused_and_not_ignored(tmp_path: Path) -> None:
    with pytest.raises(TaskError, match="fail_to_pas"):
        load_task(make_task(tmp_path, {"fail_to_pas": ["hidden_tests/test_fix.py::x"]}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("split", "validation"),
        ("difficulty", "impossible"),
        ("benches", ["fix", "speed"]),
        ("benches", []),
        ("issue", "   "),
    ],
)
def test_a_value_outside_what_the_spec_allows_is_refused(tmp_path: Path, field: str, value: object) -> None:
    with pytest.raises(TaskError, match=field):
        load_task(make_task(tmp_path, {field: value}))


def test_the_id_must_be_the_name_of_the_folder(tmp_path: Path) -> None:
    with pytest.raises(TaskError, match="t999_other"):
        load_task(make_task(tmp_path, {"id": "t999_other"}))


@pytest.mark.parametrize(
    "bad",
    [
        "tests/test_fix.py::test_x",  # not in hidden_tests
        "hidden_tests/../repo/test_x.py::test_x",  # climbs out
        "/etc/passwd::test_x",  # absolute
        "hidden_tests/test_fix.py",  # no test named: a whole file would hide which test is meant
    ],
)
def test_a_test_id_must_name_one_test_inside_hidden_tests(tmp_path: Path, bad: str) -> None:
    with pytest.raises(TaskError, match="fail_to_pass"):
        load_task(make_task(tmp_path, {"fail_to_pass": [bad]}))


@pytest.mark.parametrize("field", ["fail_to_pass", "pass_to_pass"])
def test_both_lists_of_tests_need_at_least_one_test(tmp_path: Path, field: str) -> None:
    with pytest.raises(TaskError, match=field):
        load_task(make_task(tmp_path, {field: []}))


def test_a_test_cannot_be_in_both_lists(tmp_path: Path) -> None:
    same = "hidden_tests/test_fix.py::test_x"
    with pytest.raises(TaskError, match="both"):
        load_task(make_task(tmp_path, {"fail_to_pass": [same], "pass_to_pass": [same]}))


@pytest.mark.parametrize("key", ["max_tool_calls", "max_usd", "max_seconds"])
@pytest.mark.parametrize("value", [0, -1])
def test_a_budget_must_be_positive(tmp_path: Path, key: str, value: int) -> None:
    budget = {**GOOD["budget"], key: value}
    with pytest.raises(TaskError, match=key):
        load_task(make_task(tmp_path, {"budget": budget}))


@pytest.mark.parametrize("bad", ["/abs/file.py", "../outside.py", "a/../../b.py"])
def test_localization_files_are_relative_and_stay_inside_the_repo(tmp_path: Path, bad: str) -> None:
    with pytest.raises(TaskError, match="localization"):
        load_task(make_task(tmp_path, {"localization": {"files": [bad], "symbols": []}}))


# --- The folder must be a whole task --------------------------------------------------------------


def test_a_folder_without_task_yaml_is_refused(tmp_path: Path) -> None:
    folder = make_task(tmp_path)
    (folder / "task.yaml").unlink()
    with pytest.raises(TaskError, match=r"task\.yaml"):
        load_task(folder)


@pytest.mark.parametrize("missing", ["repo", "hidden_tests"])
def test_a_task_needs_its_repo_and_its_hidden_tests(tmp_path: Path, missing: str) -> None:
    folder = make_task(tmp_path)
    (folder / missing).rmdir()
    with pytest.raises(TaskError, match=missing):
        load_task(folder)


@pytest.mark.parametrize("text", ["- just\n- a list\n", "", "key: [unclosed\n"])
def test_a_task_yaml_that_is_not_a_mapping_is_refused(tmp_path: Path, text: str) -> None:
    folder = make_task(tmp_path)
    (folder / "task.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(TaskError, match=r"task\.yaml"):
        load_task(folder)
